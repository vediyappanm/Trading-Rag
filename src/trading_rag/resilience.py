import time
import threading


class CircuitBreaker:
    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        reset_timeout_s: int = 30,
    ) -> None:
        self._name = name
        self._failure_threshold = max(1, failure_threshold)
        self._reset_timeout_s = max(1, reset_timeout_s)
        self._lock = threading.Lock()
        self._failures = 0
        self._open_until = 0.0

    def allow(self) -> bool:
        with self._lock:
            if self._open_until <= 0:
                return True
            if time.time() >= self._open_until:
                self._open_until = 0.0
                self._failures = 0
                return True
            return False

    def record_success(self) -> None:
        with self._lock:
            self._failures = 0
            self._open_until = 0.0

    def record_failure(self) -> None:
        with self._lock:
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open_until = time.time() + self._reset_timeout_s

    @property
    def name(self) -> str:
        return self._name
