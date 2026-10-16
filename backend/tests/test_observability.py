"""日志与运行轨迹：JSON 输出、request-id 贯穿、每次 run 一条 trace。"""
import asyncio
import json
import logging
from pathlib import Path

import httpx
import pytest

from app.config import Settings
from app.db import Base
from app.ingestion import import_sql, load_dataset
from app.main import create_app
from app.observability import JSONFormatter, Trace, configure_logging, make_formatter


def test_json_formatter_emits_single_line_with_context():
    from app.observability import request_id_var, run_id_var
    logger = logging.getLogger('observability.test')
    record = logging.getLogger('observability.test').makeRecord(
        'observability.test', logging.INFO, __file__, 1, '运行轨迹', None, None,
        extra={'event': 'run.trace', 'total_ms': 12})
    token_r, token_run = request_id_var.set('req-1'), run_id_var.set('run-1')
    try:
        line = make_formatter('json').format(record)
    finally:
        request_id_var.reset(token_r)
        run_id_var.reset(token_run)
    assert '\n' not in line
    payload = json.loads(line)
    assert payload['request_id'] == 'req-1' and payload['run_id'] == 'run-1'
    assert payload['event'] == 'run.trace' and payload['total_ms'] == 12
    assert payload['level'] == 'INFO' and payload['msg'] == '运行轨迹'


def test_json_formatter_does_not_fail_on_unserializable_extra():
    record = logging.getLogger('observability.test').makeRecord(
        'observability.test', logging.ERROR, __file__, 1, 'x', None, None, None,
        {'obj': object()})
    assert json.loads(make_formatter('json').format(record))['obj'].startswith('<')


def test_trace_records_stages_tools_providers_and_tokens():
    trace = Trace('run-1', session_id='s-1', mode='live')
    with trace.stage('route'):
        pass
    with pytest.raises(ValueError):
        with trace.stage('delegate:candidate'):
            raise ValueError('失败阶段')
    trace.add_tool('candidate', 'search_coaches', 41)
    trace.add_provider('对话模型', 500, retries=1)
    trace.add_tokens('chat', prompt=100, completion=20, total=120)
    data = trace.finish('completed')
    assert [s['name'] for s in data['stages']] == ['route', 'delegate:candidate']
    assert data['stages'][1]['ok'] is False
    assert data['tools'] == [{'agent': 'candidate', 'tool': 'search_coaches', 'ms': 41, 'ok': True}]
    assert data['providers']['对话模型']['retries'] == 1
    assert data['tokens']['chat'] == {'calls': 1, 'total': 120, 'prompt': 100, 'completion': 20}
    assert data['status'] == 'completed' and isinstance(data['total_ms'], int)


def test_trace_resumes_same_run_without_losing_history():
    first = Trace('run-1', session_id='s-1', request_id='req-1')
    with first.stage('route'):
        pass
    first.add_tokens('chat', total=10)
    first.finish('waiting_for_user')
    resumed = Trace('run-1', session_id='s-1', request_id='req-2', prior=first.to_dict())
    with resumed.stage('delegate:candidate'):
        pass
    data = resumed.finish('completed')
    # 恢复后同一 run 只有一条轨迹，历史阶段与 token 保留，耗时累计。
    assert [s['name'] for s in data['stages']] == ['route', 'delegate:candidate']
    assert data['tokens']['chat']['total'] == 10
    assert data['request_ids'] == ['req-1', 'req-2'] and data['request_id'] == 'req-1'
    assert data['resumes'] == 1
    assert data['total_ms'] >= first.to_dict()['total_ms']


async def _run_demo(tmp_path, text='帮我推荐教练'):
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "obs.db"}',
                        app_mode='demo', log_level='INFO', log_format='json')
    app = create_app(settings)
    repo = app.state.repo
    Base.metadata.create_all(repo.db.engine)
    import_sql(repo, load_dataset(Path(__file__).resolve().parents[2] / 'data/demo/dataset.json'))
    from conftest import MemoryCache
    app.state.orchestrator.cache = MemoryCache()
    app.state.retrieval.cache = app.state.orchestrator.cache
    return app


async def test_request_id_is_echoed_and_access_logged(tmp_path):
    app = await _run_demo(tmp_path)
    lines = []
    handler = logging.Handler()
    handler.setFormatter(JSONFormatter())
    handler.emit = lambda record: lines.append(json.loads(handler.format(record)))
    access_logger = logging.getLogger('app.http')
    access_logger.addHandler(handler)
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
                response = await client.get('/api/health', headers={'X-Request-ID': 'req-abc'})
                assert response.headers['x-request-id'] == 'req-abc'
                generated = await client.get('/api/health')
                assert generated.headers['x-request-id']
    finally:
        access_logger.removeHandler(handler)
    access = [row for row in lines if row['event'] == 'http.request']
    assert access and access[0]['request_id'] == 'req-abc'
    assert access[0]['path'] == '/api/health' and access[0]['status'] == 200
    assert isinstance(access[0]['duration_ms'], int)
    # 未带请求头时服务端生成 request-id，并回显给客户端做关联。
    assert len(access) == 2 and access[1]['request_id'] != 'req-abc'


