import argparse
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
from app.ingestion import load_dataset, import_sql, synchronize_index


async def main():
    parser = argparse.ArgumentParser(description='校验并幂等导入教练资料和知识父子块')
    parser.add_argument('file', nargs='?', default='data/demo/dataset.json')
    parser.add_argument('--replace', action='store_true', help='将文件外所有知识文档下架（保留教练）')
    parser.add_argument('--reindex', action='store_true', help='重建当前有效文档索引；不读取文件，可恢复失败的索引导入')
    parser.add_argument('--sql-only', action='store_true', help='仅导入SQL，真实模式需稍后 --reindex 才能检索')
    parser.add_argument('--validate-only', action='store_true', help='仅校验数据格式，不写数据库、不调用模型')
    args = parser.parse_args()
    if args.validate_only:
        dataset = load_dataset(args.file)
        print(f'格式有效：{len(dataset.coaches)}位教练，{len(dataset.documents)}篇文档')
        return
    settings = get_settings()
    # JSON 日志写 stderr，stdout 只留脚本自身结果，便于管道处理。
    configure_logging(settings, stream=sys.stderr)
    db = Database(settings.database_url)
    repo = Repository(db)
    providers, cache = Providers(settings), Cache(settings.redis_url)
    try:
        if not args.reindex:
            result = import_sql(repo, load_dataset(args.file), args.replace)
            print(json.dumps(result, ensure_ascii=False))
        if not args.sql_only:
            result = await synchronize_index(settings, repo, providers, Retrieval(settings, repo, providers, cache))
            print(json.dumps(result, ensure_ascii=False))
    finally:
        await providers.close()
        await cache.close()
        db.engine.dispose()


if __name__ == '__main__':
    asyncio.run(main())
