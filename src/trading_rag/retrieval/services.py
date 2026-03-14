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
        msg_idx = col_names.index("message") if "message" in col_names else None
        sym_idx = col_names.index("symbol") if "symbol" in col_names else None

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

    # Aggregation style: handle by-symbol metrics
    if "symbol" in col_names and len(values) > 0:
        sym_idx = col_names.index("symbol")
        by_symbol: dict[str, dict[str, Any]] = {}
        metric_cols = [i for i, n in enumerate(col_names) if n != "symbol"]
        for row in values:
            sym = str(row[sym_idx])
            by_symbol[sym] = {col_names[i]: row[i] for i in metric_cols}
        aggregations["by_symbol"] = by_symbol
        aggregations["symbol_comparison_count"] = len(by_symbol)
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
    symbol_filter = f'| WHERE symbol == "{symbol}"' if symbol else ""
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
    
    result = execute_esql_query(esql, time_window)
    logs, _ = _parse_esql_result(result)
    
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
    symbol_filter = f'| WHERE symbol == "{symbol}"' if symbol else ""
    
    # Use BY symbol to allow comparisons even if one symbol is passed
    esql = f"""
    FROM "{es_client.get_execution_logs_index()}"
    | WHERE {base_filter}
    {symbol_filter}
    | EVAL is_error = CASE(status == "error", 1, 0)
    | STATS avg_latency_ms = AVG(latency_ms), 
            avg_volume = AVG(volume),
            total_volume = SUM(volume),
            total_count = COUNT(),
            error_count = SUM(is_error)
      BY symbol
    | EVAL error_rate = error_count * 1.0 / total_count
    """
    
    result = execute_esql_query(esql, time_window)
    
    aggregations = {}
    rows = result.get("values", [])
    
    if rows:
        # Structure the aggregations by symbol for the analysis agent
        for row in rows:
            if len(row) >= 7:
                sym = str(row[0])
                aggregations[f"{sym}_metrics"] = {
                    "avg_latency_ms": row[1],
                    "avg_volume": row[2],
                    "total_volume": row[3],
                    "total_count": row[4],
                    "error_count": row[5],
                    "error_rate": row[6],
                }
        
        # Also provide a flattened summary for legacy parts of the analysis agent
        # (Using the first row as the 'primary' or averaging multiple)
        if len(rows) == 1:
            aggregations.update(aggregations[f"{rows[0][0]}_metrics"])
        else:
            # Multi-symbol summary
            aggregations["symbol_comparison_count"] = len(rows)
            aggregations["detected_symbols"] = [str(r[0]) for r in rows]
    
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
    search_body = {
        "query": {
            "bool": {
                "must": [
                    {"match": {"description": query}}
                ],
                "filter": []
            }
        }
    }
    
    if time_window:
        search_body["query"]["bool"]["filter"].append({
            "range": {
                "@timestamp": {
                    "gte": time_window.start.isoformat(),
                    "lte": time_window.end.isoformat()
                }
            }
        })
    
    result = es_client.search(es_client.get_incidents_index(), search_body)
    
    logs = []
    for hit in result.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        logs.append(LogEntry(
            id=hit.get("_id", ""),
            timestamp=source.get("@timestamp", datetime.utcnow().isoformat()),
            message=source.get("description", ""),
            symbol=source.get("symbol"),
            fields=source,
        ))
    
    return RetrievedEvidence(
        logs=logs,
        aggregations={},
        query_used=f"semantic search: {query}",
        path=QueryPath.SEMANTIC_INCIDENT,
    )