async def test_run_trace_endpoint_exposes_trace_but_not_run_view(tmp_path):
    app = await _run_demo(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            await client.post('/api/auth/register', json={'username': 'tester', 'password': 'password123'})
            session = (await client.post('/api/sessions')).json()
            sent = await client.post(f'/api/sessions/{session["id"]}/messages',
                                     json={'content': '帮我推荐减脂教练，预算300元以内，南山区，工作日晚上'})
            run_id = sent.json()['id']
            for _ in range(200):
                if (await client.get(f'/api/runs/{run_id}')).json()['status'] == 'completed':
                    break
                await asyncio.sleep(.01)
            run = (await client.get(f'/api/runs/{run_id}')).json()
            assert run['status'] == 'completed'
            assert 'trace' not in run
            view = (await client.get(f'/api/sessions/{session["id"]}')).json()
            assert all('trace' not in r for r in view['runs'])

            trace = (await client.get(f'/api/runs/{run_id}/trace')).json()
            assert trace['run_id'] == run_id and trace['status'] == 'completed'
            assert trace['mode'] == 'demo' and trace['session_id'] == session['id']
            assert any(s['name'] == 'route' for s in trace['stages'])
            assert any(t['tool'] == 'search_coaches' for t in trace['tools'])
            assert 'total_ms' in trace


async def test_unknown_run_trace_is_404(tmp_path):
    app = await _run_demo(tmp_path)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            await client.post('/api/auth/register', json={'username': 'tester', 'password': 'password123'})
            assert (await client.get('/api/runs/nope/trace')).status_code == 404


async def test_provider_usage_is_accounted_into_trace(tmp_path):
    """真实 usage 解析路径：chat/embed/rerank 的 usage 累加进当前 run 轨迹。"""
    from app.providers import Providers
    from app.observability import trace_var

    def handler(request):
        url = str(request.url)
        if '/chat/completions' in url:
            return httpx.Response(200, json={
                'choices': [{'message': {'role': 'assistant', 'content': '回答'}}],
                'usage': {'prompt_tokens': 120, 'completion_tokens': 30, 'total_tokens': 150}})
        if '/embeddings' in url:
            return httpx.Response(200, json={
                'data': [{'index': 0, 'embedding': [0.1, 0.2, 0.3]}],
                'usage': {'total_tokens': 8}})
        return httpx.Response(200, json={
            'output': {'results': [{'index': 0, 'relevance_score': .9}]},
            'usage': {'total_tokens': 42}})

    settings = Settings(_env_file=None, app_mode='live', chat_api_key='k',
                        dashscope_api_key='k', embedding_dimension=3)
    providers = Providers(settings, httpx.MockTransport(handler))
    trace = Trace('run-1', mode='live')
    token = trace_var.set(trace)
    try:
        await providers.chat([{'role': 'user', 'content': '你好'}])
        await providers.embed(['你好'])
        await providers.rerank('减脂', ['增肌'], 1)
    finally:
        trace_var.reset(token)
        await providers.close()
    tokens = trace.to_dict()['tokens']
    assert tokens['chat'] == {'calls': 1, 'total': 150, 'prompt': 120, 'completion': 30}
    assert tokens['embedding']['total'] == 8
    assert tokens['rerank']['total'] == 42
    assert trace.to_dict()['providers']['对话模型']['calls'] == 1


async def test_provider_failure_is_recorded_in_trace_and_log(tmp_path):
    from app.providers import ProviderError, Providers
    from app.observability import trace_var

    settings = Settings(_env_file=None, app_mode='live', chat_api_key='k', dashscope_api_key='k')
    providers = Providers(settings, httpx.MockTransport(lambda r: httpx.Response(401)))
    trace = Trace('run-1', mode='live')
    token = trace_var.set(trace)
    try:
        with pytest.raises(ProviderError, match='401'):
            await providers.chat([{'role': 'user', 'content': '你好'}])
    finally:
        trace_var.reset(token)
        await providers.close()
    row = trace.to_dict()['providers']['对话模型']
    assert row['calls'] == 1 and row['failed'] == 1


def test_configure_logging_is_idempotent_and_respects_format(tmp_path):
    settings = Settings(_env_file=None, log_level='DEBUG', log_format='json')
    root = logging.getLogger()
    original = list(root.handlers)
    try:
        configure_logging(settings, stream=open(tmp_path / 'log.jsonl', 'w', encoding='utf-8'))
        first = root.handlers.copy()
        configure_logging(settings)
        assert root.handlers == first, '重复配置不应叠加 handler'
        assert root.level == logging.DEBUG
        assert isinstance(root.handlers[0].formatter, JSONFormatter)
        assert logging.getLogger('uvicorn.access').level >= logging.WARNING
    finally:
        root.handlers[:] = original
