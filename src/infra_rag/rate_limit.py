import time
from collections import defaultdict, deque

from infra_rag.config import settings


class RateLimiter:
    def __init__(self):
        self._requests: dict[str, deque[float]] = defaultdict(deque)

    def allow(self, key: str) -> bool:
        rps = settings.api.rate_limit_rps
        if rps <= 0:
            return True
        now = time.time()
        window = 1.0
        q = self._requests[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= rps:
            return False
        q.append(now)
        return True


RATE_LIMITER = RateLimiter()
