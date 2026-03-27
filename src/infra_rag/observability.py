import threading
import time
from collections import defaultdict


class MetricsStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[str, int] = defaultdict(int)
        self._timings: dict[str, list[float]] = defaultdict(list)

    def inc(self, name: str, value: int = 1) -> None:
        with self._lock:
            self._counters[name] += value

    def observe_ms(self, name: str, value_ms: float) -> None:
        with self._lock:
            self._timings[name].append(value_ms)

    def snapshot(self) -> dict[str, dict[str, float]]:
        with self._lock:
            counters = dict(self._counters)
            timings = {k: list(v) for k, v in self._timings.items()}

        summary: dict[str, dict[str, float]] = {}
        for name, values in timings.items():
            if not values:
                continue
            values_sorted = sorted(values)
            count = len(values_sorted)
            p50 = values_sorted[int(count * 0.50) - 1]
            p95 = values_sorted[max(int(count * 0.95) - 1, 0)]
            p99 = values_sorted[max(int(count * 0.99) - 1, 0)]
            summary[name] = {
                "count": count,
                "p50_ms": p50,
                "p95_ms": p95,
                "p99_ms": p99,
                "avg_ms": sum(values_sorted) / count,
            }

        return {"counters": counters, "timings": summary}

    def recent_timing_values(self, name: str, limit: int = 20) -> list[float]:
        with self._lock:
            values = list(self._timings.get(name, []))
        if limit <= 0:
            return values
        return values[-limit:]

    def to_text(self) -> str:
        snap = self.snapshot()
        lines: list[str] = []
        for name, value in snap["counters"].items():
            lines.append(f"counter.{name} {value}")
        for name, stats in snap["timings"].items():
            lines.append(f"timing.{name}.count {int(stats['count'])}")
            lines.append(f"timing.{name}.p50_ms {stats['p50_ms']:.2f}")
            lines.append(f"timing.{name}.p95_ms {stats['p95_ms']:.2f}")
            lines.append(f"timing.{name}.p99_ms {stats['p99_ms']:.2f}")
            lines.append(f"timing.{name}.avg_ms {stats['avg_ms']:.2f}")
        return "\n".join(lines)


METRICS = MetricsStore()


class Timer:
    def __init__(self, name: str) -> None:
        self._name = name
        self._start = 0.0

    def __enter__(self) -> "Timer":
        self._start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        elapsed_ms = (time.perf_counter() - self._start) * 1000
        METRICS.observe_ms(self._name, elapsed_ms)
