from datetime import datetime
from enum import Enum
from typing import Any
import json as _json
from pydantic import BaseModel, Field, field_validator


class QueryDomain(str, Enum):
    """Top-level domain classification for incoming queries."""
    INFRA_METRICS = "infra_metrics"       # CPU, memory, disk, network metrics
    INFRA_LOGS = "infra_logs"             # Application/system/container logs
    INFRA_TRACES = "infra_traces"         # Distributed traces
    INFRA_NETWORK = "infra_network"       # SNMP, switch, router, firewall
    INFRA_ALERTS = "infra_alerts"         # Prometheus alerts, alert history
    INFRA_UPTIME = "infra_uptime"         # Blackbox probes, HTTP checks
    CROSS_DOMAIN = "cross_domain"         # Queries spanning multiple domains


class QueryPath(str, Enum):
    STRUCTURED_ESQL = "structured_esql"
    LOG_SEARCH = "log_search"
    TRACE_SEARCH = "trace_search"
    METRIC_AGGREGATION = "metric_aggregation"
    ALERT_SEARCH = "alert_search"
    NETWORK_QUERY = "network_query"
    CROSS_INDEX = "cross_index"
    # Live data paths
    PROMETHEUS_LIVE = "prometheus_live"
    GRAFANA_DASHBOARD = "grafana_dashboard"


class QueryType(str, Enum):
    # Infrastructure metric types
    CPU_SPIKE = "cpu_spike"
    MEMORY_PRESSURE = "memory_pressure"
    DISK_ALERT = "disk_alert"
    NETWORK_THROUGHPUT = "network_throughput"
    LATENCY_ANOMALY = "latency_anomaly"
    SERVICE_DOWN = "service_down"
    # Log analysis types
    ERROR_SEARCH = "error_search"
    LOG_PATTERN = "log_pattern"
    # Trace types
    TRACE_LATENCY = "trace_latency"
    TRACE_ERROR = "trace_error"
    # Alert types
    ALERT_HISTORY = "alert_history"
    ALERT_ACTIVE = "alert_active"
    # General
    CAPACITY_PLANNING = "capacity_planning"
    BASELINE_COMPARE = "baseline_compare"
    EXPLORATORY = "exploratory"


class TimeWindow(BaseModel):
    start: datetime
    end: datetime


class DataSource(str, Enum):
    """Which backend to query."""
    ELASTICSEARCH = "elasticsearch"
    PROMETHEUS = "prometheus"
    GRAFANA = "grafana"
    MULTI = "multi"  # combine ES + Prometheus


class RouterOutput(BaseModel):
    domain: QueryDomain = QueryDomain.INFRA_METRICS
    query_type: QueryType
    query_path: QueryPath
    data_source: DataSource = DataSource.ELASTICSEARCH
    confidence: float = Field(ge=0.0, le=1.0)
    time_window: TimeWindow | None = None
    target: str | None = None  # hostname, service name, container, etc.
    esql_query: str | None = None
    promql_query: str | None = None  # for live Prometheus queries
    reasoning: str


class LogEntry(BaseModel):
    id: str
    timestamp: datetime
    message: str
    source: str | None = None  # hostname, service, container
    fields: dict[str, Any] = Field(default_factory=dict)


class GrafanaDashboardInfo(BaseModel):
    uid: str
    title: str
    url: str
    tags: list[str] = Field(default_factory=list)
    panels: list[dict[str, Any]] = Field(default_factory=list)


class RetrievedEvidence(BaseModel):
    logs: list[LogEntry] = Field(default_factory=list)
    aggregations: dict[str, Any] = Field(default_factory=dict)
    query_used: str
    path: QueryPath
    domain: QueryDomain = QueryDomain.INFRA_METRICS
    data_source: DataSource = DataSource.ELASTICSEARCH
    grafana_dashboards: list[GrafanaDashboardInfo] = Field(default_factory=list)
    grafana_links: list[str] = Field(default_factory=list)


class BaselineStats(BaseModel):
    target: str | None = None  # hostname or service
    hour: int
    avg_cpu_pct: float | None = None
    avg_memory_pct: float | None = None
    avg_disk_usage_pct: float | None = None
    avg_latency_ms: float | None = None
    avg_error_rate: float | None = None
    avg_request_rate: float | None = None
    p95_latency_ms: float | None = None
    source: str = "redis"


class AnalysisOutput(BaseModel):
    answer: str
    baseline_comparison: str | None = None
    citations: list[str] = Field(default_factory=list)

    @field_validator("citations", mode="before")
    @classmethod
    def coerce_citations(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        if isinstance(v, str):
            s = v.strip()
            if s.lower() in ("none", "null", "n/a", ""):
                return []
            try:
                parsed = _json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except Exception:
                pass
            return [s]
        return []


class ReflectionOutput(BaseModel):
    groundedness_score: float = Field(ge=0.0, le=1.0)
    feedback: str
    needs_refinement: bool


class FinalResponse(BaseModel):
    answer: str
    domain: QueryDomain = QueryDomain.INFRA_METRICS
    baseline_comparison: str | None = None
    citations: list[str] = Field(default_factory=list)
    query_path: QueryPath
    query_type: QueryType | None = None
    reflections: int = 0
    processing_time_ms: int = 0
    from_cache: bool = False
    groundedness_score: float | None = None
    correctness_score: float | None = None
    citation_score: float | None = None
    should_abstain: bool = False
    abstain_reason: str | None = None
    data_freshness: str | None = None
    cached_at: str | None = None
    cost_usd: float | None = None
    cost_limit_hit: bool = False
    retrieval_stats: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    message: str
    context: dict[str, Any] | None = None
