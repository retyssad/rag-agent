from pathlib import Path
import pytest
from app.config import Settings
from app.db import Base, Database
from app.repository import Repository
from app.providers import Providers
from app.cache import Cache
from app.retrieval import Retrieval
from app.agents import Orchestrator
from app.ingestion import import_sql, load_dataset


class MemoryCache:
    degraded = False
    def __init__(self):
        self.data = {}
    async def get(self, key):
        return self.data.get(key)
    async def set(self, key, value, ttl=300):
        self.data[key] = value
    async def call(self, method, *args, **kwargs):
        return True


@pytest.fixture
def components(tmp_path):
    settings = Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "test.db"}', app_mode='demo')
    db = Database(settings.database_url)
    Base.metadata.create_all(db.engine)
    repo = Repository(db)
    import_sql(repo, load_dataset(Path(__file__).resolve().parents[2] / 'data/demo/dataset.json'))
    providers = Providers(settings)
    cache = MemoryCache()
    retrieval = Retrieval(settings, repo, providers, cache)
    orchestrator = Orchestrator(settings, repo, providers, retrieval, cache)
    yield settings, repo, providers, cache, retrieval, orchestrator
    db.engine.dispose()


async def execute(components, text, session_id=None, patch=None, coach_ids=None):
    _, repo, _, _, _, agent = components
    session_id = session_id or repo.create_session()['id']
    run = repo.create_run(session_id, text, patch=patch, coach_ids=coach_ids)
    await agent.execute(run['id'])
    return repo.run(run['id'])
