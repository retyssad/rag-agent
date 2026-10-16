"""显式调用真实供应商和 RAG，不使用模拟替代。输出不包含密钥。"""
import asyncio
import json
import sys
from app.config import get_settings
from app.db import Database
from app.observability import configure_logging
from app.repository import Repository
from app.providers import Providers
from app.cache import Cache
from app.retrieval import Retrieval
from app.agents import Orchestrator


async def main():
    settings = get_settings()
    if settings.app_mode != 'live':
        raise RuntimeError('验收真实模式前设置 APP_MODE=live')
    settings.require_live_keys()
    # JSON 日志写 stderr；stdout 保持原有的阶段 JSON，便于脚本消费。
    configure_logging(settings, stream=sys.stderr)
    repo = Repository(Database(settings.database_url))
    providers, cache = Providers(settings), Cache(settings.redis_url)
    retrieval = Retrieval(settings, repo, providers, cache)
    orchestrator = Orchestrator(settings, repo, providers, retrieval, cache)
    async def emit(kind, data):
        print(json.dumps({'stage':kind,'data':data}, ensure_ascii=False))
    try:
        message = await providers.chat([{'role':'user','content':'用一句中文解释渐进超负荷。'}])
        print(json.dumps({'chat_model': bool(message.get('content'))}, ensure_ascii=False))
        vectors = await providers.embed(['膝盖不适时低冲击训练', '新手减脂动作讲解'])
        assert len(vectors[0]) == settings.embedding_dimension
        ranked = await providers.rerank('新手减脂', ['新手减脂动作指导', '复杂竞技举重动作'], 2)
        print(json.dumps({'embedding_dim':len(vectors[0]),'external_rerank':ranked}, ensure_ascii=False))
        result = await retrieval.retrieve('平台减脂课程安排', '减脂课程 四周 安排', [''], {}, {'version':1}, emit)
        assert result['evidence'], '当前资料中找不到平台减脂证据，请先导入'
        run = repo.create_run(repo.create_session()['id'], '推荐减脂教练，预算300元以内，南山区，工作日晚上，希望耐心', execution_scope='cli')
        await orchestrator.execute(run['id'])
        final = repo.run(run['id'])
        assert final['status'] == 'completed', final['error'] or final['status']
        print(json.dumps({'live_workflow':final['status'],'candidates':len(final['result']['candidates']),
                          'knowledge_version':final['result']['knowledge_version']}, ensure_ascii=False))
    finally:
        await providers.close()
        await cache.close()
        repo.db.engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
