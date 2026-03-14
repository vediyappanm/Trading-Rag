from datetime import datetime, timedelta

from trading_rag.router.agent import parse_time_from_query, extract_symbol


def test_parse_time_last_hours():
    now = datetime(2026, 3, 13, 12, 0, 0)
    tw = parse_time_from_query("last 3 hours", now)
    assert tw is not None
    assert tw.start == now - timedelta(hours=3)
    assert tw.end == now


def test_parse_time_today():
    now = datetime(2026, 3, 13, 15, 45, 0)
    tw = parse_time_from_query("today", now)
    assert tw is not None
    assert tw.start == now.replace(hour=0, minute=0, second=0, microsecond=0)
    assert tw.end == now


def test_extract_symbol_prefers_ticker():
    symbol = extract_symbol("Show BTC trades for last hour")
    assert symbol == "BTC"


def test_extract_symbol_ignores_common_words():
    symbol = extract_symbol("Show AVG latency for last hour")
    assert symbol is None
