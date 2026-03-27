from datetime import datetime
from typing import Any
import json

from infra_rag.clients import es_client
from infra_rag.config import settings
from infra_rag.models import (
    RetrievedEvidence, LogEntry, QueryPath, QueryDomain, TimeWindow,
)


def execute_esql_query(esql: str, time_window: TimeWindow) -> dict[str, Any]:
    time_range = {
        "start": time_window.start.isoformat(),
        "end": time_window.end.isoformat(),
    }
    return es_client.execute_esql(esql, time_range)


def _parse_esql_result(result: dict[str, Any]) -> tuple[list[LogEntry], dict[str, Any]]:
    columns = result.get("columns", [])
    values = result.get("values", [])
    col_names = [c.get("name") for c in columns] if columns else []

    logs: list[LogEntry] = []
    aggregations: dict[str, Any] = {}

    # Log-style results (have _id and @timestamp)
    if "_id" in col_names and "@timestamp" in col_names:
        id_idx = col_names.index("_id")
        ts_idx = col_names.index("@timestamp")
        msg_field = next((f for f in ["message", "error.message", "log.message"] if f in col_names), None)
        msg_idx = col_names.index(msg_field) if msg_field else None
        host_field = next((f for f in ["host.name", "host.hostname", "source"] if f in col_names), None)
        host_idx = col_names.index(host_field) if host_field else None

        for row in values:
            val_id = str(row[id_idx])
            ts = row[ts_idx]
            timestamp = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

            message = ""
            if msg_idx is not None and msg_idx < len(row):
                message = str(row[msg_idx]) if row[msg_idx] else ""

            source = None
            if host_idx is not None and host_idx < len(row):
                source = row[host_idx]

            extra = {}
            skip = {id_idx, ts_idx, msg_idx, host_idx}
            for i, name in enumerate(col_names):
                if i not in skip:
                    extra[name] = row[i]

            logs.append(LogEntry(
                id=val_id,
                timestamp=timestamp,
                message=message,
                source=source,
                fields=extra,
            ))
        return logs, aggregations

    # Grouped aggregation results
    group_field = next(
        (f for f in [
            "host.name", "service.name", "container.name",
            "prometheus.job", "prometheus.instance",
            "log.level", "alertname",
        ] if f in col_names),
        None,
    )
    if group_field and len(values) > 0:
        grp_idx = col_names.index(group_field)
        by_group: dict[str, dict[str, Any]] = {}
        metric_cols = [i for i, n in enumerate(col_names) if n != group_field]
        for row in values:
            grp = str(row[grp_idx])
            by_group[grp] = {col_names[i]: row[i] for i in metric_cols}
        aggregations["by_group"] = by_group
        aggregations["group_field"] = group_field
        aggregations["group_count"] = len(by_group)
        if len(by_group) == 1:
            aggregations.update(next(iter(by_group.values())))
        return logs, aggregations

    # Flat aggregation (single row)
    for row in values[:1]:
        for idx, name in enumerate(col_names):
            aggregations[name] = row[idx]
    return logs, aggregations


def retrieve_with_esql_query(
    esql: str,
    time_window: TimeWindow,
    path: QueryPath = QueryPath.STRUCTURED_ESQL,
    domain: QueryDomain = QueryDomain.INFRA_METRICS,
) -> RetrievedEvidence:
    result = execute_esql_query(esql, time_window)
    logs, aggregations = _parse_esql_result(result)
    return RetrievedEvidence(
        logs=logs,
        aggregations=aggregations,
        query_used=esql,
        path=path,
        domain=domain,
    )


# ─── Infrastructure Metrics Retrieval ───


def retrieve_metrics(
    time_window: TimeWindow,
    target: str | None = None,
    metric_name: str | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = settings.api.max_log_limit
    index = settings.elasticsearch.metrics_index
    target_filter = f'AND host.name == "{target}"' if target else ""
    metric_filter = f'AND metric.name == "{metric_name}"' if metric_name else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {target_filter} {metric_filter}
| SORT @timestamp DESC
| LIMIT {limit}
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.METRIC_AGGREGATION,
        domain=QueryDomain.INFRA_METRICS,
    )


def retrieve_metric_aggregation(
    time_window: TimeWindow,
    target: str | None = None,
) -> RetrievedEvidence:
    index = settings.elasticsearch.metrics_index
    target_filter = f'AND host.name == "{target}"' if target else ""
    by_clause = "" if target else "BY host.name"

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {target_filter}
| STATS avg_cpu = AVG(cpu.usage_pct),
        max_cpu = MAX(cpu.usage_pct),
        avg_memory = AVG(memory.usage_pct),
        max_memory = MAX(memory.usage_pct),
        avg_disk = AVG(disk.usage_pct),
        max_disk = MAX(disk.usage_pct),
        avg_net_in = AVG(network.bytes_in),
        avg_net_out = AVG(network.bytes_out),
        data_points = COUNT() {by_clause}
