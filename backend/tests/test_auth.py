import httpx
import pytest
from app.main import create_app
from app.auth import hash_password, verify_password


@pytest.fixture
def client(tmp_path):
    from app.config import Settings
    app = create_app(Settings(_env_file=None, database_url=f'sqlite:///{tmp_path / "auth.db"}',
                              redis_url='redis://127.0.0.1:1/0'))
    from app.db import Base
    Base.metadata.create_all(app.state.repo.db.engine)
    from conftest import MemoryCache
    app.state.orchestrator.cache = MemoryCache()
    app.state.retrieval.cache = app.state.orchestrator.cache
    return app


def test_password_hashing():
    stored = hash_password('correct horse battery')
    assert stored.startswith('scrypt$')
    assert verify_password('correct horse battery', stored)
    assert not verify_password('wrong password', stored)
    assert not verify_password('correct horse battery', 'garbage')


async def test_register_login_me_logout(client):
    async with client.router.lifespan_context(client):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(client), base_url='http://test') as c:
            # 未登录一律 401（health 除外）。
            assert (await c.get('/api/health')).status_code == 200
            assert (await c.get('/api/auth/me')).status_code == 401
            assert (await c.get('/api/sessions')).status_code == 401
            assert (await c.post('/api/sessions')).status_code == 401
            assert (await c.get('/api/coaches')).status_code == 401

            registered = await c.post('/api/auth/register', json={'username': 'alice', 'password': 'password123'})
            assert registered.status_code == 201
            assert registered.json()['user']['username'] == 'alice'
            assert 'coach_token' in registered.cookies
            # 注册后即已登录。
            me = await c.get('/api/auth/me')
            assert me.status_code == 200 and me.json()['user']['username'] == 'alice'

            # 重复用户名。
            assert (await c.post('/api/auth/register', json={'username': 'alice', 'password': 'password456'})).status_code == 409

            # 登出后需要重新登录。
            assert (await c.post('/api/auth/logout')).status_code == 200
            c.cookies.clear()
            assert (await c.get('/api/auth/me')).status_code == 401

            # 错误密码统一 401，不泄露用户名是否存在。
            assert (await c.post('/api/auth/login', json={'username': 'alice', 'password': 'nope12345'})).status_code == 401
            assert (await c.post('/api/auth/login', json={'username': 'nobody', 'password': 'password123'})).status_code == 401

            ok = await c.post('/api/auth/login', json={'username': 'alice', 'password': 'password123'})
            assert ok.status_code == 200 and ok.json()['user']['username'] == 'alice'

            # 表单校验。
            assert (await c.post('/api/auth/register', json={'username': 'a b', 'password': 'password123'})).status_code == 422
            assert (await c.post('/api/auth/register', json={'username': 'bob', 'password': 'short'})).status_code == 422


async def test_session_isolation_and_management(client):
    async with client.router.lifespan_context(client):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(client), base_url='http://test') as a, \
                   httpx.AsyncClient(transport=httpx.ASGITransport(client), base_url='http://test') as b:
            await a.post('/api/auth/register', json={'username': 'alice', 'password': 'password123'})
            await b.post('/api/auth/register', json={'username': 'bob', 'password': 'password123'})

            session = (await a.post('/api/sessions')).json()
            sent = await a.post(f'/api/sessions/{session["id"]}/messages', json={'content': '找个耐心教练'})
            assert sent.status_code == 202
            run_id = sent.json()['id']

            # b 无法读取、发消息、取消、看事件流、删除 a 的会话与运行：一律 404。
            assert (await b.get(f'/api/sessions/{session["id"]}')).status_code == 404
            assert (await b.post(f'/api/sessions/{session["id"]}/messages', json={'content': 'hi'})).status_code == 404
            assert (await b.get(f'/api/runs/{run_id}')).status_code == 404
            assert (await b.post(f'/api/runs/{run_id}/cancel')).status_code == 404
            assert (await b.get(f'/api/runs/{run_id}/events')).status_code == 404
            assert (await b.delete(f'/api/sessions/{session["id"]}')).status_code == 404
            assert (await b.post(f'/api/sessions/{session["id"]}/compare',
                                 json={'content': '比较', 'coach_ids': ['coach-01', 'coach-02']})).status_code == 404

            # 列表：各自只看到自己的。
            a_list = (await a.get('/api/sessions')).json()
            b_list = (await b.get('/api/sessions')).json()
            assert [s['id'] for s in a_list] == [session['id']]
            assert b_list == []
            assert a_list[0]['title'].startswith('找个耐心教练')

            # a 自己可以删除，删除后列表为空且再次访问 404。
            assert (await a.delete(f'/api/sessions/{session["id"]}')).status_code == 200
            assert (await a.get('/api/sessions')).json() == []
            assert (await a.get(f'/api/sessions/{session["id"]}')).status_code == 404


async def test_reply_isolation(client):
    async with client.router.lifespan_context(client):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(client), base_url='http://test') as a, \
                   httpx.AsyncClient(transport=httpx.ASGITransport(client), base_url='http://test') as b:
            await a.post('/api/auth/register', json={'username': 'alice', 'password': 'password123'})
            await b.post('/api/auth/register', json={'username': 'bob', 'password': 'password123'})
            session = (await a.post('/api/sessions')).json()
            sent = await a.post(f'/api/sessions/{session["id"]}/messages', json={'content': '帮我推荐教练'})
            run_id = sent.json()['id']
            import asyncio
            for _ in range(200):
                run = (await a.get('/api/runs/' + run_id)).json()
                if run['status'] == 'waiting_for_user':
                    break
                await asyncio.sleep(.01)
            assert run['status'] == 'waiting_for_user'
            request_id = run['clarification']['id']
            # b 不能回答 a 的问题。
            assert (await b.post(f'/api/clarifications/{request_id}/reply', json={'answer': '减脂，南山区'})).status_code == 404
            # a 可以。
            assert (await a.post(f'/api/clarifications/{request_id}/reply', json={'answer': '减脂，南山区'})).status_code == 202
