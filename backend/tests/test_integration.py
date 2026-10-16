"""真实存储验收。向量为确定性测试向量，不作为百炼模型验收。"""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from sqlalchemy import create_engine, text
from app.config import Settings
from app.retrieval import MilvusIndex
from app.providers import demo_vector

pytestmark = pytest.mark.integration


@pytest.fixture
def integration_enabled():
    if os.getenv('RUN_INTEGRATION') != '1':
        pytest.skip('设置 RUN_INTEGRATION=1 并启动 Compose 才会验证真实存储')


def _swap_database(url, name):
    """把 SQLAlchemy URL 的库名换成 name，保留查询串。"""
    scheme, sep, rest = url.partition('://')
    netloc, slash, tail = rest.partition('/')
    query = tail[len(tail.split('?')[0]):]
    return f'{scheme}{sep}{netloc}/{name}{query}'


@pytest.fixture(scope='module')
def integration_mysql_url():
    """在独立 schema 上跑 MySQL 验收，绝不写入应用库。

    应用库现在承载真实演示目录；若测试直接往应用库 seed 测试 fixture，
    会静默覆盖真实数据。因此默认使用 `<应用库>_it` 独立 schema，
    必要时用 root 凭据创建并授权，再由 coach 账号迁移与灌入。
    """
    app_url = os.getenv('INTEGRATION_DATABASE_URL') or Settings().database_url
    if not app_url.startswith('mysql'):
        pytest.skip('INTEGRATION_DATABASE_URL 或 DATABASE_URL 需为 MySQL 才会验证真实存储')
    app_db = app_url.split('/')[-1].split('?')[0]
    target = os.getenv('INTEGRATION_DATABASE_URL') or _swap_database(app_url, app_db + '_it')
    target_db = target.split('/')[-1].split('?')[0]
    if target_db == app_db:
        raise RuntimeError('集成测试库不能与应用库同名，请设置 INTEGRATION_DATABASE_URL 指向独立库')
    root_password = os.getenv('MYSQL_ROOT_PASSWORD', 'local-demo-root')
    root_url = _swap_database(app_url.replace('coach:local-demo-coach', f'root:{root_password}'), 'mysql')
    try:
        root = create_engine(root_url)
        with root.begin() as c:
            c.execute(text(f'CREATE DATABASE IF NOT EXISTS `{target_db}` CHARACTER SET utf8mb4'))
            user = app_url.split('://', 1)[1].split(':')[0]
            c.execute(text(f"GRANT ALL PRIVILEGES ON `{target_db}`.* TO '{user}'@'%'"))
            c.execute(text('FLUSH PRIVILEGES'))
        root.dispose()
    except Exception as error:  # 无 root 权限时无法保证隔离，跳过而不是污染应用库
        pytest.skip(f'无法创建/授权独立测试库 {target_db}：{str(error)[:120]}')

    from app.config import get_settings
    from alembic.config import Config
    from alembic import command
    previous = os.environ.get('DATABASE_URL')
    os.environ['DATABASE_URL'] = target
    get_settings.cache_clear()
    try:
        command.upgrade(Config('backend/alembic.ini'), 'head')
    finally:
        if previous is None:
            os.environ.pop('DATABASE_URL', None)
        else:
            os.environ['DATABASE_URL'] = previous
        get_settings.cache_clear()
    return target


def test_real_mysql_migration_seed_and_query(integration_enabled, integration_mysql_url):
    from app.db import Database
    from app.repository import Repository
    from app.ingestion import load_dataset, import_sql
    db = Database(integration_mysql_url)
    with db.engine.connect() as c:
        assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()
    repo = Repository(db)
    dataset = load_dataset(Path(__file__).resolve().parents[2] / 'data/demo/dataset.json')
    import_sql(repo, dataset)
    assert not import_sql(repo, dataset)['changed']
    rows = repo.search_coaches({'budget_max': 300, 'district': '南山区',
        'available_windows': [{'days':[0,1,2,3,4],'start':18,'end':22}]})
    assert {c['id'] for c in rows} == {'coach-01','coach-02'}
    db.engine.dispose()


async def test_real_redis_cache_lock_and_pubsub(integration_enabled):
    from app.cache import Cache
    cache = Cache(os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0'))
    key = 'integration:' + uuid4().hex
    try:
        assert await cache.call('ping')
        await cache.set(key, {'version': 2}, ttl=30)
        assert await cache.get(key) == {'version': 2}
        assert await cache.call('set', key + ':lock', 'owner', nx=True, ex=30)
        assert not await cache.call('set', key + ':lock', 'other', nx=True, ex=30)
        pubsub = cache.client.pubsub()
        await pubsub.subscribe(key + ':events')
        await cache.client.publish(key + ':events', '筛选完成')
        message = None
        for _ in range(5):
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=2)
            if message:
                break
        assert message['data'] == '筛选完成'
        await pubsub.aclose()
    finally:
        await cache.call('delete', key, key + ':lock')
        await cache.close()


def test_real_milvus_chinese_bm25_dense_update_and_scope(integration_enabled):
    settings = Settings(_env_file=None, app_mode='live', embedding_dimension=128,
                        milvus_collection='integration_' + uuid4().hex)
    index = MilvusIndex(settings)
    texts = ['膝盖不适学员使用低冲击训练，教练耐心讲解动作。', '增肌训练关注渐进超负荷与负重记录。', '平台课程按四周安排。']
    rows = [{'chunk_id': f'chunk-{i}', 'coach_id': ['coach-a','coach-b',''][i],
        'parent_id': f'parent-{i}', 'document_id': f'doc-{i}', 'document_version':'1', 'document_type':'case',
        'text': value, 'source_title':'测试资料', 'source_reference':'integration://fixture',
        'is_active': True, 'updated_at':'2026-10-14T00:00:00'} for i, value in enumerate(texts)]
    try:
        index.replace_all(rows, [demo_vector(t) for t in texts])
        dense, bm25 = index.search('膝盖不适 低冲击 耐心', demo_vector(texts[0]), ['coach-a'], {}, 5)
        assert dense and bm25
        assert {c['coach_id'] for c in dense + bm25} == {'coach-a'}
        assert bm25[0]['chunk_id'] == 'chunk-0'
        # 验证真正使用了中文分析器而不是仅接受 schema 参数。
        analyzed = index.client.run_analyzer(texts=['膝盖不适时使用低冲击训练'], analyzer_params={'type':'chinese'})
        assert len(analyzed[0].tokens) > 1
        rows[0]['text'], rows[0]['document_version'] = '版本二训练资料', '2'
        index.replace_all(rows, [demo_vector(r['text']) for r in rows])
        dense, _ = index.search('训练', demo_vector('训练'), ['coach-a'], {}, 5)
        assert dense[0]['document_version'] == '2'
        index.replace_all(rows[1:], [demo_vector(r['text']) for r in rows[1:]])
        assert index.search('训练', demo_vector('训练'), ['coach-a'], {}, 5) == ([], [])
    finally:
        if index.client.has_collection(settings.milvus_collection):
            index.client.drop_collection(settings.milvus_collection)
