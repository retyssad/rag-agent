"""日志与运行追踪：结构化 JSON 日志、请求 id 贯穿、每次 run 一条 trace。

不使用第三方日志库：Python stdlib logging 加自写 formatter 即可满足需求，
避免为此引入新依赖并重新生成 requirements.lock.txt。

约束（见 PROJECT_HANDOFF.md）：日志与 trace 只记录可展示的执行事实
（阶段名、工具名、耗时、token、错误摘要），不记录模型内部思维链，
也不记录工具原始参数和供应商原始响应。
"""
import json
import logging
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

# 当前请求 / 当前运行 / 当前 trace 的上下文；由中间件与 Orchestrator 写入，
# 其余模块只读，因此无需层层传参。
request_id_var: ContextVar[str | None] = ContextVar('request_id', default=None)
run_id_var: ContextVar[str | None] = ContextVar('run_id', default=None)
session_id_var: ContextVar[str | None] = ContextVar('session_id', default=None)
user_id_var: ContextVar[str | None] = ContextVar('user_id', default=None)
trace_var: ContextVar['Trace | None'] = ContextVar('trace', default=None)

# LogRecord 自带的属性，不能当作业务字段透传。
_RESERVED = frozenset(
    ('name', 'msg', 'args', 'levelname', 'levelno', 'pathname', 'filename', 'module',
     'exc_info', 'exc_text', 'stack_info', 'lineno', 'funcName', 'created', 'msecs',
     'relativeCreated', 'thread', 'threadName', 'processName', 'process', 'taskName',
     'message', 'asctime',
     # uvicorn 用 color_message 重复同一段消息并带 ANSI 转义，JSON 下不需要。
     'color_message')
)


def new_request_id():
    return uuid.uuid4().hex


def utc_iso():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def get_logger(name):
    return logging.getLogger(name)


class JSONFormatter(logging.Formatter):
    """单行 JSON：固定字段 + 上下文 id + extra 业务字段。"""

    def format(self, record):
        payload = {
            'ts': utc_iso(),
            'level': record.levelname,
            'logger': record.name,
            'event': getattr(record, 'event', record.name),
            'msg': record.getMessage(),
        }
        for key, var in (('request_id', request_id_var), ('run_id', run_id_var),
                         ('session_id', session_id_var), ('user_id', user_id_var)):
            value = var.get()
            if value:
                payload[key] = value
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith('_') and key not in payload:
                payload[key] = value
        if record.exc_info:
            payload['exc_type'] = getattr(record.exc_info[0], '__name__', 'Exception')
            payload['exc'] = self.formatException(record.exc_info)[-1500:]
        # default=str 兜底：不因某个字段不可序列化而丢日志。
        return json.dumps(payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """人类可读模式，便于本机直接观察。"""

    def format(self, record):
        parts = [utc_iso(), record.levelname, getattr(record, 'event', record.name), record.getMessage()]
        for key, var in (('request_id', request_id_var), ('run_id', run_id_var)):
            if var.get():
                parts.append(f'{key}={var.get()}')
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith('_'):
                parts.append(f'{key}={value}')
        if record.exc_info:
            parts.append(self.formatException(record.exc_info)[-1500:])
        return ' '.join(str(p) for p in parts)


def make_formatter(log_format='json'):
    return TextFormatter() if log_format == 'text' else JSONFormatter()


def configure_logging(settings, stream=None):
    """幂等安装 root handler；重复调用只更新级别与格式。

    stream 默认 stdout（容器/服务端）。CLI 脚本把日志指向 stderr，把 stdout
    留给脚本自身的 JSON 输出，避免两路内容混在一起。

    uvicorn 自己也在启动时配置日志，因此 app 的 lifespan 启动阶段会再次调用本函数，
    让 uvicorn.error 走同一个 JSON formatter。uvicorn 内置的 access logger 被调低到
    WARNING：每个请求由 RequestContextMiddleware 输出一条带 request_id 和耗时的
    access 日志，避免两套访问日志重复。
    """
    handler = getattr(configure_logging, '_handler', None)
    if handler is None:
        handler = logging.StreamHandler(stream or sys.stdout)
        configure_logging._handler = handler
        root = logging.getLogger()
        for existing in list(root.handlers):
            root.removeHandler(existing)
        root.addHandler(handler)
    level = getattr(logging, str(getattr(settings, 'log_level', 'INFO')).upper(), logging.INFO)
    handler.setFormatter(make_formatter(getattr(settings, 'log_format', 'json')))
    root = logging.getLogger()
    root.setLevel(level)
    for name in ('uvicorn', 'uvicorn.error', 'alembic'):
        child = logging.getLogger(name)
        child.handlers = []
        child.propagate = True
        child.setLevel(level)
    access = logging.getLogger('uvicorn.access')
    access.handlers = []
    access.propagate = True
    access.setLevel(logging.WARNING)
    # jieba 自带 logger 且默认 DEBUG，会把分词器加载噪声混进结构化日志。
    for name in ('jieba', 'httpx', 'httpcore'):
        noisy = logging.getLogger(name)
        noisy.handlers = []
        noisy.propagate = True
        noisy.setLevel(logging.WARNING)
    return handler


class RequestContextMiddleware:
    """纯 ASGI 中间件：注入 request-id 上下文、回显响应头、记录访问日志。

    刻意不用 BaseHTTPMiddleware：它会缓冲/包装响应体，与 SSE 流式响应不兼容。
    """

    def __init__(self, app):
        self.app = app
        self.logger = get_logger('app.http')

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            await self.app(scope, receive, send)
            return
        headers = {k.decode('latin-1').lower(): v.decode('latin-1') for k, v in scope.get('headers') or []}
        request_id = (headers.get('x-request-id') or '').strip()[:64] or new_request_id()
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        status = {'code': 500}

        async def send_wrapper(message):
            if message['type'] == 'http.response.start':
                status['code'] = message['status']
                raw = list(message.get('headers') or [])
                raw.append((b'x-request-id', request_id.encode('latin-1')))
                message = {**message, 'headers': raw}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000)
            # 只记方法/路径/状态/耗时；不记 query、body、Cookie。
            self.logger.info('请求完成', extra={'event': 'http.request',
                                                'method': scope.get('method'), 'path': scope.get('path'),
                                                'status': status['code'], 'duration_ms': duration_ms})
            request_id_var.reset(token)


