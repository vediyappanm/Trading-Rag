from datetime import datetime
from typing import Any
import json

from trading_rag.clients import es_client
from trading_rag.config import settings
from trading_rag.models import RetrievedEvidence, LogEntry, QueryPath, TimeWindow


def execute_esql_query(esql: str, time_window: TimeWindow) -> dict[str, Any]:
    time_range = {
        "start": time_window.start.isoformat(),
        "end": time_window.end.isoformat(),
    }
    result = es_client.execute_esql(esql, time_range)
    return result


def _parse_esql_result(result: dict[str, Any]) -> tuple[list[LogEntry], dict[str, Any]]:
    columns = result.get("columns", [])
    values = result.get("values", [])
    col_names = [c.get("name") for c in columns] if columns else []

    logs: list[LogEntry] = []
    aggregations: dict[str, Any] = {}

    if "_id" in col_names and "@timestamp" in col_names:
        id_idx = col_names.index("_id")
        ts_idx = col_names.index("@timestamp")
        # Handle both old 'message' and new Noren field names
        msg_field = next((f for f in ["message", "description", "OrdRemarks"] if f in col_names), None)
        msg_idx = col_names.index(msg_field) if msg_field else None
        # Handle both old 'symbol' and new Noren fields
        sym_field = next((f for f in ["ticker", "TradingSymbol", "symbol"] if f in col_names), None)
        sym_idx = col_names.index(sym_field) if sym_field else None

        for row in values:
            val_id = str(row[id_idx])
            ts = row[ts_idx]
            # Ensure timestamp is a string for Pydantic if it's not already handled
            timestamp = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            
            # Map fields safely
            message = ""
            if msg_idx is not None and msg_idx < len(row):
                message = str(row[msg_idx])
            
            symbol = None
            if sym_idx is not None and sym_idx < len(row):
                symbol = row[sym_idx]

            # Collect any extra fields
            extra = {}
            for i, name in enumerate(col_names):
                if i not in (id_idx, ts_idx, msg_idx, sym_idx):
                    extra[name] = row[i]

            logs.append(LogEntry(
                id=val_id,
                timestamp=timestamp,
                message=message,
                symbol=symbol,
                fields=extra
            ))
        return logs, aggregations

    # Aggregation style: handle by-symbol or by-exchange metrics
    group_field = next((f for f in ["ticker", "TradingSymbol", "ExchSeg", "BrokerId", "symbol"] if f in col_names), None)
    if group_field and len(values) > 0:
        grp_idx = col_names.index(group_field)
        by_group: dict[str, dict[str, Any]] = {}
        metric_cols = [i for i, n in enumerate(col_names) if n != group_field]
        for row in values:
            grp = str(row[grp_idx])
            by_group[grp] = {col_names[i]: row[i] for i in metric_cols}
        aggregations["by_symbol"] = by_group
        aggregations["symbol_comparison_count"] = len(by_group)
        aggregations["detected_symbols"] = list(by_group.keys())
        # Populate flat metrics from first group for single-symbol queries
        if len(by_group) == 1:
            first_metrics = next(iter(by_group.values()))
            aggregations.update(_normalize_metrics(first_metrics))
        return logs, aggregations

    # Aggregation style: convert columns to dict per row
    for row in values[:1]:
        for idx, name in enumerate(col_names):
            aggregations[name] = row[idx]
    return logs, aggregations


def retrieve_with_esql_query(
    esql: str,
    time_window: TimeWindow,
    path: QueryPath = QueryPath.STRUCTURED_ESQL,
) -> RetrievedEvidence:
    result = execute_esql_query(esql, time_window)
    logs, aggregations = _parse_esql_result(result)
    return RetrievedEvidence(
        logs=logs,
        aggregations=aggregations,
        query_used=esql,
        path=path,
    )


