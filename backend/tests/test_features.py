"""收藏、预登记子 Agent、4 周表、跨会话画像与决策报告的测试。"""
import asyncio
from datetime import date, timedelta
from pathlib import Path
import httpx
import pytest
from app.auth import AuthService
from app.config import Settings
from app.db import Base
from app.ingestion import import_sql, load_dataset
from app.main import create_app
from app.repository import Repository, ValidationFailed

DATASET = Path(__file__).resolve().parents[2] / 'data/demo/dataset.json'


def make_user(repo, username='tester'):
    user, _ = AuthService(repo.db).register(username, 'password123')
    return user


async def run_for_user(components, user, text, session_id=None):
    _, repo, _, _, _, agent = components
    session_id = session_id or repo.create_session(user['id'])['id']
    run = repo.create_run(session_id, text, user_id=user['id'])
    await agent.execute(run['id'])
    return repo.run(run['id'])


async def test_preregistration_agent_registers_and_persists(components):
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    run = await run_for_user(components, user, '帮我给林知夏预登记周三晚上')
    assert run['status'] == 'completed', run['error']
    record = run['result']['preregistration']
    assert record and record['coach']['id'] == 'coach-01'
    assert record['status_label'] == '已登记 · 待教练确认'
    # 落库并可读回，4 周表里对应日格子带上登记。
    listed = repo.list_preregistrations(user['id'], 'coach-01')
    assert len(listed) == 1 and listed[0]['slots'] == record['slots']
    schedule = repo.coach_schedule('coach-01', user['id'])
    assert len(schedule['days']) == 28
    marked = [d for d in schedule['days'] if d['registrations']]
    assert marked and marked[0]['registrations'][0]['status_label'] == '已登记 · 待教练确认'


async def test_preregistration_without_coach_pauses(components):
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    run = await run_for_user(components, user, '帮我预登记周三晚上')
    assert run['status'] == 'waiting_for_user'
    assert '哪位教练' in run['clarification']['question']


async def test_preregistration_without_time_pauses(components):
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    run = await run_for_user(components, user, '给林知夏预登记')
    assert run['status'] == 'waiting_for_user'
    assert '具体时间' in run['clarification']['question']


async def test_preregistration_unbookable_weekday_pauses_without_timeout(components):
    """周二不在林知夏可约时段内：应暂停并给出可约选项，而不是重试到超时失败。"""
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    run = await run_for_user(components, user, '帮我给林知夏预登记周二晚上')
    assert run['status'] == 'waiting_for_user', run.get('error')
    question = run['clarification']['question']
    assert '无法预登记' in question
    assert set(run['clarification']['options']) & {'周一', '周三', '周五'}
    assert repo.list_preregistrations(user['id'], 'coach-01') == []


def test_slot_validation_rejects_past_out_of_range_and_wrong_window(components):
    _, repo, _, _, _, _ = components
    coach = repo.coach('coach-01')
    today = date(2026, 10, 15)
    with pytest.raises(ValidationFailed):
        repo.create_preregistration('u1', 'coach-01', [{'date':'2020-01-01','start':18,'end':19}], today=today)
    # 周三(0,2,4) 18-22：23 点越界、10 点不在开放时段
    with pytest.raises(ValidationFailed):
        repo.create_preregistration('u1', 'coach-01', [{'date':'2026-10-21','start':18,'end':23}], today=today)
    with pytest.raises(ValidationFailed):
        repo.create_preregistration('u1', 'coach-01', [{'date':'2026-10-21','start':10,'end':12}], today=today)


async def test_preregistration_without_coach_id_errors_instead_of_crashing(components):
    """delegate_preregistration 始终注册；未确定教练时被误调必须返回可修正错误，不能越界崩溃。"""
    _, _, _, _, _, orchestrator = components
    with pytest.raises(ValueError, match='明确一位教练'):
        await orchestrator.preregistration_agent('给教练预登记周三晚上', {'coach_ids': [], 'user_id': 'u1'}, lambda *a: None)
    with pytest.raises(ValueError, match='明确一位教练'):
        await orchestrator.preregistration_agent('给教练预登记周三晚上',
            {'coach_ids': ['coach-01', 'coach-02'], 'user_id': 'u1'}, lambda *a: None)


def test_sanitize_note_drops_model_prose_and_internal_ids(components):
    """备注字段不得泄漏模型解释或内部标识；只保留用户自己的一句话补充。"""
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    leaked = ('用户 a84f0fe2ab4d45d6a656df7ed8ce3da8（session 53b0e2ced2a441369cf376b6a46fc363）'
              '仅提出“周一”可用，未指定具体日期与整点时段。')
    record = repo.create_preregistration(user['id'], 'coach-01',
        [{'date': '2026-10-21', 'start': 19, 'end': 20}], leaked, today=date(2026, 10, 15))
    assert record['note'] == ''
    assert 'session' not in record['note'] and 'a84f0fe2' not in record['note']
    kept = repo.create_preregistration(user['id'], 'coach-01',
        [{'date': '2026-10-21', 'start': 20, 'end': 21}], '想先确认膝盖不适能否安排低冲击训练',
        today=date(2026, 10, 15))
    assert kept['note'] == '想先确认膝盖不适能否安排低冲击训练'