class Trace:
    """一次 run 的执行轨迹：阶段耗时、工具调用、供应商调用与 token。

    人工暂停后恢复会再次进入 execute()；此时用 prior 续写同一条轨迹，
    累计耗时（用户思考时间不计入）并保留各阶段明细。
    """

    def __init__(self, run_id, session_id=None, mode='demo', request_id=None, prior=None):
        prior = prior or {}
        self._accumulated_ms = int(prior.get('total_ms') or 0)
        request_ids = list(prior.get('request_ids') or [])
        if prior.get('request_id') and prior['request_id'] not in request_ids:
            request_ids.append(prior['request_id'])
        if request_id and request_id not in request_ids:
            request_ids.append(request_id)
        self.data = {
            'request_id': prior.get('request_id') or request_id,
            'request_ids': request_ids,
            'run_id': run_id,
            'session_id': session_id or prior.get('session_id'),
            'mode': mode,
            'status': 'running',
            'started_at': prior.get('started_at') or utc_iso(),
            'total_ms': None,
            'resumes': int(prior.get('resumes') or 0) + (1 if prior else 0),
            'stages': list(prior.get('stages') or []),
            'tools': list(prior.get('tools') or []),
            'providers': {k: dict(v) for k, v in (prior.get('providers') or {}).items()},
            'tokens': {k: dict(v) for k, v in (prior.get('tokens') or {}).items()},
            'errors': list(prior.get('errors') or []),
        }
        self._started = time.perf_counter()

    def _elapsed_ms(self):
        return round((time.perf_counter() - self._started) * 1000)

    def snapshot(self):
        """运行中的快照：未结束时重算 total_ms；已结束则原样返回。"""
        data = dict(self.data)
        if data.get('status') == 'running':
            data['total_ms'] = self._accumulated_ms + self._elapsed_ms()
        return data

    @contextmanager
    def stage(self, name):
        started = time.perf_counter()
        entry = {'name': name, 'ok': True}
        try:
            yield entry
        except BaseException:
            self.data['stages'].append({**entry, 'ok': False, 'ms': round((time.perf_counter() - started) * 1000)})
            raise
        self.data['stages'].append({**entry, 'ms': round((time.perf_counter() - started) * 1000)})

    def add_stage(self, name, ms, ok=True):
        self.data['stages'].append({'name': name, 'ms': int(ms), 'ok': ok})

    def add_tool(self, agent, tool, ms, ok=True):
        self.data['tools'].append({'agent': agent, 'tool': tool, 'ms': int(ms), 'ok': bool(ok)})

    def add_provider(self, label, ms, ok=True, retries=0):
        row = self.data['providers'].setdefault(
            label, {'calls': 0, 'ms_total': 0, 'ms_max': 0, 'failed': 0, 'retries': 0})
        row['calls'] += 1
        row['ms_total'] += int(ms)
        row['ms_max'] = max(row['ms_max'], int(ms))
        row['failed'] += 0 if ok else 1
        row['retries'] += int(retries)

    def add_tokens(self, kind, prompt=0, completion=0, total=0):
        row = self.data['tokens'].setdefault(kind, {'calls': 0, 'total': 0})
        row['calls'] += 1
        for key, value in (('prompt', prompt), ('completion', completion)):
            if value:
                row[key] = row.get(key, 0) + int(value)
        row['total'] += int(total) if total else int(prompt) + int(completion)

    def add_error(self, stage, error):
        self.data['errors'].append({'stage': stage, 'type': type(error).__name__, 'message': str(error)[:300]})

    def finish(self, status):
        self.data['status'] = status
        self.data['finished_at'] = utc_iso()
        self.data['total_ms'] = self._accumulated_ms + self._elapsed_ms()
        return self.data

    def to_dict(self):
        return self.data


def get_trace():
    return trace_var.get()


@contextmanager
def named_stage(name):
    """记录一个阶段耗时；没有活动 trace 时（如子 Agent 单测）为空操作。"""
    trace = trace_var.get()
    if not trace:
        yield
        return
    with trace.stage(name):
        yield
