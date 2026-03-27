from datetime import datetime, timezone

from infra_rag.cache import build_cache_context, _bucket_time


def test_bucket_time_rounds_down():
    ts = datetime(2026, 3, 13, 10, 7, 45, tzinfo=timezone.utc)
    bucket = _bucket_time(ts)
    # default bucket is 300s; 10:07:45 -> 10:05:00
    assert bucket.endswith("00")


def test_build_cache_context_has_parts():
    ts = datetime(2026, 3, 13, 10, 7, 0, tzinfo=timezone.utc)
    ctx = build_cache_context("metric_aggregation", "web-01", ts)
    assert "path=metric_aggregation" in ctx
    assert "symbol=web-01" in ctx
    assert "bucket=" in ctx
