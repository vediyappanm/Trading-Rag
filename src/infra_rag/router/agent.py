ROUTER_SYSTEM_PROMPT = """You are a query router for an Infrastructure Management RAG system. You analyze user questions about infrastructure, servers, services, containers, and networks to determine the best retrieval strategy.

Data is stored in Elasticsearch across these indices:
- infra-metrics: CPU, memory, disk, network metrics from Prometheus (via remote_write)
  Fields: @timestamp, host.name, host.ip, cpu.usage_pct, memory.usage_pct, disk.usage_pct,
  network.bytes_in, network.bytes_out, container.name, service.name, prometheus.job, prometheus.instance

- infra-logs: Application and system logs from Filebeat/Fluent Bit
  Fields: @timestamp, host.name, service.name, log.level (INFO/WARN/ERROR/FATAL),
  message, container.name, source, agent.type, error.message, error.stack_trace

- infra-traces: Distributed traces from Grafana Tempo
  Fields: @timestamp, trace.id, span.id, service.name, operation.name,
  duration_ms, status.code (OK/ERROR), parent.span.id

- infra-alerts: Prometheus Alertmanager alert history
  Fields: @timestamp, alertname, severity (critical/warning/info), instance,
  host.name, state (firing/resolved), description, summary

- infra-network: SNMP/network device metrics
  Fields: @timestamp, host.name, device.name, interface.name, interface.bytes_in,
  interface.bytes_out, device.cpu_pct, device.memory_pct

- infra-blackbox: HTTP/ICMP probe results from Blackbox Exporter
  Fields: @timestamp, probe.target, probe.duration_ms, probe.success, probe.type

Available domains:
- infra_metrics: CPU, memory, disk, network metrics from servers/containers
- infra_logs: Application/system log analysis
- infra_traces: Distributed trace analysis
- infra_network: Network device metrics (SNMP, switches, routers)
- infra_alerts: Prometheus alert history and active alerts
- infra_uptime: Blackbox probe results, HTTP checks, uptime monitoring
- cross_domain: Queries spanning metrics + logs + alerts (e.g., "why is X down?")

Available paths:
1. structured_esql: Direct ES|QL for aggregations, counts, metrics
2. log_search: Log retrieval with level/service filtering
3. trace_search: Distributed trace queries
4. metric_aggregation: CPU/memory/disk/network metric rollups
5. alert_search: Alert history and active alert queries
6. network_query: SNMP/network device queries
7. cross_index: Multi-index correlation for root cause analysis

Output format:
- domain: The primary data domain
- query_type: One of cpu_spike, memory_pressure, disk_alert, network_throughput, latency_anomaly, service_down, error_search, log_pattern, trace_latency, trace_error, alert_history, alert_active, capacity_planning, baseline_compare, exploratory
- query_path: The selected retrieval path
- confidence: 0.0 to 1.0
- time_window: Extract time range (default: last 1 hour for realtime, last 24h for historical)
- target: Hostname, service name, or container name mentioned
- esql_query: ES|QL query for structured paths, or null
- reasoning: Brief explanation"""

ROUTER_USER_PROMPT = """Analyze this infrastructure query:

{query}

Determine:
1. Which domain and retrieval path best matches
2. Which query_type best describes the intent
3. Extract any time window mentioned
4. Extract any target (hostname, service, container) mentioned
5. Provide your confidence score
6. Generate an ES|QL query if applicable

Current time: {current_time}

Respond with your routing decision."""

from datetime import datetime, timedelta
from typing import Any
import re

from infra_rag.clients import create_llm, structured_output
from infra_rag.config import settings
from infra_rag.models import RouterOutput, QueryPath, QueryDomain, DataSource, TimeWindow, QueryType
from infra_rag.clients.prometheus import (
    promql_cpu, promql_memory, promql_disk, promql_network_rx, promql_network_tx, promql_up,
)


