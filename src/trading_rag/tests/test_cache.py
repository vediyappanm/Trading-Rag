from datetime import datetime, timezone

from trading_rag.cache import build_cache_context, _bucket_time


def test_bucket_time_rounds_down():
    ts = datetime(2026, 3, 13, 10, 7, 45, tzinfo=timezone.utc)
    bucket = _bucket_time(ts)
    # default bucket is 300s; 10:07:45 -> 10:05:00
    assert bucket.endswith("00")


def test_build_cache_context_has_parts():
    ts = datetime(2026, 3, 13, 10, 7, 0, tzinfo=timezone.utc)
    ctx = build_cache_context("structured_esql", "BTC", ts)
    assert "path=structured_esql" in ctx
    assert "symbol=BTC" in ctx
    assert "bucket=" in ctx
