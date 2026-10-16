import asyncio
import pytest
from app.agents import strip_inline_ids
from app.repository import Repository, Conflict
from app.db import Database
from conftest import execute

QUERY = '我想减脂，预算每节课300元以内，南山区，工作日晚上，膝盖不舒服，希望教练耐心'


def test_strip_inline_ids_removes_hash_leaks():
    hid = 'b39fa1848b8c67239af27e1947e2e9fee06cf55d84468d4d4ebf2b67dbc0af14'
    assert strip_inline_ids(f'每周两次私教训练。[{hid}]') == '每周两次私教训练。'
    assert strip_inline_ids(f'内容（{hid}）后续') == '内容后续'
    assert strip_inline_ids(f'裸串 {hid} 结尾') == '裸串结尾'
    # 正常短数字/文字不受影响。
    assert strip_inline_ids('第 1 周：了解基础动作；每周 2 次') == '第 1 周：了解基础动作；每周 2 次'


async def test_recommendation_hard_constraints_evidence_and_delegation(components):
    _, repo, _, _, _, _ = components
    run = await execute(components, QUERY)
    assert run['status'] == 'completed', run['error']
    rows = run['result']['candidates']
    assert {c['id'] for c in rows} == {'coach-01', 'coach-02'}
    for c in rows:
        assert c['price'] <= 300 and '南山区' in c['districts']
        assert all(c['hard_matches'].values())
        for reason in c['reasons']:
            parent = repo.parent(reason['evidence_id'])
            assert parent['coach_id'] == c['id'] and reason['quote'] in parent['text']
        assert any('医疗' in text for text in c['pending'])
    events = repo.events(run['id'])
    assert {e['data'].get('agent') for e in events if e['kind'] == 'delegation'} == {'candidate', 'retrieval', 'evaluation'}
    assert any(e['kind'] == 'recall' and e['data']['channels'][0]['bm25'] > 0 for e in events)
    assert any(e['kind'] == 'rerank' and e['data']['parents'] > 0 for e in events)


async def test_budget_only_preserves_all_other_conditions(components):
    first = await execute(components, QUERY)
    second = await execute(components, '预算改成400，其他条件不变', first['session_id'])
    assert second['status'] == 'completed', second['error']
    previous, current = first['requirements'], second['requirements']
    assert current['budget_max'] == 400
    for key in ['district', 'available_windows', 'goal', 'self_reported_conditions', 'preferences']:
        assert current[key] == previous[key]
    assert current['version'] == previous['version'] + 1
    assert len(second['result']['candidates']) > len(first['result']['candidates'])


async def test_direct_knowledge_and_platform_and_mixed_routes(components):
    _, repo, _, _, _, _ = components
    direct = await execute(components, '什么是渐进超负荷？')
    assert direct['status'] == 'completed', direct['error']
    assert direct['result']['knowledge']['source_type'] == 'model_knowledge'
    assert not any(e['kind'] == 'recall' for e in repo.events(direct['id']))
    platform = await execute(components, '平台减脂课程怎么安排？')
    assert platform['status'] == 'completed', platform['error']
    assert platform['result']['knowledge']['citations']
    assert all(e['coach_id'] is None for e in platform['result']['knowledge']['evidence'])
    # 引文 id 只应出现在 citations，绝不能混进正文。
    answer = platform['result']['knowledge']['answer']
    assert not any(cid in answer for cid in platform['result']['knowledge']['citations'])
    mixed = await execute(components, '减脂怎么练，顺便推荐教练，南山区，预算300，工作日晚上')
    assert mixed['status'] == 'completed', mixed['error']
    assert mixed['result']['candidates'] and mixed['result']['knowledge']


async def test_pause_resume_survives_new_repository_and_idempotence(components):
    settings, repo, _, _, _, agent = components
    run = await execute(components, '帮我推荐教练')
    assert run['status'] == 'waiting_for_user'
    request_id = run['clarification']['id']
    reopened = Repository(Database(settings.database_url))
    reopened.recover_interrupted()
    assert reopened.run(run['id'])['clarification']['id'] == request_id
    resumed, claimed = reopened.reply(request_id, '减脂，南山区', {})
    assert claimed and resumed['id'] == run['id']
    duplicate, claimed_again = reopened.reply(request_id, '减脂，南山区', {})
    assert not claimed_again
    with pytest.raises(Conflict):
        reopened.reply(request_id, '增肌，福田区', {})
    await agent.execute(run['id'])
    completed = repo.run(run['id'])
    assert completed['status'] == 'completed', completed['error']
    assert completed['requirements']['goal'] == '减脂'


async def test_empty_never_auto_relaxes_and_explicit_option_resumes(components):
    _, repo, _, _, _, agent = components
    run = await execute(components, '找减脂教练，南山区，预算100，工作日晚上')
    assert run['status'] == 'waiting_for_user'
    assert run['requirements']['budget_max'] == 100
    assert run['clarification']['reason'] == 'empty'
    assert '仅放宽预算后有' in run['clarification']['question']
    repo.reply(run['clarification']['id'], '不限预算', {})
    await agent.execute(run['id'])
    final = repo.run(run['id'])
    assert final['status'] == 'completed', final['error']
    assert final['requirements']['budget_max'] is None
    assert final['requirements']['district'] == '南山区'


async def test_superseded_run_cannot_write_old_result_or_reply(components):
    _, repo, _, _, _, _ = components
    old = await execute(components, '帮我推荐教练')
    new = repo.create_run(old['session_id'], '什么是渐进超负荷？')
    assert repo.run(old['id'])['status'] == 'cancelled'
    with pytest.raises(Conflict):
        repo.reply(old['clarification']['id'], '减脂，南山区', {})
    with pytest.raises(Conflict):
        repo.save_state(old['id'], status='completed', result={'summary': '旧结果'})
    assert repo.session(old['session_id'])['active_run_id'] == new['id']


async def test_comparison_resolves_previous_candidates(components):
    first = await execute(components, QUERY)
    second = await execute(components, '比较前两位教练', first['session_id'])
    assert second['status'] == 'completed', second['error']
    assert {c['id'] for c in second['result']['candidates']} == {'coach-01', 'coach-02'}


async def test_missing_evidence_cannot_imply_experience(components):
    run = await execute(components, '贺南风多少钱，擅长什么？')
    assert run['status'] == 'completed', run['error']
    assert not run['result']['candidates'][0]['reasons']
    assert any('资料' in text for text in run['result']['candidates'][0]['pending'])


async def test_provider_failure_is_visible_and_no_fake_result(components, monkeypatch):
    from app.providers import ProviderError
    _, _, providers, _, _, _ = components
    async def broken(*args):
        raise ProviderError('百炼 Reranker 返回 HTTP 503')
    monkeypatch.setattr(providers, 'rerank', broken)
    run = await execute(components, QUERY)
    assert run['status'] == 'failed'
    assert '503' in run['error'] and run['result'] is None
