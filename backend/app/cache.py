import asyncio
import json
from redis.asyncio import Redis
from app.observability import get_logger

logger = get_logger('app.cache')


class Cache:
    def __init__(self, url):
        self.client = Redis.from_url(url, decode_responses=True, socket_connect_timeout=1, socket_timeout=1)
        self.degraded = False

    async def call(self, method, *args, **kwargs):
        try:
            result = await getattr(self.client, method)(*args, **kwargs)
            if self.degraded:
                logger.info('Redis 恢复可用，缓存重新启用', extra={'event': 'cache.recovered'})
            self.degraded = False
            return result
        except Exception:
            # Redis 故障只影响临时缓存；人工输入和事件的事实在 SQL 中。
            if not self.degraded:
                logger.warning('Redis 不可用，缓存降级；SQL 状态与事件不受影响',
                               extra={'event': 'cache.degraded', 'op': method})
            self.degraded = True
            return None

    async def get(self, key):
        raw = await self.call('get', key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except ValueError:
            return None

    async def set(self, key, value, ttl=300):
        await self.call('set', key, json.dumps(value, ensure_ascii=False), ex=ttl)

    async def close(self):
        await self.client.aclose()