async def test_candidate_truncation_is_disclosed_not_silent(components):
    """候选超过上限时必须如实告知，不能静默丢弃符合硬条件的教练。"""
    settings, repo, _, _, _, _ = components
    settings.max_candidates = 2
    user = make_user(repo)
    run = await run_for_user(components, user, '找减脂教练，不限区域，不限时间')
    assert run['status'] == 'completed', run.get('error')
    result = run['result']
    assert len(result['candidates']) == 2
    assert result['candidate_total'] > 2 and result['candidate_truncated'] == result['candidate_total'] - 2
    assert any('未参与比较' in text for text in result['degraded']), result['degraded']


async def test_preregistration_delete_and_clear(components):
    """单条删除与清空全部预登记；只能删自己的。"""
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    other = make_user(repo, 'other')
    today = date(2026, 10, 15)
    first = repo.create_preregistration(user['id'], 'coach-01', [{'date':'2026-10-21','start':19,'end':20}], today=today)
    second = repo.create_preregistration(user['id'], 'coach-01', [{'date':'2026-10-21','start':20,'end':21}], today=today)
    # 单条删除
    assert repo.delete_preregistration(user['id'], first['id'])['deleted'] == first['id']
    assert [r['id'] for r in repo.list_preregistrations(user['id'])] == [second['id']]
    # 越权删除按不存在处理
    with pytest.raises(Exception):
        repo.delete_preregistration(other['id'], second['id'])
    assert len(repo.list_preregistrations(user['id'])) == 1
    # 清空全部
    assert repo.delete_preregistrations(user['id'])['deleted'] == 1
    assert repo.list_preregistrations(user['id']) == []


async def test_profile_reset_stops_accumulating_history_but_keeps_sessions(components):
    """清空画像后只累积新会话；聊天记录保留，画像不会自动长回旧数据。"""
    settings, repo, _, _, _, _ = components
    user = make_user(repo)
    await run_for_user(components, user, '我想减脂，预算300，南山区，工作日晚上')
    before = repo.user_profile(user['id'])
    assert before['goals'] and before['sessions'] >= 1 and before['reset_at'] is None
    reset = repo.reset_profile(user['id'])
    assert reset['reset_at'] and reset['goals'] == [] and reset['sessions'] == 0
    # 再次读取仍是已重置状态（不会从全部历史重新算回来）
    after = repo.user_profile(user['id'])
    assert after['goals'] == [] and after['sessions'] == 0 and after['reset_at']
    # 聊天记录保留
    assert len(repo.list_sessions(user['id'])) >= 1
    # 新会话重新累积
    await run_for_user(components, user, '我想增肌，预算400，福田区，工作日晚上')
    grown = repo.refresh_profile(user['id'])
    assert [g['value'] for g in grown['goals']] == ['增肌'] and grown['sessions'] == 1



def test_user_facing_error_hides_internal_details(components):
    """失败文案要面向用户：不含异常类名、英文库消息或内部角色名。"""
    from app.agents import Orchestrator
    from app.providers import ProviderError
    _, repo, _, _, _, orchestrator = components
    # 供应商的面向用户消息原样保留（含状态码）。
    assert '503' in orchestrator.user_facing_error(ProviderError('百炼 Reranker 返回 HTTP 503；请检查配置、额度与模型权限'))
    # 我们自己的中文校验提示保留。
    assert '不在未来 4 周' in orchestrator.user_facing_error(ValidationFailed('2020-01-01 不在未来 4 周的可预登记范围内'))
    # 超时与意外异常不得漏出类名。
    for error in (TimeoutError('simulated'), asyncio.TimeoutError(),
                  ValueError("(sqlalchemy) unknown column 'x'"), KeyError('trace')):
        text = orchestrator.user_facing_error(error)
        assert 'Error' not in text and 'KeyError' not in text and 'sqlalchemy' not in text, text
        assert text.strip()
    assert '超时' in orchestrator.user_facing_error(TimeoutError('simulated'))


def test_max_steps_error_does_not_leak_agent_role(components):
    _, repo, _, _, _, orchestrator = components
    text = orchestrator.user_facing_error(RuntimeError('这次处理步骤超过上限，没能完成；请把需求拆成几步再问'))
    assert 'retrieval' not in text and 'candidate' not in text