| SORT avg_cpu DESC
| LIMIT 20
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.METRIC_AGGREGATION,
        domain=QueryDomain.INFRA_METRICS,
    )


# ─── Infrastructure Logs Retrieval ───


def retrieve_logs(
    time_window: TimeWindow,
    target: str | None = None,
    level: str | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = settings.api.max_log_limit
    index = settings.elasticsearch.logs_index
    target_filter = f'AND (host.name == "{target}" OR service.name == "{target}" OR container.name == "{target}")' if target else ""
    level_filter = f'AND log.level == "{level.upper()}"' if level else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {target_filter} {level_filter}
| SORT @timestamp DESC
| LIMIT {limit}
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.LOG_SEARCH,
        domain=QueryDomain.INFRA_LOGS,
    )


def retrieve_error_logs(
    time_window: TimeWindow,
    target: str | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = settings.api.max_log_limit
    index = settings.elasticsearch.logs_index
    target_filter = f'AND (host.name == "{target}" OR service.name == "{target}")' if target else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  AND log.level IN ("ERROR", "FATAL", "CRITICAL") {target_filter}
| SORT @timestamp DESC
| LIMIT {limit}
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.LOG_SEARCH,
        domain=QueryDomain.INFRA_LOGS,
    )


def retrieve_log_aggregation(
    time_window: TimeWindow,
    target: str | None = None,
) -> RetrievedEvidence:
    index = settings.elasticsearch.logs_index
    target_filter = f'AND (host.name == "{target}" OR service.name == "{target}")' if target else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {target_filter}
| STATS total_logs = COUNT(),
        errors = SUM(CASE(log.level == "ERROR", 1, 0)),
        warnings = SUM(CASE(log.level == "WARN", 1, 0)),
        fatals = SUM(CASE(log.level == "FATAL", 1, 0)),
        infos = SUM(CASE(log.level == "INFO", 1, 0)) BY service.name
| SORT errors DESC
| LIMIT 20
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.LOG_SEARCH,
        domain=QueryDomain.INFRA_LOGS,
    )


# ─── Traces Retrieval ───


def retrieve_traces(
    time_window: TimeWindow,
    service: str | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = min(50, settings.api.max_log_limit)
    index = settings.elasticsearch.traces_index
    service_filter = f'AND service.name == "{service}"' if service else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {service_filter}
| SORT @timestamp DESC
| LIMIT {limit}
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.TRACE_SEARCH,
        domain=QueryDomain.INFRA_TRACES,
    )


def retrieve_trace_latency(
    time_window: TimeWindow,
    service: str | None = None,
) -> RetrievedEvidence:
    index = settings.elasticsearch.traces_index
    service_filter = f'AND service.name == "{service}"' if service else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {service_filter}
| STATS avg_duration_ms = AVG(duration_ms),
        p95_duration_ms = PERCENTILE(duration_ms, 95),
        p99_duration_ms = PERCENTILE(duration_ms, 99),
        total_spans = COUNT(),
        error_spans = SUM(CASE(status.code == "ERROR", 1, 0)) BY service.name
| SORT avg_duration_ms DESC
| LIMIT 20
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.TRACE_SEARCH,
        domain=QueryDomain.INFRA_TRACES,
    )


# ─── Alerts Retrieval ───


def retrieve_alerts(
    time_window: TimeWindow,
    target: str | None = None,
    severity: str | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = settings.api.max_log_limit
    index = settings.elasticsearch.alerts_index
    target_filter = f'AND (instance LIKE "*{target}*" OR host.name == "{target}")' if target else ""
    severity_filter = f'AND severity == "{severity}"' if severity else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {target_filter} {severity_filter}
| SORT @timestamp DESC
| LIMIT {limit}
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.ALERT_SEARCH,
        domain=QueryDomain.INFRA_ALERTS,
    )


def retrieve_alert_summary(
    time_window: TimeWindow,
) -> RetrievedEvidence:
    index = settings.elasticsearch.alerts_index

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
| STATS total_alerts = COUNT(),
        critical = SUM(CASE(severity == "critical", 1, 0)),
        warning = SUM(CASE(severity == "warning", 1, 0)),
        info = SUM(CASE(severity == "info", 1, 0)) BY alertname
| SORT critical DESC, total_alerts DESC
| LIMIT 20
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.ALERT_SEARCH,
        domain=QueryDomain.INFRA_ALERTS,
    )


# ─── Network / SNMP Retrieval ───


def retrieve_network_metrics(
    time_window: TimeWindow,
    target: str | None = None,
) -> RetrievedEvidence:
    index = settings.elasticsearch.network_index
    target_filter = f'AND (host.name == "{target}" OR device.name == "{target}")' if target else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {target_filter}
