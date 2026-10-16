import asyncio
import httpx
from app.main import create_app
from app.db import Base
from app.ingestion import import_sql, load_dataset
from pathlib import Path


async def test_rest_sse_cancel_and_refresh(tmp_path):
    from app.config import Settings
    app = create_app(Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "api.db"}', redis_url='redis://127.0.0.1:1/0'))
    repo = app.state.repo
    Base.metadata.create_all(repo.db.engine)
    import_sql(repo, load_dataset(Path(__file__).resolve().parents[2] / 'data/demo/dataset.json'))
    # API 行为测试不依赖 Redis，生产实现另有故障降级测试。
    from conftest import MemoryCache
    app.state.orchestrator.cache = MemoryCache()
    app.state.retrieval.cache = app.state.orchestrator.cache
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url='http://test') as client:
            registered = await client.post('/api/auth/register', json={'username': 'tester', 'password': 'password123'})
            assert registered.status_code == 201
            session = (await client.post('/api/sessions')).json()
            sent = await client.post(f'/api/sessions/{session["id"]}/messages', json={'content':'帮我推荐教练'})
            assert sent.status_code == 202
            run_id = sent.json()['id']
            for _ in range(100):
                run = (await client.get('/api/runs/' + run_id)).json()
                if run['status'] == 'waiting_for_user':
                    break
                await asyncio.sleep(.01)
            assert run['status'] == 'waiting_for_user'
            events = await client.get('/api/runs/' + run_id + '/events')
            assert events.headers['content-type'].startswith('text/event-stream')
            assert 'waiting_for_user' in events.text and 'event: done' in events.text
            request_id = run['clarification']['id']
            assert (await client.post('/api/runs/' + run_id + '/cancel')).json()['status'] == 'cancelled'
            assert (await client.post(f'/api/clarifications/{request_id}/reply', json={'answer':'减脂，南山区'})).status_code == 409
            refreshed = (await client.get('/api/sessions/' + session['id'])).json()
            assert refreshed['runs'][0]['status'] == 'cancelled'
            assert (await client.get('/api/runs/nonexistent')).status_code == 404
            assert (await client.post(f'/api/sessions/{session["id"]}/messages', json={'content':''})).status_code == 422
