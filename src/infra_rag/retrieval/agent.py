import logging
from datetime import datetime
from typing import Any

from infra_rag.models import (
    RetrievedEvidence, LogEntry, QueryPath, QueryDomain, DataSource,
    TimeWindow, QueryType, GrafanaDashboardInfo,
)
from infra_rag.retrieval.services import (
    retrieve_with_esql_query,
    retrieve_metrics,
    retrieve_metric_aggregation,
    retrieve_logs,
    retrieve_error_logs,
    retrieve_log_aggregation,
    retrieve_traces,
    retrieve_trace_latency,
    retrieve_alerts,
    retrieve_alert_summary,
    retrieve_network_metrics,
    retrieve_uptime_probes,
    semantic_search_logs,
    cross_domain_search,
)

logger = logging.getLogger(__name__)


def retrieve_evidence(
    query_path: QueryPath,
    query_type: QueryType | None,
    query: str,
    time_window: TimeWindow,
    target: str | None = None,
    esql_query: str | None = None,
    promql_query: str | None = None,
    domain: QueryDomain = QueryDomain.INFRA_METRICS,
    data_source: DataSource = DataSource.ELASTICSEARCH,
) -> RetrievedEvidence:

    # Prefer the explicit live path even if an LLM router also produced ES|QL.
    if query_path == QueryPath.PROMETHEUS_LIVE and promql_query:
        return _retrieve_from_prometheus(promql_query, query_type, time_window, target)

    # ── Grafana dashboard search ──
    if query_path == QueryPath.GRAFANA_DASHBOARD:
        return _retrieve_grafana_dashboards(query, target)

    # ── Multi-source (Prometheus live + ES historical) ──
    if data_source == DataSource.MULTI:
        return _retrieve_multi_source(
            query_path, query_type, query, time_window, target,
            esql_query, promql_query, domain,
        )

    # ── Elasticsearch paths (existing) ──
    if esql_query:
        return retrieve_with_esql_query(esql_query, time_window, query_path, domain)

    if query_path == QueryPath.METRIC_AGGREGATION:
        if query_type in {QueryType.CPU_SPIKE, QueryType.MEMORY_PRESSURE, QueryType.DISK_ALERT}:
            return retrieve_metric_aggregation(time_window, target)
        return retrieve_metrics(time_window, target)

    elif query_path == QueryPath.LOG_SEARCH:
        if query_type == QueryType.ERROR_SEARCH:
            return retrieve_error_logs(time_window, target)
        if query_type == QueryType.LOG_PATTERN:
            return semantic_search_logs(query, time_window)
        return retrieve_log_aggregation(time_window, target)

    elif query_path == QueryPath.TRACE_SEARCH:
        if query_type in {QueryType.TRACE_LATENCY, QueryType.LATENCY_ANOMALY}:
            return retrieve_trace_latency(time_window, target)
        return retrieve_traces(time_window, target)

    elif query_path == QueryPath.ALERT_SEARCH:
        if query_type == QueryType.ALERT_HISTORY:
            return retrieve_alert_summary(time_window)
        return retrieve_alerts(time_window, target)

    elif query_path == QueryPath.NETWORK_QUERY:
        return retrieve_network_metrics(time_window, target)

    elif query_path == QueryPath.CROSS_INDEX:
        return cross_domain_search(time_window, target)

    elif query_path == QueryPath.STRUCTURED_ESQL:
        if domain == QueryDomain.INFRA_UPTIME:
            return retrieve_uptime_probes(time_window, target)
        if domain == QueryDomain.INFRA_NETWORK:
            return retrieve_network_metrics(time_window, target)
        return retrieve_metric_aggregation(time_window, target)

    return retrieve_metric_aggregation(time_window, target)


# ─── Prometheus Live Retrieval ───