async def test_profile_aggregates_across_sessions(components):
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    await run_for_user(components, user, '我想减脂，预算300，南山区，工作日晚上，希望耐心')
    await run_for_user(components, user, '帮我给林知夏预登记周三晚上')
    repo.add_favorite(user['id'], 'coach-01')
    profile = repo.refresh_profile(user['id'])
    assert profile['goals'][0]['value'] == '减脂'
    assert profile['districts'][0]['value'] == '南山区'
    assert profile['budget'] == {'min': 300, 'max': 300}
    assert any(p['value'] == '耐心' for p in profile['preferences'])
    assert profile['favorite_coaches'] == [{'id': 'coach-01', 'name': '林知夏'}]
    assert profile['preregistered_coaches'] == [{'id': 'coach-01', 'name': '林知夏'}]


async def test_favorites_are_idempotent(components):
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    repo.add_favorite(user['id'], 'coach-01')
    repo.add_favorite(user['id'], 'coach-01')
    assert len(repo.list_favorites(user['id'])) == 1
    repo.remove_favorite(user['id'], 'coach-01')
    assert repo.list_favorites(user['id']) == []


async def test_report_contains_evidence_and_pending(components):
    _, repo, _, _, _, _ = components
    user = make_user(repo)
    run = await run_for_user(components, user, '我想减脂，预算每节课300元以内，南山区，工作日晚上，膝盖不舒服，希望教练耐心')
    report = repo.run_report(run['id'], user['id'])
    assert report['username'] == 'tester'
    assert report['requirements']['goal'] == '减脂'
    assert report['candidates']
    for candidate in report['candidates']:
        assert candidate['score_label'] and candidate['hard_matches']
        for reason in candidate['reasons']:
            assert reason['quote'] and reason['source_title']
    assert report['evidence'] and report['generated_at']
    # 归属校验：他人读取一律 404。
    with pytest.raises(Exception):
        repo.run_report(run['id'], 'someone-else')
    # 无结果的运行不能导出。
    with pytest.raises(Exception):
        repo.run_report(repo.create_run(repo.create_session(user['id'])['id'], '在吗', user_id=user['id'])['id'],
                        user['id'])


async def test_me_endpoints_and_cross_account_isolation(tmp_path):
    app = create_app(Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "feat.db"}',
                              redis_url='redis://127.0.0.1:1/0'))
    repo = app.state.repo
    Base.metadata.create_all(repo.db.engine)
    import_sql(repo, load_dataset(DATASET))
    from conftest import MemoryCache
    app.state.orchestrator.cache = app.state.retrieval.cache = MemoryCache()
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app)
        async with httpx.AsyncClient(transport=transport, base_url='http://test') as a, \
                   httpx.AsyncClient(transport=transport, base_url='http://test') as b:
            assert (await a.post('/api/auth/register', json={'username': 'alice', 'password': 'password123'})).status_code == 201
            assert (await b.post('/api/auth/register', json={'username': 'bob', 'password': 'password123'})).status_code == 201
            # 收藏
            assert (await a.post('/api/me/favorites', json={'coach_id': 'coach-01'})).status_code == 201
            favorites = (await a.get('/api/me/favorites')).json()
            assert favorites[0]['coach']['name'] == '林知夏'
            assert (await b.get('/api/me/favorites')).json() == []
            assert (await a.delete('/api/me/favorites/coach-01')).json()['favorite'] is False
            # 预登记：用 4 周表挑一个可约日
            schedule = (await a.get('/api/coaches/coach-01/schedule')).json()
            day = next(d for d in schedule['days'] if d['is_available'])
            slot = {'date': day['date'], 'start': day['windows'][0]['start'],
                    'end': day['windows'][0]['start'] + 1}
            created = await a.post('/api/me/preregistrations', json={'coach_id': 'coach-01', 'slots': [slot]})
            assert created.status_code == 201, created.text
            assert created.json()['status_label'] == '已登记 · 待教练确认'
            assert (await b.get('/api/me/preregistrations')).json() == []
            # 非法时间 400
            bad = await a.post('/api/me/preregistrations',
                               json={'coach_id': 'coach-01', 'slots': [{'date': '2020-01-01', 'start': 9, 'end': 10}]})
            assert bad.status_code == 400
            # 画像
            profile = (await a.get('/api/me/profile')).json()
            assert profile['preregistered_coaches'] == [{'id': 'coach-01', 'name': '林知夏'}]
            assert (await b.get('/api/me/profile')).json()['preregistered_coaches'] == []
            # 报告越权 404
            session = (await a.post('/api/sessions')).json()
            sent = await a.post(f'/api/sessions/{session["id"]}/messages',
                                json={'content': '推荐减脂教练，预算300，南山区，工作日晚上'})
            run_id = sent.json()['id']
            for _ in range(300):
                run = (await a.get('/api/runs/' + run_id)).json()
                if run['status'] not in ('created', 'running'):
                    break
                await asyncio.sleep(.02)
            assert (await b.get(f'/api/runs/{run_id}/report')).status_code == 404
            report = await a.get(f'/api/runs/{run_id}/report')
            assert report.status_code == 200 and report.json()['run_id'] == run_id
