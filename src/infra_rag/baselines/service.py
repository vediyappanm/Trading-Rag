from datetime import datetime, timedelta
from typing import Any

from infra_rag.clients import es_client, redis_client
from infra_rag.models import BaselineStats
from infra_rag.config import settings


def get_baseline(target: str | None, hour: int) -> BaselineStats | None:
    cached = redis_client.get_baseline(target, hour)
    if cached:
        cached.pop("source", None)
        return BaselineStats(**cached, source="redis")
    return fetch_baseline_from_es(target, hour)


def fetch_baseline_from_es(target: str | None, hour: int) -> BaselineStats | None:
    now = datetime.utcnow()
    lookback = now - timedelta(days=7)
    index = settings.elasticsearch.metrics_index
    target_filter = f'AND host.name == "{target}"' if target else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{lookback.isoformat()}" AND @timestamp <= "{now.isoformat()}"
  {target_filter}
| STATS avg_cpu = AVG(cpu.usage_pct),
        avg_memory = AVG(memory.usage_pct),
        avg_disk = AVG(disk.usage_pct),
        avg_net_in = AVG(network.bytes_in),
        avg_net_out = AVG(network.bytes_out),
        data_points = COUNT()
| LIMIT 1
"""

    try:
        result = es_client.execute_esql(esql, {"start": lookback.isoformat(), "end": now.isoformat()})
        if result.get("values") and len(result["values"]) > 0:
            columns = result.get("columns", [])
            col_names = [c.get("name") for c in columns]
            row = result["values"][0]
            row_dict = {col_names[i]: row[i] for i in range(len(col_names))}

            baseline = BaselineStats(
                target=target,
                hour=hour,
                avg_cpu_pct=row_dict.get("avg_cpu"),
                avg_memory_pct=row_dict.get("avg_memory"),
                avg_disk_usage_pct=row_dict.get("avg_disk"),
                avg_latency_ms=None,
                avg_error_rate=None,
                source="elasticsearch",
            )
            redis_client.set_baseline(target, hour, baseline.model_dump())
            return baseline
    except Exception:
        pass

    return None


def compute_baseline(target: str | None, hour: int) -> BaselineStats | None:
    baseline = fetch_baseline_from_es(target, hour)
    if baseline:
        redis_client.set_baseline(target, hour, baseline.model_dump())
    return baseline


def get_or_compute_baseline(target: str | None, hour: int | None = None) -> BaselineStats | None:
    if hour is None:
        hour = datetime.utcnow().hour

    baseline = get_baseline(target, hour)
    if baseline is None:
        baseline = compute_baseline(target, hour)
    return baseline


def get_default_baseline() -> BaselineStats:
    return BaselineStats(
        target=None,
        hour=datetime.utcnow().hour,
        avg_cpu_pct=40.0,
        avg_memory_pct=60.0,
        avg_disk_usage_pct=50.0,
        avg_latency_ms=100.0,
        avg_error_rate=0.01,
        avg_request_rate=500.0,
        p95_latency_ms=250.0,
        source="default",
    )