def parse_time_from_query(query: str, current_time: datetime) -> TimeWindow | None:
    ql = query.lower()

    # Explicit date ranges
    date_pattern = r"(\d{4}-\d{2}-\d{2})\s+(?:to|until|-)\s+(\d{4}-\d{2}-\d{2})"
    date_match = re.search(date_pattern, query)
    if date_match:
        try:
            start = datetime.fromisoformat(date_match.group(1))
            end = datetime.fromisoformat(date_match.group(2))
            return TimeWindow(start=start, end=end)
        except ValueError:
            pass

    # Relative time expressions
    relative_patterns = [
        (r"last\s+(\d+)\s+minute", "minutes"),
        (r"last\s+(\d+)\s+hour", "hours"),
        (r"last\s+(\d+)\s+day", "days"),
        (r"past\s+(\d+)\s+minute", "minutes"),
        (r"past\s+(\d+)\s+hour", "hours"),
        (r"past\s+(\d+)\s+day", "days"),
    ]
    for pattern, unit in relative_patterns:
        match = re.search(pattern, ql)
        if match:
            value = int(match.group(1))
            delta = timedelta(**{unit: value})
            return TimeWindow(start=current_time - delta, end=current_time)

    # Named time ranges
    if "last hour" in ql or "past hour" in ql:
        return TimeWindow(start=current_time - timedelta(hours=1), end=current_time)
    if "today" in ql:
        start = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeWindow(start=start, end=current_time)
    if "yesterday" in ql:
        start = (current_time - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
        end = current_time.replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeWindow(start=start, end=end)
    if "this week" in ql:
        start = current_time - timedelta(days=current_time.weekday())
        start = start.replace(hour=0, minute=0, second=0, microsecond=0)
        return TimeWindow(start=start, end=current_time)

    return None


# Words that should not be treated as target hostnames/services
_NOISE_WORDS: set[str] = {
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "must",
    "what", "how", "when", "where", "why", "which", "who", "show", "list",
    "get", "find", "all", "any", "and", "or", "but", "not", "for", "with",
    "from", "to", "in", "on", "at", "by", "last", "me", "of", "my",
    "cpu", "memory", "disk", "network", "latency", "error", "errors",
    "alert", "alerts", "log", "logs", "trace", "traces", "metric", "metrics",
    "server", "servers", "service", "services", "container", "containers",
    "high", "low", "spike", "spikes", "down", "up", "slow", "fast",
    "status", "health", "check", "monitor", "dashboard",
    "hour", "hours", "minute", "minutes", "day", "days", "today", "yesterday",
    "average", "avg", "max", "min", "total", "count", "sum", "rate",
    "current", "active", "firing", "resolved", "critical", "warning",
    "why", "happening", "happened", "running", "stopped", "failed",
    "node", "nodes", "cluster", "pod", "pods", "namespace",
    "usage", "utilization", "throughput", "bandwidth", "capacity",
    "uptime", "downtime", "availability", "response", "request",
}


def extract_target(query: str) -> str | None:
    """Extract hostname, service name, IP, or container name from query."""
    # IP addresses
    ip_match = re.search(r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", query)
    if ip_match:
        return ip_match.group(1)

    # Quoted names
    quoted = re.search(r'["\']([^"\']+)["\']', query)
    if quoted:
        return quoted.group(1)

    # Hostnames with dots (e.g., web-01.prod.example.com)
    host_match = re.search(r"\b([a-zA-Z][a-zA-Z0-9-]+(?:\.[a-zA-Z0-9-]+)+)\b", query)
    if host_match:
        candidate = host_match.group(1)
        if not candidate.startswith("http"):
            return candidate

    # Hostnames with dashes (e.g., web-01, api-server-03, prod-db-master)
    dash_match = re.search(r"\b([a-zA-Z][a-zA-Z0-9]*(?:-[a-zA-Z0-9]+)+)\b", query)
    if dash_match:
        candidate = dash_match.group(1).lower()
        if candidate not in _NOISE_WORDS and len(candidate) > 3:
            return dash_match.group(1)

    # Container/service names after keywords
    after_kw = re.search(
        r"(?:on|for|from|of|in|at|host|server|service|container|node|pod)\s+([a-zA-Z][a-zA-Z0-9_.-]+)",
        query, re.IGNORECASE,
    )
    if after_kw:
        candidate = after_kw.group(1).lower()
        if candidate not in _NOISE_WORDS and len(candidate) > 2:
            return after_kw.group(1)

    return None


def _is_live_query(ql: str) -> bool:
    """Detect queries asking for current/realtime/live data."""
    return any(kw in ql for kw in [
        "right now", "currently", "current", "live", "realtime", "real-time",
        "at this moment", "now", "what is the", "what's the",
    ])


def _is_dashboard_query(ql: str) -> bool:
    """Detect queries asking for Grafana dashboards."""
    return any(kw in ql for kw in [
        "dashboard", "grafana", "panel", "graph", "chart", "visuali",
        "show me the dashboard", "open grafana", "grafana link",
    ])


def _is_host_overview_query(ql: str) -> bool:
    """Detect broad host overview queries that should use live multi-metric retrieval."""
    mentions_compute = "cpu" in ql
    mentions_memory = "memory" in ql or "ram" in ql
    mentions_fleet = any(kw in ql for kw in [
        "all hosts", "across all hosts", "all servers", "all nodes",
        "across hosts", "host metrics", "infrastructure overview", "overview",
    ])
    return (mentions_compute and mentions_memory) or mentions_fleet


def route_query(query: str) -> RouterOutput:
    """Regex-based fast routing for infrastructure queries."""
    current_time = datetime.utcnow()
    ql = query.lower()

    time_window = parse_time_from_query(query, current_time)
    if time_window is None:
        time_window = TimeWindow(
            start=current_time - timedelta(hours=1),
            end=current_time,
        )

    target = extract_target(query)
    is_live = _is_live_query(ql)
    is_dashboard = _is_dashboard_query(ql)

    # ── Grafana dashboard requests ──
    if is_dashboard:
        return _build_output(QueryDomain.INFRA_METRICS, QueryType.EXPLORATORY,
                            QueryPath.GRAFANA_DASHBOARD, DataSource.GRAFANA,
                            0.90, time_window, target, query)

    # ── Host overview / fleet summary ──
    if _is_host_overview_query(ql):
        return _build_output(QueryDomain.INFRA_METRICS, QueryType.EXPLORATORY,
                            QueryPath.PROMETHEUS_LIVE, DataSource.PROMETHEUS,
                            0.92, time_window, target, query)

    # ── Alerts (live from Prometheus API if asking for current) ──
    if any(kw in ql for kw in ["alert", "firing", "alertmanager", "pagerduty", "oncall"]):
        if any(kw in ql for kw in ["active", "firing", "current"]):
            return _build_output(QueryDomain.INFRA_ALERTS, QueryType.ALERT_ACTIVE,
                                QueryPath.PROMETHEUS_LIVE, DataSource.PROMETHEUS,
                                0.90, time_window, target, query)
        return _build_output(QueryDomain.INFRA_ALERTS, QueryType.ALERT_HISTORY,
                            QueryPath.ALERT_SEARCH, DataSource.ELASTICSEARCH,
                            0.85, time_window, target, query)

    # Uptime / blackbox
    if any(kw in ql for kw in ["uptime", "downtime", "probe", "blackbox", "health check", "ping"]):
        src = DataSource.PROMETHEUS if is_live else DataSource.ELASTICSEARCH
        path = QueryPath.PROMETHEUS_LIVE if is_live else QueryPath.STRUCTURED_ESQL
        return _build_output(QueryDomain.INFRA_UPTIME, QueryType.SERVICE_DOWN,
                            path, src, 0.85, time_window, target, query)

    # Traces
    if any(kw in ql for kw in ["trace", "span", "distributed", "tracing", "jaeger", "tempo"]):
        qt = QueryType.TRACE_ERROR if any(kw in ql for kw in ["error", "fail"]) else QueryType.TRACE_LATENCY
        return _build_output(QueryDomain.INFRA_TRACES, qt,
                            QueryPath.TRACE_SEARCH, DataSource.ELASTICSEARCH,
                            0.85, time_window, target, query)

    # Logs
    if any(kw in ql for kw in ["log", "logs", "error log", "exception", "stack trace", "stderr"]):
        if any(kw in ql for kw in ["error", "exception", "fatal", "critical", "fail"]):
            return _build_output(QueryDomain.INFRA_LOGS, QueryType.ERROR_SEARCH,
                                QueryPath.LOG_SEARCH, DataSource.ELASTICSEARCH,
                                0.90, time_window, target, query)
        return _build_output(QueryDomain.INFRA_LOGS, QueryType.LOG_PATTERN,
                            QueryPath.LOG_SEARCH, DataSource.ELASTICSEARCH,
                            0.80, time_window, target, query)

    # Network / SNMP
    if any(kw in ql for kw in ["snmp", "switch", "router", "firewall", "interface", "bandwidth",
                                "idrac", "ilo", "network device"]):
        return _build_output(QueryDomain.INFRA_NETWORK, QueryType.NETWORK_THROUGHPUT,
                            QueryPath.NETWORK_QUERY, DataSource.ELASTICSEARCH,
                            0.85, time_window, target, query)

    # CPU — live from Prometheus if asking "right now"
    if any(kw in ql for kw in ["cpu", "processor", "load average", "cpu usage"]):
        src = DataSource.PROMETHEUS if is_live else DataSource.ELASTICSEARCH
        path = QueryPath.PROMETHEUS_LIVE if is_live else QueryPath.METRIC_AGGREGATION
        return _build_output(QueryDomain.INFRA_METRICS, QueryType.CPU_SPIKE,
                            path, src, 0.90, time_window, target, query)

    # Memory
    if any(kw in ql for kw in ["memory", "ram", "oom", "out of memory", "swap", "memory pressure"]):
        src = DataSource.PROMETHEUS if is_live else DataSource.ELASTICSEARCH
        path = QueryPath.PROMETHEUS_LIVE if is_live else QueryPath.METRIC_AGGREGATION
        return _build_output(QueryDomain.INFRA_METRICS, QueryType.MEMORY_PRESSURE,
                            path, src, 0.90, time_window, target, query)

    # Disk
    if any(kw in ql for kw in ["disk", "storage", "filesystem", "inode", "disk space", "volume"]):
        src = DataSource.PROMETHEUS if is_live else DataSource.ELASTICSEARCH
        path = QueryPath.PROMETHEUS_LIVE if is_live else QueryPath.METRIC_AGGREGATION
        return _build_output(QueryDomain.INFRA_METRICS, QueryType.DISK_ALERT,
                            path, src, 0.90, time_window, target, query)

    # Latency
    if any(kw in ql for kw in ["latency", "response time", "slow", "p95", "p99", "timeout"]):
        return _build_output(QueryDomain.INFRA_METRICS, QueryType.LATENCY_ANOMALY,
                            QueryPath.METRIC_AGGREGATION, DataSource.ELASTICSEARCH,
                            0.85, time_window, target, query)

    # Service down — use Prometheus + ES combined
    if any(kw in ql for kw in ["down", "unreachable", "not responding", "service down", "outage"]):
        return _build_output(QueryDomain.CROSS_DOMAIN, QueryType.SERVICE_DOWN,
                            QueryPath.CROSS_INDEX, DataSource.MULTI,
                            0.85, time_window, target, query)

    # Cross-domain ("why is X...", "what happened to X...")
    if any(kw in ql for kw in ["why is", "what happened", "root cause", "investigate", "troubleshoot"]):
        return _build_output(QueryDomain.CROSS_DOMAIN, QueryType.EXPLORATORY,
                            QueryPath.CROSS_INDEX, DataSource.MULTI,
                            0.80, time_window, target, query)

    # Capacity planning
    if any(kw in ql for kw in ["capacity", "forecast", "growth", "trend", "planning"]):
        return _build_output(QueryDomain.INFRA_METRICS, QueryType.CAPACITY_PLANNING,
                            QueryPath.METRIC_AGGREGATION, DataSource.ELASTICSEARCH,
                            0.80, time_window, target, query)

    # Network throughput (general)
    if any(kw in ql for kw in ["network", "throughput", "traffic", "bytes"]):
        src = DataSource.PROMETHEUS if is_live else DataSource.ELASTICSEARCH
        path = QueryPath.PROMETHEUS_LIVE if is_live else QueryPath.METRIC_AGGREGATION
        return _build_output(QueryDomain.INFRA_METRICS, QueryType.NETWORK_THROUGHPUT,
                            path, src, 0.80, time_window, target, query)

    # Error keywords without explicit "log"
    if any(kw in ql for kw in ["error", "errors", "5xx", "500", "exception", "crash", "failed"]):
        return _build_output(QueryDomain.INFRA_LOGS, QueryType.ERROR_SEARCH,
                            QueryPath.LOG_SEARCH, DataSource.ELASTICSEARCH,
                            0.80, time_window, target, query)

    # Default: live Prometheus if asking "now", else ES
    src = DataSource.PROMETHEUS if is_live else DataSource.ELASTICSEARCH
    path = QueryPath.PROMETHEUS_LIVE if is_live else QueryPath.METRIC_AGGREGATION
    return _build_output(QueryDomain.INFRA_METRICS, QueryType.EXPLORATORY,
                        path, src, 0.60, time_window, target, query)


def _build_output(
    domain: QueryDomain,
    query_type: QueryType,
    query_path: QueryPath,
    data_source: DataSource,
    confidence: float,
    time_window: TimeWindow,
    target: str | None,
    query: str,
) -> RouterOutput:
    esql_query = _build_esql(domain, query_type, time_window, target, query) if data_source != DataSource.GRAFANA else None
    promql = _build_promql(query_type, target) if data_source in (DataSource.PROMETHEUS, DataSource.MULTI) else None
    reasoning = (
        f"Domain: {domain.value}, Type: {query_type.value}, Path: {query_path.value}, "
        f"Source: {data_source.value}. Target: {target or 'none'}. "
        f"Time: {time_window.start.isoformat()} to {time_window.end.isoformat()}"
    )
    return RouterOutput(
        domain=domain,
        query_type=query_type,
        query_path=query_path,
        data_source=data_source,
        confidence=confidence,
        time_window=time_window,
        target=target,
        esql_query=esql_query,
        promql_query=promql,
        reasoning=reasoning,
    )


def _build_promql(query_type: QueryType, target: str | None) -> str | None:
    """Generate a PromQL query based on query type and target."""
    if query_type == QueryType.CPU_SPIKE:
        return promql_cpu(target)
    elif query_type == QueryType.MEMORY_PRESSURE:
        return promql_memory(target)
    elif query_type == QueryType.DISK_ALERT:
        return promql_disk(target)
    elif query_type == QueryType.NETWORK_THROUGHPUT:
        return promql_network_rx(target)
    elif query_type == QueryType.SERVICE_DOWN:
        return promql_up()
    elif query_type == QueryType.ALERT_ACTIVE:
        return "ALERTS{alertstate='firing'}"
    elif query_type == QueryType.EXPLORATORY:
        # Return a multi-metric overview
        if target:
            return promql_cpu(target)
        return promql_up()
    return None


def _build_esql(
    domain: QueryDomain,
    query_type: QueryType,
    time_window: TimeWindow,
    target: str | None,
    query: str,
) -> str | None:
    base = f'@timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"'

    # ── Metrics ──
    if domain == QueryDomain.INFRA_METRICS:
        index = settings.elasticsearch.metrics_index
        if target:
            target_filter = f'AND (host.name == "{target}" OR service.name == "{target}")'
        else:
            target_filter = ""
        by_clause = "" if target else "BY host.name"

        if query_type == QueryType.CPU_SPIKE:
            return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS avg_cpu = AVG(cpu.usage_pct),
        max_cpu = MAX(cpu.usage_pct),
        avg_memory = AVG(memory.usage_pct),
        data_points = COUNT() {by_clause}
| SORT max_cpu DESC
| LIMIT 20
"""
        if query_type == QueryType.MEMORY_PRESSURE:
            return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS avg_memory = AVG(memory.usage_pct),
        max_memory = MAX(memory.usage_pct),
        data_points = COUNT() {by_clause}
| SORT max_memory DESC
| LIMIT 20
"""
        if query_type == QueryType.DISK_ALERT:
            return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS avg_disk = AVG(disk.usage_pct),
        max_disk = MAX(disk.usage_pct),
        data_points = COUNT() {by_clause}
| SORT max_disk DESC
| LIMIT 20
"""
        if query_type == QueryType.NETWORK_THROUGHPUT:
            return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS avg_in = AVG(network.bytes_in),
        avg_out = AVG(network.bytes_out),
        max_in = MAX(network.bytes_in),
        max_out = MAX(network.bytes_out),
        data_points = COUNT() {by_clause}
| SORT avg_in DESC
| LIMIT 20
"""
        # Default metric aggregation
        return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS avg_cpu = AVG(cpu.usage_pct),
        max_cpu = MAX(cpu.usage_pct),
        avg_memory = AVG(memory.usage_pct),
        max_memory = MAX(memory.usage_pct),
        avg_disk = AVG(disk.usage_pct),
        avg_net_in = AVG(network.bytes_in),
        avg_net_out = AVG(network.bytes_out),
        data_points = COUNT() {by_clause}
| SORT avg_cpu DESC
| LIMIT 20
"""

    # ── Logs ──
    if domain == QueryDomain.INFRA_LOGS:
        index = settings.elasticsearch.logs_index
        target_filter = f'AND (host.name == "{target}" OR service.name == "{target}")' if target else ""

        if query_type == QueryType.ERROR_SEARCH:
            return f"""
FROM "{index}"
| WHERE {base} AND log.level IN ("ERROR", "FATAL", "CRITICAL") {target_filter}
| SORT @timestamp DESC
| LIMIT 50
"""
        return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS total_logs = COUNT(),
        errors = SUM(CASE(log.level == "ERROR", 1, 0)),
        warnings = SUM(CASE(log.level == "WARN", 1, 0)),
        fatals = SUM(CASE(log.level == "FATAL", 1, 0)) BY service.name
| SORT errors DESC
| LIMIT 20
"""

    # ── Traces ──
    if domain == QueryDomain.INFRA_TRACES:
        index = settings.elasticsearch.traces_index
        service_filter = f'AND service.name == "{target}"' if target else ""

        return f"""
FROM "{index}"
| WHERE {base} {service_filter}
| STATS avg_duration = AVG(duration_ms),
        p95_duration = PERCENTILE(duration_ms, 95),
        p99_duration = PERCENTILE(duration_ms, 99),
        total_spans = COUNT(),
        error_spans = SUM(CASE(status.code == "ERROR", 1, 0)) BY service.name
| SORT avg_duration DESC
| LIMIT 20
"""

    # ── Alerts ──
    if domain == QueryDomain.INFRA_ALERTS:
        index = settings.elasticsearch.alerts_index
        target_filter = f'AND (instance LIKE "*{target}*" OR host.name == "{target}")' if target else ""

        if query_type == QueryType.ALERT_ACTIVE:
            return f"""
FROM "{index}"
| WHERE {base} AND state == "firing" {target_filter}
| SORT @timestamp DESC
| LIMIT 50
"""
        return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS total = COUNT(),
        critical = SUM(CASE(severity == "critical", 1, 0)),
        warning = SUM(CASE(severity == "warning", 1, 0)) BY alertname
| SORT critical DESC
| LIMIT 20
"""

    # ── Network ──
    if domain == QueryDomain.INFRA_NETWORK:
        index = settings.elasticsearch.network_index
        target_filter = f'AND (host.name == "{target}" OR device.name == "{target}")' if target else ""

        return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS avg_in = AVG(interface.bytes_in),
        avg_out = AVG(interface.bytes_out),
        avg_cpu = AVG(device.cpu_pct),
        avg_memory = AVG(device.memory_pct),
        data_points = COUNT() BY host.name
| SORT avg_in DESC
| LIMIT 20
"""

    # ── Uptime ──
    if domain == QueryDomain.INFRA_UPTIME:
        index = settings.elasticsearch.blackbox_index
        target_filter = f'AND probe.target LIKE "*{target}*"' if target else ""

        return f"""
FROM "{index}"
| WHERE {base} {target_filter}
| STATS avg_latency = AVG(probe.duration_ms),
        p95_latency = PERCENTILE(probe.duration_ms, 95),
        success_rate = AVG(CASE(probe.success == true, 1.0, 0.0)),
        total_probes = COUNT() BY probe.target
| SORT success_rate ASC
| LIMIT 20
"""

    return None


def route_query_llm(query: str) -> RouterOutput:
    """LLM-based routing with infra domain awareness."""
    llm = create_llm(settings.llm.router_model)
    current_time = datetime.utcnow()

    prompt = ROUTER_USER_PROMPT.format(
        query=query,
        current_time=current_time.isoformat(),
    )

    result = structured_output(llm, RouterOutput, prompt)

    import logging
    logger = logging.getLogger(__name__)

    if result:
        # Parse time window from query (override LLM's often-wrong time parsing)
        time_window = parse_time_from_query(query, current_time)
        if time_window is None:
            time_window = TimeWindow(
                start=current_time - timedelta(hours=1),
                end=current_time,
            )
        result.time_window = time_window

        # Validate/clean target — reject generic phrases the LLM misparses as hostnames
        llm_target = str(result.target or "").strip()
        _BOGUS_TARGETS = {
            "null", "none", "", "all", "all hosts", "all servers", "all services",
            "all nodes", "every", "everything", "next", "next month", "overall",
            "system", "cluster", "infrastructure", "infra", "total",
        }
        if llm_target and llm_target.lower() not in _BOGUS_TARGETS:
            result.target = llm_target
        else:
            result.target = extract_target(query)

        # Regenerate ES|QL from our templates (LLM ESQL is often broken)
        result.esql_query = _build_esql(
            result.domain, result.query_type,
            result.time_window, result.target, query,
        )
        result.promql_query = _build_promql(result.query_type, result.target)

        ql = query.lower()

        # Broad host-overview questions work best from live Prometheus metrics.
        if _is_host_overview_query(ql):
            result.domain = QueryDomain.INFRA_METRICS
            result.query_type = QueryType.EXPLORATORY
            result.query_path = QueryPath.PROMETHEUS_LIVE
            result.data_source = DataSource.PROMETHEUS
            result.promql_query = _build_promql(result.query_type, result.target)
            result.esql_query = None

        # Keep "currently firing alerts" on the live Prometheus path.
        if result.query_type == QueryType.ALERT_ACTIVE:
            result.query_path = QueryPath.PROMETHEUS_LIVE
            result.data_source = DataSource.PROMETHEUS
            result.esql_query = None

        logger.info(f"LLM Routed: '{query}' → domain={result.domain.value}, "
                    f"type={result.query_type.value}, target={result.target}")
        return result

    logger.warning(f"LLM routing failed for '{query}', falling back to regex")
    return route_query(query)
