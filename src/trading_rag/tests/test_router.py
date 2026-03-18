from datetime import datetime, timedelta

from trading_rag.router.agent import parse_time_from_query, extract_symbol


def test_parse_time_absolute_range_to():
    now = datetime(2026, 3, 13, 12, 0, 0)
    tw = parse_time_from_query("show me data from 2026-01-20 to 2026-01-21", now)
    assert tw is not None
    assert tw.start == datetime(2026, 1, 20)
    assert tw.end == datetime(2026, 1, 21)


def test_parse_time_absolute_range_dash():
    now = datetime(2026, 3, 13, 12, 0, 0)
    tw = parse_time_from_query("data 2026-01-01 - 2026-01-31", now)
    assert tw is not None
    assert tw.start == datetime(2026, 1, 1)
    assert tw.end == datetime(2026, 1, 31)


def test_parse_time_no_time_mentioned():
    now = datetime(2026, 3, 13, 12, 0, 0)
    # No time → returns None, caller applies 365-day default
    tw = parse_time_from_query("what is the fill rate", now)
    assert tw is None


def test_parse_time_relative_patterns_return_none():
    # Relative patterns are no longer supported — data is historical (Jan 2026)
    now = datetime(2026, 3, 13, 12, 0, 0)
    assert parse_time_from_query("last 3 hours", now) is None
    assert parse_time_from_query("today", now) is None
    assert parse_time_from_query("yesterday", now) is None
    assert parse_time_from_query("last 24 hours", now) is None


def test_extract_symbol_prefers_ticker():
    symbol = extract_symbol("Show BTC trades for last hour")
    assert symbol == "BTC"


def test_extract_symbol_ignores_common_words():
    symbol = extract_symbol("Show AVG latency for last hour")
    assert symbol is None


def test_extract_symbol_ignores_query_words():
    # Common question words must not be extracted as symbols
    assert extract_symbol("which brokers have the most rejected orders") is None
    assert extract_symbol("what is the overall fill rate") is None
    assert extract_symbol("how many orders are open") is None
    assert extract_symbol("show top symbols by volume") is None


def test_extract_symbol_nifty():
    symbol = extract_symbol("How many NIFTY orders were placed")
    assert symbol == "NIFTY"


def test_extract_symbol_reliance():
    symbol = extract_symbol("Show RELIANCE orders")
    assert symbol == "RELIANCE"
