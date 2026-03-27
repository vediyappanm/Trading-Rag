from redis import Redis
import json
from typing import Any

from infra_rag.config import settings
from infra_rag.resilience import CircuitBreaker


_redis_breaker = CircuitBreaker(
    "redis",
    failure_threshold=settings.redis.circuit_breaker_failures,
    reset_timeout_s=settings.redis.circuit_breaker_reset_s,
)


class RedisClient:
    def __init__(self):
        self._client: Redis | None = None

    @property
    def client(self) -> Redis:
        if self._client is None:
            self._client = Redis(
                host=settings.redis.host,
                port=settings.redis.port,
                password=settings.redis.password or None,
                db=settings.redis.db,
                decode_responses=True,
                socket_timeout=settings.redis.socket_timeout_s,
                socket_connect_timeout=settings.redis.socket_connect_timeout_s,
            )
        return self._client

    def get_baseline(self, symbol: str | None, hour: int) -> dict | None:
        key = f"baseline:{symbol or 'all'}:{hour}"
        if not _redis_breaker.allow():
            return None
        try:
            data = self.client.get(key)
            _redis_breaker.record_success()
            if data:
                return json.loads(data)
            return None
        except Exception:
            _redis_breaker.record_failure()
            return None

    def set_baseline(self, symbol: str | None, hour: int, data: dict, ttl: int = 86400):
        key = f"baseline:{symbol or 'all'}:{hour}"
        if not _redis_breaker.allow():
            return
        try:
            self.client.setex(key, ttl, json.dumps(data))
            _redis_breaker.record_success()
        except Exception:
            _redis_breaker.record_failure()

    def get_cached_query(self, query_hash: str) -> dict | None:
        key = f"query_cache:{query_hash}"
        if not _redis_breaker.allow():
            return None
        try:
            data = self.client.get(key)
            _redis_breaker.record_success()
            if data:
                return json.loads(data)
            return None
        except Exception:
            _redis_breaker.record_failure()
            return None

    def set_cached_query(self, query_hash: str, result: dict, ttl: int = 300):
        key = f"query_cache:{query_hash}"
        if not _redis_breaker.allow():
            return
        try:
            self.client.setex(key, ttl, json.dumps(result))
            _redis_breaker.record_success()
        except Exception:
            _redis_breaker.record_failure()

    def close(self):
        if self._client:
            self._client.close()


redis_client = RedisClient()