def _normalize_metrics(raw: dict[str, Any]) -> dict[str, Any]:
    """Map Noren real field names to the keys analysis/baseline code expects."""
    return {
        "avg_latency_ms": raw.get("avg_qty", 0),       # repurposed: avg order quantity
        "avg_volume": raw.get("total_orders", 0),       # repurposed: total orders
        "total_volume": raw.get("total_qty", 0),        # total quantity traded
        "total_count": raw.get("total_orders", 0),
        "error_rate": 1.0 - (raw.get("fill_rate") or 1.0),  # non-fill rate
        "fill_rate": raw.get("fill_rate", 0),
        "buy_orders": raw.get("buy_orders", 0),
        "sell_orders": raw.get("sell_orders", 0),
        "cancel_rate": raw.get("cancel_rate", 0),
    }


_FIELD_CAPS_CACHE: dict[str, set[str]] = {}


def _safe_keep_fields(index: str) -> list[str]:
    if index in _FIELD_CAPS_CACHE:
        fields = _FIELD_CAPS_CACHE[index]
    else:
        try:
            caps = es_client.client.field_caps(index=index, fields="*")
            fields = set(caps.get("fields", {}).keys())
        except Exception:
            fields = set()
        _FIELD_CAPS_CACHE[index] = fields
    preferred = settings.elasticsearch.esql_keep_fields
    return [f for f in preferred if f in fields or f.startswith("@")]