def _retrieve_from_prometheus(
    promql: str,
    query_type: QueryType | None,
    time_window: TimeWindow,
    target: str | None,
) -> RetrievedEvidence:
    from infra_rag.clients.prometheus import prom_client, promql_cpu, promql_memory, promql_disk
    from infra_rag.clients.grafana import grafana_client

    aggs: dict[str, Any] = {}
    logs: list[LogEntry] = []
    grafana_links: list[str] = []

    try:
        # For active alerts, use the alerts API directly
        if query_type == QueryType.ALERT_ACTIVE:
            alerts = prom_client.get_alerts()
            firing = [a for a in alerts if a.get("state") == "firing"]
            aggs["total_alerts"] = len(firing)
            aggs["firing_alerts"] = len(firing)

            for alert in firing[:20]:
                labels = alert.get("labels", {})
                annotations = alert.get("annotations", {})
                logs.append(LogEntry(
                    id=f"alert:{labels.get('alertname', 'unknown')}",
                    timestamp=datetime.utcnow(),
                    message=annotations.get("description", annotations.get("summary", "")),
                    source=labels.get("instance", labels.get("job", "")),
                    fields={
                        "alertname": labels.get("alertname", ""),
                        "severity": labels.get("severity", ""),
                        "state": "firing",
                        "instance": labels.get("instance", ""),
                        "job": labels.get("job", ""),
                    },
                ))
        elif query_type == QueryType.EXPLORATORY:
            # Broad host overview: combine live CPU, memory, and disk into one evidence set.
            queries = {
                "cpu": promql_cpu(target),
                "memory": promql_memory(target),
                "disk": promql_disk(target),
            }
            by_instance: dict[str, dict[str, Any]] = {}

            for metric_name, metric_query in queries.items():
                data = prom_client.query_instant(metric_query)
                if data.get("resultType", "") != "vector":
                    continue

                for item in data.get("result", []):
                    metric = item.get("metric", {})
                    value = item.get("value", [None, None])
                    instance = metric.get("instance", metric.get("job", "unknown"))
                    val = float(value[1]) if value[1] is not None else 0.0

                    if instance not in by_instance:
                        by_instance[instance] = {}
                    by_instance[instance][metric_name] = round(val, 2)

            if by_instance:
                aggs["by_group"] = by_instance
                aggs["group_field"] = "instance"
                aggs["group_count"] = len(by_instance)
                aggs["source"] = "prometheus_live"
                aggs["overview"] = True
                promql = "multi: cpu + memory + disk"

        else:
            # Execute instant PromQL query
            data = prom_client.query_instant(promql)
            result_type = data.get("resultType", "")
            results = data.get("result", [])

            if result_type == "vector":
                by_instance: dict[str, dict[str, Any]] = {}
                for item in results:
                    metric = item.get("metric", {})
                    value = item.get("value", [None, None])
                    instance = metric.get("instance", metric.get("job", "unknown"))
                    val = float(value[1]) if value[1] is not None else 0.0

                    if instance not in by_instance:
                        by_instance[instance] = {}
                    # Use metric name or query type as key
                    metric_name = metric.get("__name__", query_type.value if query_type else "value")
                    by_instance[instance][metric_name] = round(val, 2)
                    by_instance[instance]["_raw_value"] = round(val, 2)

                if by_instance:
                    aggs["by_group"] = by_instance
                    aggs["group_field"] = "instance"
                    aggs["group_count"] = len(by_instance)
                    aggs["source"] = "prometheus_live"

            elif result_type == "scalar":
                val = float(results[1]) if len(results) > 1 else 0.0
                aggs["value"] = round(val, 2)
                aggs["source"] = "prometheus_live"

        # Build Grafana explore link for the query
        try:
            explore_url = grafana_client.build_explore_url(promql)
            grafana_links.append(explore_url)
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Prometheus retrieval failed: {e}")
        aggs["error"] = str(e)

    return RetrievedEvidence(
        logs=logs,
        aggregations=aggs,
        query_used=f"promql: {promql}",
        path=QueryPath.PROMETHEUS_LIVE,
        domain=QueryDomain.INFRA_METRICS,
        data_source=DataSource.PROMETHEUS,
        grafana_links=grafana_links,
    )


# ─── Grafana Dashboard Retrieval ───


def _retrieve_grafana_dashboards(
    query: str,
    target: str | None,
) -> RetrievedEvidence:
    from infra_rag.clients.grafana import grafana_client

    dashboards: list[GrafanaDashboardInfo] = []
    grafana_links: list[str] = []
    aggs: dict[str, Any] = {}

    try:
        search_term = target or query
        raw_dashboards = grafana_client.find_dashboards_for_target(search_term)

        for d in raw_dashboards[:5]:
            uid = d.get("uid", "")
            title = d.get("title", "")
            url = grafana_client.build_dashboard_url(uid, var_host=target)

            # Get panel info
            try:
                panels = grafana_client.get_dashboard_panels(uid)
            except Exception:
                panels = []

            dashboards.append(GrafanaDashboardInfo(
                uid=uid,
                title=title,
                url=url,
                tags=d.get("tags", []),
                panels=panels,
            ))
            grafana_links.append(url)

        aggs["dashboards_found"] = len(dashboards)
        aggs["source"] = "grafana"

    except Exception as e:
        logger.error(f"Grafana dashboard search failed: {e}")
        aggs["error"] = str(e)

    return RetrievedEvidence(
        logs=[],
        aggregations=aggs,
        query_used=f"grafana search: {target or query}",
        path=QueryPath.GRAFANA_DASHBOARD,
        domain=QueryDomain.INFRA_METRICS,
        data_source=DataSource.GRAFANA,
        grafana_dashboards=dashboards,
        grafana_links=grafana_links,
    )


# ─── Multi-Source Retrieval (Prometheus + ES) ───


def _retrieve_multi_source(
    query_path: QueryPath,
    query_type: QueryType | None,
    query: str,
    time_window: TimeWindow,
    target: str | None,
    esql_query: str | None,
    promql_query: str | None,
    domain: QueryDomain,
) -> RetrievedEvidence:
    all_logs: list[LogEntry] = []
    all_aggs: dict[str, Any] = {}
    grafana_links: list[str] = []

    # 1. Live metrics from Prometheus
    if promql_query:
        try:
            prom_evidence = _retrieve_from_prometheus(promql_query, query_type, time_window, target)
            all_aggs["live_metrics"] = prom_evidence.aggregations
            grafana_links.extend(prom_evidence.grafana_links)
        except Exception as e:
            logger.warning(f"Prometheus part of multi-source failed: {e}")
            all_aggs["live_metrics"] = {"error": str(e)}

    # 2. Historical data from ES
    try:
        if esql_query:
            es_evidence = retrieve_with_esql_query(esql_query, time_window, query_path, domain)
        else:
            es_evidence = cross_domain_search(time_window, target)
        all_logs.extend(es_evidence.logs)
        all_aggs["historical"] = es_evidence.aggregations
    except Exception as e:
        logger.warning(f"ES part of multi-source failed: {e}")
        all_aggs["historical"] = {"error": str(e)}

    all_aggs["source"] = "multi (prometheus + elasticsearch)"

    return RetrievedEvidence(
        logs=all_logs[:30],
        aggregations=all_aggs,
        query_used=f"multi: promql={promql_query}, esql={esql_query or 'cross-domain'}",
        path=query_path,
        domain=domain,
        data_source=DataSource.MULTI,
        grafana_links=grafana_links,
    )
