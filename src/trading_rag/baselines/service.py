from datetime import datetime, timedelta
from typing import Any

from trading_rag.clients import es_client, redis_client
from trading_rag.models import BaselineStats


def get_baseline(symbol: str | None, hour: int) -> BaselineStats | None:
    cached = redis_client.get_baseline(symbol, hour)
    if cached:
        # If 'source' is in cached, it will be overridden if passed as keyword
        # To be safe, remove it from dict first if we want to force "redis"
        cached.pop("source", None)
        return BaselineStats(**cached, source="redis")
    
    return fetch_baseline_from_es(symbol, hour)


def fetch_baseline_from_es(symbol: str | None, hour: int) -> BaselineStats | None:
    # Use last 365 days to cover all ingested historical data (e.g. Jan 2026 data)
    now = datetime.utcnow()
    one_year_ago = now - timedelta(days=365)

    symbol_filter = f'AND (ticker == "{symbol.upper()}" OR TradingSymbol == "{symbol.upper()}")' if symbol else ""

    esql = f"""
    FROM "{es_client.get_execution_logs_index()}"
    | WHERE @timestamp >= "{one_year_ago.isoformat()}"
      AND @timestamp <= "{now.isoformat()}"
      AND msg_type == "ordupd"
      {symbol_filter}
    | STATS avg_qty = AVG(QtyToFill),
            total_orders = COUNT(),
            fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)),
            p95_qty = PERCENTILE(QtyToFill, 95)
    | LIMIT 1
    """

    try:
        result = es_client.execute_esql(esql, {"start": one_year_ago.isoformat(), "end": now.isoformat()})

        if result.get("values") and len(result["values"]) > 0:
            columns = result.get("columns", [])
            col_names = [c.get("name") for c in columns]
            row = result["values"][0]
            row_dict = {col_names[i]: row[i] for i in range(len(col_names))}

            baseline_data = {
                "symbol": symbol,
                "hour": hour,
                # Repurpose model fields: avg_latency_ms=avg_qty, avg_volume=total_orders,
                # error_rate=non-fill rate, p95_latency_ms=p95_qty
                "avg_latency_ms": row_dict.get("avg_qty"),
                "avg_volume": row_dict.get("total_orders"),
                "error_rate": 1.0 - (row_dict.get("fill_rate") or 1.0),
                "p95_latency_ms": row_dict.get("p95_qty"),
                "source": "elasticsearch"
            }
            baseline = BaselineStats(**baseline_data)
            redis_client.set_baseline(symbol, hour, baseline.model_dump())
            return baseline
    except Exception:
        pass

    return None


def compute_baseline(symbol: str | None, hour: int) -> BaselineStats | None:
    baseline = fetch_baseline_from_es(symbol, hour)
    if baseline:
        redis_client.set_baseline(symbol, hour, baseline.model_dump())
    return baseline


def get_or_compute_baseline(symbol: str | None, hour: int | None = None) -> BaselineStats | None:
    if hour is None:
        hour = datetime.utcnow().hour
    
    baseline = get_baseline(symbol, hour)
    if baseline is None:
        baseline = compute_baseline(symbol, hour)
    
    return baseline


def get_default_baseline() -> BaselineStats:
    return BaselineStats(
        symbol=None,
        hour=datetime.utcnow().hour,
        avg_latency_ms=100.0,
        avg_volume=1000.0,
        error_rate=0.01,
        p95_latency_ms=200.0,
        source="default",
    )