def retrieve_execution_logs(
    time_window: TimeWindow,
    symbol: str | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = settings.api.max_log_limit
    if limit <= 0:
        limit = 1
    symbol_filter = f'| WHERE ticker == "{symbol.upper()}" OR TradingSymbol == "{symbol.upper()}"' if symbol else ""
    keep_fields = ", ".join(_safe_keep_fields(es_client.get_execution_logs_index()))
    keep_clause = f"\n    | KEEP {keep_fields}" if keep_fields else ""
    esql = f"""
    FROM "{es_client.get_execution_logs_index()}"
    | WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
    {symbol_filter}
    | SORT @timestamp DESC
    | LIMIT {limit}
    {keep_clause}
    """
    
    result = execute_esql_query(esql, time_window)
    logs, _ = _parse_esql_result(result)
    
    return RetrievedEvidence(
        logs=logs,
        aggregations={},
        query_used=esql,
        path=QueryPath.STRUCTURED_ESQL,
    )


def retrieve_feed_logs(
    time_window: TimeWindow,
    symbol: str | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = settings.api.max_log_limit
    if limit <= 0:
        limit = 1
    symbol_filter = f'| WHERE symbol == "{symbol}"' if symbol else ""
    keep_fields = ", ".join(_safe_keep_fields(es_client.get_feed_logs_index()))
    keep_clause = f"\n    | KEEP {keep_fields}" if keep_fields else ""
    esql = f"""
    FROM "{es_client.get_feed_logs_index()}"
    | WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
    {symbol_filter}
    | SORT @timestamp DESC
    | LIMIT {limit}
    {keep_clause}
    """
    
    try:
        result = execute_esql_query(esql, time_window)
        logs, _ = _parse_esql_result(result)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Error executing feed logs query: {e}")
        logs = []
    
    return RetrievedEvidence(
        logs=logs,
        aggregations={},
        query_used=esql,
        path=QueryPath.STRUCTURED_ESQL,
    )


def retrieve_with_aggregation(
    time_window: TimeWindow,
    symbol: str | None = None,
) -> RetrievedEvidence:
    base_filter = f'@timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"'
    symbol_filter = f'AND (ticker == "{symbol.upper()}" OR STARTS_WITH(TradingSymbol, "{symbol.upper()}"))' if symbol else ""
    by_clause = "BY ticker" if not symbol else ""

    esql = f"""
    FROM "{es_client.get_execution_logs_index()}"
    | WHERE {base_filter} {symbol_filter} AND msg_type == "ordupd"
    | STATS total_orders = COUNT(),
            avg_qty = AVG(QtyToFill),
            total_qty = SUM(QtyToFill),
            fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)),
            cancel_rate = AVG(CASE(OrdStatus == 67, 1.0, 0.0)),
            buy_orders = SUM(CASE(TransType == "B", 1, 0)),
            sell_orders = SUM(CASE(TransType == "S", 1, 0))
      {by_clause}
    | SORT total_orders DESC
    | LIMIT 20
    """

    result = execute_esql_query(esql, time_window)
    columns = result.get("columns", [])
    col_names = [c.get("name") for c in columns]
    rows = result.get("values", [])

    aggregations: dict[str, Any] = {}

    if rows and col_names:
        # Find group-by column (ticker or TradingSymbol)
        group_col = next((c for c in ["ticker", "TradingSymbol", "ExchSeg", "BrokerId"] if c in col_names), None)
        if group_col and len(rows) > 1:
            grp_idx = col_names.index(group_col)
            by_sym: dict[str, dict[str, Any]] = {}
            for row in rows:
                sym_key = str(row[grp_idx])
                metrics = {col_names[i]: row[i] for i in range(len(col_names)) if i != grp_idx}
                by_sym[sym_key] = metrics
                by_sym[sym_key].update(_normalize_metrics(metrics))
            aggregations["by_symbol"] = by_sym
            aggregations["detected_symbols"] = list(by_sym.keys())
            aggregations["symbol_comparison_count"] = len(by_sym)
        else:
            # Single aggregation row (no BY clause)
            row = rows[0]
            raw = {col_names[i]: row[i] for i in range(len(col_names))}
            aggregations.update(raw)
            aggregations.update(_normalize_metrics(raw))

    return RetrievedEvidence(
        logs=[],
        aggregations=aggregations,
        query_used=esql,
        path=QueryPath.STRUCTURED_ESQL,
    )


def correlate_execution_and_feed(
    time_window: TimeWindow,
    symbol: str | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = settings.api.max_log_limit
    if limit <= 0:
        limit = 1
    execution_evidence = retrieve_execution_logs(time_window, symbol, limit)
    feed_evidence = retrieve_feed_logs(time_window, symbol, limit)
    
    all_logs = execution_evidence.logs + feed_evidence.logs
    all_logs.sort(key=lambda x: x.timestamp, reverse=True)
    
    symbol_filter = f'| WHERE symbol == "{symbol}"' if symbol else ""
    combined_esql = f"""
    FROM "{es_client.get_execution_logs_index()}", "{es_client.get_feed_logs_index()}"
    | WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
    {symbol_filter}
    | SORT @timestamp DESC
    | LIMIT {limit * 2}
    """
    
    return RetrievedEvidence(
        logs=all_logs[:limit],
        aggregations={},
        query_used=combined_esql,
        path=QueryPath.DUAL_INDEX_CORRELATION,
    )


def semantic_search_incidents(
    query: str,
    time_window: TimeWindow | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = min(10, settings.api.max_log_limit)
    if limit <= 0:
        limit = 1
    # Search across TradingSymbol, BrokerId, AcctId, and ticker fields in real Noren data
    filters: list[dict] = [{"term": {"msg_type": "ordupd"}}]
    if time_window:
        filters.append({
            "range": {
                "@timestamp": {
                    "gte": time_window.start.isoformat(),
                    "lte": time_window.end.isoformat(),
                }
            }
        })
    search_body = {
        "size": limit,
        "query": {
            "bool": {
                "should": [
                    {"match": {"TradingSymbol": {"query": query, "boost": 2}}},
                    {"match": {"ticker": {"query": query, "boost": 3}}},
                    {"term": {"BrokerId": query.upper()}},
                    {"term": {"AcctId": query.upper()}},
                ],
                "filter": filters,
                "minimum_should_match": 1,
            }
        },
        "sort": [{"@timestamp": {"order": "desc"}}],
    }

    result = es_client.search(es_client.get_execution_logs_index(), search_body)

    logs = []
    for hit in result.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        logs.append(LogEntry(
            id=hit.get("_id", ""),
            timestamp=source.get("@timestamp", datetime.utcnow().isoformat()),
            message=f"{source.get('TradingSymbol','')} {source.get('TransType','')} {source.get('QtyToFill','')} @ {source.get('PriceToFill',0)/100:.2f}",
            symbol=source.get("ticker") or source.get("TradingSymbol"),
            fields=source,
        ))

    return RetrievedEvidence(
        logs=logs,
        aggregations={},
        query_used=f"search: {query}",
        path=QueryPath.SEMANTIC_INCIDENT,
    )
