import asyncio
import hashlib
import json
from contextlib import asynccontextmanager
from fastapi import Depends, FastAPI, Request, Response, Query, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from app.config import Settings, get_settings
from app.db import Database
from app.documents import ExtractError, extract
from app.observability import (RequestContextMiddleware, configure_logging, get_logger,
                               session_id_var, user_id_var)
from app.repository import Repository, Conflict, NotFound, ValidationFailed
from app.auth import AuthService, Unauthorized, COOKIE_NAME, TOKEN_TTL_DAYS
from app.providers import Providers
from app.cache import Cache
from app.retrieval import Retrieval
from app.agents import Orchestrator
from app.userkb import UserKnowledge
from app.schemas import (LoginInput, MessageInput, RegisterInput, ReplyInput, PreregisterInput,
                         FavoriteInput, KnowledgeQuestion)

logger = get_logger('app.main')


def create_app(settings: Settings | None = None):
    settings = settings or get_settings()
    configure_logging(settings)
    db = Database(settings.database_url)
    repo = Repository(db)
    auth = AuthService(db)
    providers = Providers(settings)
    cache = Cache(settings.redis_url)
    retrieval = Retrieval(settings, repo, providers, cache)
    user_kb = UserKnowledge(settings, repo, providers, cache)
    orchestrator = Orchestrator(settings, repo, providers, retrieval, cache)

    @asynccontextmanager
    async def lifespan(app):
        # uvicorn 在导入 app 之后才配置自己的日志，这里再配置一次让服务端日志
        # 与业务日志使用同一个 JSON formatter。
        configure_logging(settings)
        try:
            repo.recover_interrupted()
        except Exception as error:
            logger.error('数据库未就绪，请先执行 Alembic 迁移', extra={'event': 'startup.failed'}, exc_info=True)
            raise RuntimeError('数据库未就绪，请先执行 Alembic 迁移；真实模式请先启动基础服务。') from error
        logger.info('服务启动', extra={'event': 'startup', 'mode': settings.app_mode,
                                    'database': settings.database_url.split('://')[0]})
        yield
        await orchestrator.close()
        await providers.close()
        await cache.close()
        db.engine.dispose()
        logger.info('服务已停止', extra={'event': 'shutdown'})

    app = FastAPI(title='教练选择助手', version='1.0.0', lifespan=lifespan)
    app.state.repo, app.state.orchestrator = repo, orchestrator
    app.state.retrieval, app.state.providers, app.state.settings = retrieval, providers, settings
    app.state.user_kb = user_kb
    app.state.auth = auth
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins.split(','),
                       allow_credentials=True,
                       allow_methods=['GET', 'POST', 'DELETE'],
                       allow_headers=['Content-Type', 'Last-Event-ID', 'X-Request-ID'],
                       expose_headers=['X-Request-ID'])
    # 纯 ASGI 中间件：request-id 贯穿 API→orchestrator→provider 调用。
    app.add_middleware(RequestContextMiddleware)

    def current_user(request: Request) -> dict:
        user = auth.resolve(request.cookies.get(COOKIE_NAME))
        user_id_var.set(user['id'])
        return user

    def set_cookie(response: Response, token: str):
        response.set_cookie(COOKIE_NAME, token, max_age=TOKEN_TTL_DAYS * 24 * 3600,
                            httponly=True, samesite='lax', path='/')

    @app.exception_handler(Unauthorized)
    async def unauthorized_handler(request, exc):
        return JSONResponse(status_code=401, content={'detail': str(exc)})

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse(status_code=409, content={'detail': str(exc)})

    @app.exception_handler(NotFound)
    async def missing_handler(request, exc):
        return JSONResponse(status_code=404, content={'detail': str(exc)})

    @app.exception_handler(ValidationFailed)
    async def validation_handler(request, exc):
        return JSONResponse(status_code=400, content={'detail': str(exc)})

    @app.exception_handler(ExtractError)
    async def extract_handler(request, exc):
        return JSONResponse(status_code=400, content={'detail': str(exc)})

    @app.get('/api/health')
    async def health():
        sql_ok = True
        try:
            with db.engine.connect() as connection:
                connection.execute(text('SELECT 1'))
        except Exception:
            sql_ok = False
        redis_ok = bool(await cache.call('ping'))
        return {'mode': settings.app_mode, 'database': sql_ok, 'redis': redis_ok,
                'model_configured': bool(settings.chat_api_key and settings.dashscope_api_key),
                'knowledge': repo.knowledge_state() if sql_ok else None,
                'timezone': 'Asia/Hong_Kong', 'is_demo': settings.app_mode == 'demo'}

    @app.post('/api/auth/register', status_code=201)
    async def register(payload: RegisterInput, response: Response):
        user, token = auth.register(payload.username, payload.password)
        set_cookie(response, token)
        return {'user': user}

    @app.post('/api/auth/login')
    async def sign_in(payload: LoginInput, response: Response):
        user, token = auth.login(payload.username, payload.password)
        set_cookie(response, token)
        return {'user': user}

    @app.post('/api/auth/logout')
    async def sign_out(request: Request, response: Response):
        auth.logout(request.cookies.get(COOKIE_NAME))
        response.delete_cookie(COOKIE_NAME, path='/')
        return {'ok': True}

    @app.get('/api/auth/me')
    async def me(user: dict = Depends(current_user)):
        return {'user': user}

    @app.post('/api/sessions', status_code=201)
    async def new_session(user: dict = Depends(current_user)):
        return repo.create_session(user['id'])

    @app.get('/api/sessions')
    async def sessions(user: dict = Depends(current_user)):
        return repo.list_sessions(user['id'])

    @app.get('/api/sessions/{session_id}')
    async def get_session(session_id: str, user: dict = Depends(current_user)):
        return repo.session(session_id, user['id'])

    @app.delete('/api/sessions/{session_id}')
    async def delete_session(session_id: str, user: dict = Depends(current_user)):
        view = repo.session(session_id, user['id'])
        active = next((r for r in view['runs'] if r['status'] in ('created', 'running', 'waiting_for_user')), None)
        if active:
            repo.cancel(active['id'], user['id'])
            orchestrator.stop(active['id'])
        return repo.delete_session(session_id, user['id'])

    @app.post('/api/sessions/{session_id}/messages', status_code=202)
    async def send_message(session_id: str, payload: MessageInput, user: dict = Depends(current_user)):
        session_id_var.set(session_id)
        run = repo.create_run(session_id, payload.content,
                              payload.patch.model_dump(exclude_unset=True) if payload.patch else None,
                              payload.coach_ids, user_id=user['id'])
        orchestrator.launch(run['id'])
        return run

    @app.get('/api/runs/{run_id}')
    async def get_run(run_id: str, user: dict = Depends(current_user)):
        return repo.run(run_id, user['id'])

    @app.get('/api/runs/{run_id}/trace')
    async def get_run_trace(run_id: str, user: dict = Depends(current_user)):
        """每次 run 的执行轨迹：阶段耗时、工具调用、供应商调用与 token。"""
        return repo.run_trace(run_id, user['id'])

    @app.post('/api/runs/{run_id}/cancel')
    async def cancel(run_id: str, user: dict = Depends(current_user)):
        run = repo.cancel(run_id, user['id'])
        orchestrator.stop(run_id)
        return run

    @app.post('/api/clarifications/{request_id}/reply', status_code=202)
    async def reply(request_id: str, payload: ReplyInput, user: dict = Depends(current_user)):
        run, claimed = repo.reply(request_id, payload.answer,
                                payload.patch.model_dump(exclude_unset=True) if payload.patch else None,
                                user['id'])
        if claimed:
            orchestrator.launch(run['id'])
        return {**run, 'resumed': claimed}

    @app.get('/api/runs/{run_id}/events')
    async def events(run_id: str, request: Request, after: int = Query(0, ge=0),
                     user: dict = Depends(current_user)):
        repo.run(run_id, user['id'])
        try:
            cursor = max(after, int(request.headers.get('last-event-id') or 0))
        except ValueError:
            cursor = after

        async def stream():
            nonlocal cursor
            idle = 0
            while not await request.is_disconnected():
                batch = repo.events(run_id, cursor)
                for event in batch:
                    cursor = event['id']
                    yield f'id: {cursor}\ndata: {json.dumps(event, ensure_ascii=False, default=str)}\n\n'
                state = repo.run(run_id)['status']
                if state in ('completed', 'failed', 'cancelled', 'waiting_for_user') and not batch:
                    yield f'event: done\ndata: {json.dumps({"status": state})}\n\n'
                    break
                idle += 1
                if idle % 20 == 0:
                    yield ': heartbeat\n\n'
                await asyncio.sleep(.3)
        return StreamingResponse(stream(), media_type='text/event-stream',
                                  headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get('/api/coaches')
    async def coaches(budget_max: float | None = Query(None, ge=0), district: str | None = None,
                      user: dict = Depends(current_user)):
        return repo.search_coaches({'budget_max': budget_max, 'district': district})

    @app.get('/api/coaches/{coach_id}')
    async def coach(coach_id: str, user: dict = Depends(current_user)):
        return repo.coach(coach_id)

    @app.post('/api/sessions/{session_id}/compare', status_code=202)
    async def compare(session_id: str, payload: MessageInput, user: dict = Depends(current_user)):
        if len(payload.coach_ids) < 2:
            raise Conflict('至少选择两位教练')
        session_id_var.set(session_id)
        run = repo.create_run(session_id, '比较教练：' + payload.content,
                              coach_ids=payload.coach_ids, user_id=user['id'])
        orchestrator.launch(run['id'])
        return run

    @app.get('/api/evidence/{parent_id}')
    async def evidence(parent_id: str, version: str | None = None, user: dict = Depends(current_user)):
        return repo.parent(parent_id, version)

    @app.get('/api/runs/{run_id}/report')
    async def run_report(run_id: str, user: dict = Depends(current_user)):
        """决策报告数据：候选对比 + 逐条依据 + 待确认项，供前端打印导出 PDF。"""
        return repo.run_report(run_id, user['id'])

    @app.get('/api/me/profile')
    async def my_profile(user: dict = Depends(current_user)):
        return repo.user_profile(user['id'])

    @app.get('/api/me/favorites')
    async def my_favorites(user: dict = Depends(current_user)):
        return repo.list_favorites(user['id'])

    @app.post('/api/me/favorites', status_code=201)
    async def add_favorite(payload: FavoriteInput, user: dict = Depends(current_user)):
        result = repo.add_favorite(user['id'], payload.coach_id)
        repo.refresh_profile(user['id'])
        return result

    @app.delete('/api/me/favorites/{coach_id}')
    async def remove_favorite(coach_id: str, user: dict = Depends(current_user)):
        result = repo.remove_favorite(user['id'], coach_id)
        repo.refresh_profile(user['id'])
        return result

    @app.get('/api/me/preregistrations')
    async def my_preregistrations(coach_id: str | None = None, user: dict = Depends(current_user)):
        return repo.list_preregistrations(user['id'], coach_id)

    @app.delete('/api/me/preregistrations')
    async def clear_preregistrations(coach_id: str | None = None, user: dict = Depends(current_user)):
        """清空该账号的预登记（可按教练限定）。"""
        result = repo.delete_preregistrations(user['id'], coach_id)
        repo.refresh_profile(user['id'])
        return result

    @app.delete('/api/me/preregistrations/{prereg_id}')
    async def delete_preregistration(prereg_id: str, user: dict = Depends(current_user)):
        result = repo.delete_preregistration(user['id'], prereg_id)
        repo.refresh_profile(user['id'])
        return result

    @app.post('/api/me/preregistrations', status_code=201)
    async def create_preregistration(payload: PreregisterInput, user: dict = Depends(current_user)):
        if payload.session_id:
            repo.session(payload.session_id, user['id'])
        record = repo.create_preregistration(user['id'], payload.coach_id,
            [s.model_dump() for s in payload.slots], payload.note, payload.session_id)
        repo.refresh_profile(user['id'])
        return record

    @app.delete('/api/me/profile')
    async def reset_profile(user: dict = Depends(current_user)):
        """清空跨会话画像并重置累积；聊天记录保留，之后只累积新会话。"""
        return repo.reset_profile(user['id'])

    @app.get('/api/coaches/{coach_id}/schedule')
    async def coach_schedule(coach_id: str, weeks: int = Query(4, ge=1, le=8),
                             user: dict = Depends(current_user)):
        """教练未来数周的逐日可约视图；带上当前账号已登记的时间。"""
        return repo.coach_schedule(coach_id, user['id'], weeks)

    # ---------------------------------------------------------------- 用户专属知识库
    @app.get('/api/kb/documents')
    async def kb_documents(user: dict = Depends(current_user)):
        return {'documents': repo.list_user_documents(user['id']),
                'state': repo.user_knowledge_state(user['id'])}

    @app.post('/api/kb/documents', status_code=201)
    async def kb_upload(file: UploadFile = File(...), title: str | None = Form(None),
                        user: dict = Depends(current_user)):
        """上传 PDF / Markdown / 纯文本，抽取文本后入库并重建该账号的索引。"""
        raw = await file.read()
        text, meta = extract(file.filename, raw)
        content_hash = hashlib.sha256(raw).hexdigest()
        name = (file.filename or '').strip()[:255]
        try:
            result = await user_kb.ingest(user['id'], title=(title or name).strip()[:255] or name,
                filename=name, file_type=meta['file_type'], byte_size=len(raw), text=text,
                content_hash=content_hash)
        except Exception as error:
            # 文档已写入 SQL，但索引失败：明确告知并保持“未同步”，可由“重建索引”恢复，
            # 不能返回裸 500 让用户以为什么都没发生。
            logger.error('用户知识库索引失败', extra={'event': 'kb.upload_index_failed'}, exc_info=True)
            return JSONResponse(status_code=503, content={'detail':
                '文档已保存，但向量索引未建立；请稍后点击“重建索引”。原因：'
                + type(error).__name__})
        return {**result, 'truncated': meta['truncated'], 'chars': meta['chars']}

    @app.delete('/api/kb/documents/{document_id}')
    async def kb_delete(document_id: str, user: dict = Depends(current_user)):
        return await user_kb.delete_document(user['id'], document_id)

    @app.post('/api/kb/reindex')
    async def kb_reindex(user: dict = Depends(current_user)):
        """重建当前账号的知识库索引（模型/维度变更或索引未同步时使用）。"""
        return await user_kb.reindex(user['id'])

    @app.post('/api/kb/query')
    async def kb_query(payload: KnowledgeQuestion, user: dict = Depends(current_user)):
        """基于该账号自己的文档回答，带引用；只用该账号的资料。"""
        return await user_kb.answer(user['id'], payload.question)

    @app.get('/api/kb/evidence/{parent_id}')
    async def kb_evidence(parent_id: str, version: str | None = None, user: dict = Depends(current_user)):
        return repo.user_parent(user['id'], parent_id, version)

    return app


app = create_app()
