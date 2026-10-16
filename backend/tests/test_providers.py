import json
import httpx
import pytest
from app.config import Settings
from app.providers import Providers, ProviderError


async def test_real_adapter_wire_protocol_for_chat_and_bailian():
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append((str(request.url), body))
        assert request.headers['authorization'] == 'Bearer test-key'
        if '/chat/completions' in str(request.url):
            return httpx.Response(200, json={'choices': [{'message': {'role': 'assistant', 'content': '回答', 'reasoning_content': '不可展示'}}]})
        if '/embeddings' in str(request.url):
            return httpx.Response(200, json={'data': [{'index': 0, 'embedding': [0.1, 0.2, 0.3]}]})
        return httpx.Response(200, json={'output': {'results': [{'index': 1, 'relevance_score': .95}]}})
    settings = Settings(_env_file=None, app_mode='live', chat_api_key='test-key', dashscope_api_key='test-key', embedding_dimension=3)
    providers = Providers(settings, httpx.MockTransport(handler))
    assert await providers.chat([{'role': 'user', 'content': '你好'}]) == {'role': 'assistant', 'content': '回答'}
    assert await providers.embed(['你好']) == [[.1, .2, .3]]
    assert await providers.rerank('减脂', ['增肌', '减脂训练'], 2) == [(1, .95)]
    assert calls[0][1]['model'] == settings.chat_model
    assert calls[1][1]['model'] == settings.bailian_embedding_model
    assert calls[2][1]['model'] == settings.bailian_rerank_model
    await providers.close()


async def test_chat_tools_use_compatible_auto_choice():
    captured = {}

    def handler(request):
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'message': {
            'role': 'assistant', 'content': None,
            'tool_calls': [{'id': 'call-1', 'type': 'function', 'function': {
                'name': 'finish', 'arguments': '{}'}}],
        }}]})

    settings = Settings(_env_file=None, app_mode='live', chat_api_key='test-key')
    providers = Providers(settings, httpx.MockTransport(handler))
    tool = {'type': 'function', 'function': {'name': 'finish', 'parameters': {
        'type': 'object', 'properties': {}, 'additionalProperties': False,
    }}}
    message = await providers.chat([{'role': 'user', 'content': '完成'}], [tool])
    assert message['tool_calls'][0]['function']['name'] == 'finish'
    assert captured['tool_choice'] == 'auto'
    await providers.close()


async def test_bad_dimension_and_http_auth_failures_are_explicit():
    settings = Settings(_env_file=None, app_mode='live', dashscope_api_key='test', embedding_dimension=4)
    providers = Providers(settings, httpx.MockTransport(lambda r: httpx.Response(200, json={'data':[{'index':0,'embedding':[1,2]}]})))
    with pytest.raises(ProviderError, match='维度'):
        await providers.embed(['你好'])
    await providers.close()
    providers = Providers(settings, httpx.MockTransport(lambda r: httpx.Response(401)))
    with pytest.raises(ProviderError, match='401'):
        await providers.embed(['你好'])
    await providers.close()


async def test_live_agent_calls_tools_in_bounded_loop(components):
    from app.agents import ToolAgent, Tool
    from app.schemas import FinishArgs
    settings, _, providers, _, _, _ = components
    settings.app_mode = 'live'
    ctx = {}
    async def emit(*args):
        pass
    async def finish(args):
        ctx['_finished'] = True
        return {'finished': True}
    call = {'role':'assistant','content':None,'tool_calls':[{'id':'x','type':'function','function':{'name':'finish','arguments':'{}'}}]}
    async def chat(messages, tools):
        assert tools[0]['function']['name'] == 'finish'
        assert len(messages) == 2
        return call
    providers.chat = chat
    agent = ToolAgent('test', '完成任务', '测试独立上下文', ctx,
        [Tool('finish','完成',FinishArgs,finish)], providers,settings,emit,lambda s:[])
    await agent.run()
    assert ctx['_finished']


async def test_redis_failure_explicit_degradation():
    from app.cache import Cache
    cache = Cache('redis://127.0.0.1:1/0')
    assert await cache.get('missing') is None
    assert cache.degraded
    await cache.close()


async def test_structured_results_use_validated_function_arguments(components):
    from app.schemas import Route
    _, _, providers, _, _, _ = components
    attempts = []
    async def chat(messages, tools):
        attempts.append(messages)
        assert tools[0]['function']['name'] == 'submit_structured_result'
        if len(attempts) == 1:
            return {'role':'assistant','content':'我给你一个设计方案'}
        return {'role':'assistant','tool_calls':[{'id':'x','type':'function','function':{
            'name':'submit_structured_result','arguments':'{"intents":["coach_recommendation"],"patch":{"budget_max":300}}'}}]}
    providers.chat = chat
    result = await providers.structured('仅提取需求', {'message':'预算300'}, Route)
    assert result.patch.budget_max == 300 and len(attempts) == 2


async def test_tool_agent_rejects_repeated_calls_and_stops_at_limit(components):
    from app.agents import ToolAgent, Tool
    from app.schemas import FinishArgs
    settings, _, providers, _, _, _ = components
    settings.app_mode, settings.max_agent_steps = 'live', 3
    executions = []
    async def observe(args):
        executions.append(args)
        return {'observed':True}
    async def emit(*args):
        pass
    async def chat(messages, tools):
        return {'role':'assistant','tool_calls':[{'id':str(len(messages)),'type':'function','function':{'name':'observe','arguments':'{}'}}]}
    providers.chat = chat
    agent = ToolAgent('bounded','限制重复动作','测试',{},[Tool('observe','观察',FinishArgs,observe)],providers,settings,emit,lambda s:[])
    with pytest.raises(RuntimeError, match='超过上限'):
        await agent.run()
    assert len(executions) == 1


async def test_tool_agent_accepts_json_string_arguments(components):
    # 部分模型（如 Qwen）把数组字段序列化成 JSON 字符串，应容错还原而非直接失败。
    from app.agents import ToolAgent, Tool
    from app.schemas import RetrievalArgs
    settings, _, providers, _, _, _ = components
    settings.app_mode, settings.max_agent_steps = 'live', 3
    captured = []
    async def retrieve(args):
        captured.append(args)
        return {'ok': True}
    async def emit(*args):
        pass
    calls = [
        {'role': 'assistant', 'tool_calls': [{'id': '1', 'type': 'function', 'function': {
            'name': 'retrieve', 'arguments': '{"query": "减脂", "coach_ids": "[\\"coach-01\\", \\"coach-02\\"]"}'}}]},
        {'role': 'assistant', 'tool_calls': [{'id': '2', 'type': 'function', 'function': {
            'name': 'finish', 'arguments': '{}'}}]},
    ]
    from app.schemas import FinishArgs
    ctx = {}
    async def finish(args):
        ctx['_finished'] = True
        return {'finished': True}
    async def chat(messages, tools):
        return calls.pop(0)
    providers.chat = chat
    agent = ToolAgent('coerce', '测试参数容错', '测试',
        ctx, [Tool('retrieve', '检索', RetrievalArgs, retrieve), Tool('finish', '完成', FinishArgs, finish)],
        providers, settings, emit, lambda s: [])
    await agent.run()
    assert captured and captured[0].coach_ids == ['coach-01', 'coach-02']