| STATS avg_if_in = AVG(interface.bytes_in),
        avg_if_out = AVG(interface.bytes_out),
        max_if_in = MAX(interface.bytes_in),
        max_if_out = MAX(interface.bytes_out),
        avg_cpu = AVG(device.cpu_pct),
        avg_memory = AVG(device.memory_pct),
        data_points = COUNT() BY host.name
| SORT avg_if_in DESC
| LIMIT 20
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.NETWORK_QUERY,
        domain=QueryDomain.INFRA_NETWORK,
    )


# ─── Blackbox / Uptime Retrieval ───


def retrieve_uptime_probes(
    time_window: TimeWindow,
    target: str | None = None,
) -> RetrievedEvidence:
    index = settings.elasticsearch.blackbox_index
    target_filter = f'AND probe.target LIKE "*{target}*"' if target else ""

    esql = f"""
FROM "{index}"
| WHERE @timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"
  {target_filter}
| STATS avg_latency_ms = AVG(probe.duration_ms),
        p95_latency_ms = PERCENTILE(probe.duration_ms, 95),
        success_rate = AVG(CASE(probe.success == true, 1.0, 0.0)),
        total_probes = COUNT() BY probe.target
| SORT success_rate ASC
| LIMIT 20
"""
    try:
        result = execute_esql_query(esql, time_window)
        logs, aggs = _parse_esql_result(result)
    except Exception:
        logs, aggs = [], {}

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=esql,
        path=QueryPath.STRUCTURED_ESQL,
        domain=QueryDomain.INFRA_UPTIME,
    )


# ─── Semantic / Full-text Search ───


def semantic_search_logs(
    query: str,
    time_window: TimeWindow | None = None,
    limit: int | None = None,
) -> RetrievedEvidence:
    if limit is None:
        limit = min(20, settings.api.max_log_limit)
    index = settings.elasticsearch.logs_index

    filters: list[dict] = []
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
                    {"match": {"message": {"query": query, "boost": 2}}},
                    {"match": {"error.message": {"query": query, "boost": 3}}},
                    {"match": {"service.name": {"query": query, "boost": 1.5}}},
                    {"match": {"host.name": {"query": query, "boost": 1.5}}},
                ],
                "filter": filters,
                "minimum_should_match": 1,
            }
        },
        "sort": [{"@timestamp": {"order": "desc"}}],
    }

    try:
        result = es_client.search(index, search_body)
    except Exception:
        return RetrievedEvidence(
            logs=[], aggregations={},
            query_used=f"search: {query}",
            path=QueryPath.LOG_SEARCH,
            domain=QueryDomain.INFRA_LOGS,
        )

    logs = []
    for hit in result.get("hits", {}).get("hits", []):
        source = hit.get("_source", {})
        logs.append(LogEntry(
            id=hit.get("_id", ""),
            timestamp=source.get("@timestamp", datetime.utcnow().isoformat()),
            message=source.get("message", ""),
            source=source.get("host", {}).get("name") or source.get("service", {}).get("name"),
            fields=source,
        ))

    return RetrievedEvidence(
        logs=logs,
        aggregations={},
        query_used=f"search: {query}",
        path=QueryPath.LOG_SEARCH,
        domain=QueryDomain.INFRA_LOGS,
    )


# ─── Cross-domain correlation ───


def cross_domain_search(
    time_window: TimeWindow,
    target: str | None = None,
) -> RetrievedEvidence:
    """Retrieve data across metrics + logs + alerts for a target host/service."""
    all_logs: list[LogEntry] = []
    all_aggs: dict[str, Any] = {}

    # Metrics
    try:
        metrics = retrieve_metric_aggregation(time_window, target)
        all_aggs["metrics"] = metrics.aggregations
    except Exception:
        all_aggs["metrics"] = {}

    # Error logs
    try:
        errors = retrieve_error_logs(time_window, target, limit=10)
        all_logs.extend(errors.logs)
        all_aggs["error_count"] = len(errors.logs)
    except Exception:
        all_aggs["error_count"] = 0

    # Alerts
    try:
        alerts = retrieve_alerts(time_window, target, limit=10)
        all_logs.extend(alerts.logs)
        all_aggs["alert_count"] = len(alerts.logs)
    except Exception:
        all_aggs["alert_count"] = 0

    all_logs.sort(key=lambda x: x.timestamp, reverse=True)

    return RetrievedEvidence(
        logs=all_logs[:30],
        aggregations=all_aggs,
        query_used=f"cross-domain: target={target}",
        path=QueryPath.CROSS_INDEX,
        domain=QueryDomain.CROSS_DOMAIN,
    )
