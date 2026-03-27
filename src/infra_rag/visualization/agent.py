"""Grafana Dashboard JSON Generator Agent.

Generates valid Grafana dashboard JSON from:
- User's question (what they want to see)
- Retrieved evidence (what data is available)
- Target host/service (scope of visualization)

The generated dashboard is pushed to Grafana via API, and a link is returned.
"""

import logging
import uuid
from datetime import datetime
from typing import Any

from infra_rag.clients import create_llm, structured_output
from infra_rag.clients.grafana import grafana_client
from infra_rag.clients.prometheus import (
    promql_cpu, promql_memory, promql_disk,
    promql_network_rx, promql_network_tx,
)
from infra_rag.config import settings
from infra_rag.models import (
    QueryType, QueryDomain, RetrievedEvidence, BaselineStats,
)

logger = logging.getLogger(__name__)


# ─── Panel Templates ───

def _timeseries_panel(
    title: str,
    promql: str,
    grid_x: int = 0,
    grid_y: int = 0,
    grid_w: int = 12,
    grid_h: int = 8,
    unit: str = "percent",
    panel_id: int = 1,
    thresholds: list[dict] | None = None,
) -> dict[str, Any]:
    panel: dict[str, Any] = {
        "id": panel_id,
        "type": "timeseries",
        "title": title,
        "gridPos": {"x": grid_x, "y": grid_y, "w": grid_w, "h": grid_h},
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "targets": [
            {
                "expr": promql,
                "refId": "A",
                "legendFormat": "{{instance}}",
            }
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "custom": {
                    "drawStyle": "line",
                    "lineWidth": 2,
                    "fillOpacity": 15,
                    "gradientMode": "opacity",
                    "pointSize": 5,
                    "showPoints": "never",
                },
            },
            "overrides": [],
        },
    }
    if thresholds:
        panel["fieldConfig"]["defaults"]["thresholds"] = {
            "mode": "absolute",
            "steps": thresholds,
        }
    return panel


def _stat_panel(
    title: str,
    promql: str,
    grid_x: int = 0,
    grid_y: int = 0,
    grid_w: int = 6,
    grid_h: int = 4,
    unit: str = "percent",
    panel_id: int = 1,
) -> dict[str, Any]:
    return {
        "id": panel_id,
        "type": "stat",
        "title": title,
        "gridPos": {"x": grid_x, "y": grid_y, "w": grid_w, "h": grid_h},
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "targets": [
            {"expr": promql, "refId": "A", "legendFormat": "{{instance}}"}
        ],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "thresholds": {
                    "mode": "absolute",
                    "steps": [
                        {"color": "green", "value": None},
                        {"color": "yellow", "value": 70},
                        {"color": "red", "value": 90},
                    ],
                },
            },
            "overrides": [],
        },
        "options": {"colorMode": "background", "graphMode": "area"},
    }


def _table_panel(
    title: str,
    promql: str,
    grid_x: int = 0,
    grid_y: int = 0,
    grid_w: int = 24,
    grid_h: int = 8,
    panel_id: int = 1,
) -> dict[str, Any]:
    return {
        "id": panel_id,
        "type": "table",
        "title": title,
        "gridPos": {"x": grid_x, "y": grid_y, "w": grid_w, "h": grid_h},
        "datasource": {"type": "prometheus", "uid": "prometheus"},
        "targets": [
            {"expr": promql, "refId": "A", "format": "table", "instant": True}
        ],
        "options": {"showHeader": True},
        "fieldConfig": {"defaults": {}, "overrides": []},
    }


def _logs_panel(
    title: str,
    query: str,
    datasource_uid: str = "elasticsearch-logs",
    grid_x: int = 0,
    grid_y: int = 0,
    grid_w: int = 24,
    grid_h: int = 10,
    panel_id: int = 1,
) -> dict[str, Any]:
    return {
        "id": panel_id,
        "type": "logs",
        "title": title,
        "gridPos": {"x": grid_x, "y": grid_y, "w": grid_w, "h": grid_h},
        "datasource": {"type": "elasticsearch", "uid": datasource_uid},
        "targets": [
            {"query": query, "refId": "A"}
        ],
        "options": {
            "showTime": True,
            "showLabels": True,
            "showCommonLabels": False,
            "wrapLogMessage": True,
            "prettifyLogMessage": False,
            "enableLogDetails": True,
            "sortOrder": "Descending",
        },
    }


# ─── Dashboard Builder ───


def _wrap_dashboard(
    title: str,
    panels: list[dict[str, Any]],
    tags: list[str] | None = None,
    time_from: str = "now-1h",
    time_to: str = "now",
    refresh: str = "30s",
) -> dict[str, Any]:
    uid = f"adhoc-{uuid.uuid4().hex[:8]}"
    return {
        "uid": uid,
        "title": title,
        "tags": tags or ["infra-rag", "ad-hoc"],
        "timezone": "utc",
        "editable": True,
        "time": {"from": time_from, "to": time_to},
        "refresh": refresh,
        "panels": panels,
        "schemaVersion": 39,
        "version": 0,
    }


# ─── Pre-built Dashboard Templates ───


def _dashboard_host_overview(target: str | None) -> dict[str, Any]:
    """Host overview: CPU + Memory + Disk + Network."""
    instance = target
    title = f"Host Overview: {target}" if target else "All Hosts Overview"

    panels = [
        _stat_panel("CPU Usage", promql_cpu(instance),
                     grid_x=0, grid_y=0, grid_w=6, grid_h=4, panel_id=1),
        _stat_panel("Memory Usage", promql_memory(instance),
                     grid_x=6, grid_y=0, grid_w=6, grid_h=4, panel_id=2),
        _stat_panel("Disk Usage", promql_disk(instance),
                     grid_x=12, grid_y=0, grid_w=6, grid_h=4, panel_id=3),
        _stat_panel("Network RX", promql_network_rx(instance),
                     grid_x=18, grid_y=0, grid_w=6, grid_h=4,
                     unit="Bps", panel_id=4),
        _timeseries_panel("CPU Over Time", promql_cpu(instance),
                          grid_x=0, grid_y=4, grid_w=12, grid_h=8, panel_id=5,
                          thresholds=[
                              {"color": "green", "value": None},
                              {"color": "yellow", "value": 70},
                              {"color": "red", "value": 90},
                          ]),
        _timeseries_panel("Memory Over Time", promql_memory(instance),
                          grid_x=12, grid_y=4, grid_w=12, grid_h=8, panel_id=6,
                          thresholds=[
                              {"color": "green", "value": None},
                              {"color": "yellow", "value": 70},
                              {"color": "red", "value": 90},
                          ]),
        _timeseries_panel("Disk Usage Over Time", promql_disk(instance),
                          grid_x=0, grid_y=12, grid_w=12, grid_h=8, panel_id=7),
        _timeseries_panel("Network I/O", promql_network_rx(instance),
                          grid_x=12, grid_y=12, grid_w=12, grid_h=8,
                          unit="Bps", panel_id=8),
    ]
    return _wrap_dashboard(title, panels, tags=["infra-rag", "host", "ad-hoc"])


def _dashboard_service_latency(target: str | None) -> dict[str, Any]:
    """Service latency dashboard with p50/p95/p99."""
    svc_filter = f'service=~"{target}.*"' if target else ""
    title = f"Service Latency: {target}" if target else "All Services Latency"

    panels = [
        _timeseries_panel(
            "Request Latency (p50/p95/p99)",
            f'histogram_quantile(0.95, rate(http_request_duration_seconds_bucket{{{svc_filter}}}[5m]))',
            grid_x=0, grid_y=0, grid_w=24, grid_h=10,
            unit="s", panel_id=1,
        ),
        _stat_panel(
            "Request Rate",
            f'sum(rate(http_requests_total{{{svc_filter}}}[5m]))',
            grid_x=0, grid_y=10, grid_w=8, grid_h=4,
            unit="reqps", panel_id=2,
        ),
        _stat_panel(
            "Error Rate (5xx)",
            f'sum(rate(http_requests_total{{status=~"5..",{svc_filter}}}[5m])) / sum(rate(http_requests_total{{{svc_filter}}}[5m])) * 100',
            grid_x=8, grid_y=10, grid_w=8, grid_h=4,
            unit="percent", panel_id=3,
        ),
        _stat_panel(
            "Active Connections",
            f'sum(http_connections_active{{{svc_filter}}})',
            grid_x=16, grid_y=10, grid_w=8, grid_h=4,
            unit="short", panel_id=4,
        ),
    ]
    return _wrap_dashboard(title, panels, tags=["infra-rag", "service", "latency", "ad-hoc"])


def _dashboard_alerts_overview() -> dict[str, Any]:
    """Active alerts dashboard."""
    panels = [
        _stat_panel("Firing Alerts", 'count(ALERTS{alertstate="firing"})',
                     grid_x=0, grid_y=0, grid_w=8, grid_h=4,
                     unit="short", panel_id=1),
        _stat_panel("Critical Alerts", 'count(ALERTS{alertstate="firing", severity="critical"})',
                     grid_x=8, grid_y=0, grid_w=8, grid_h=4,
                     unit="short", panel_id=2),
        _stat_panel("Warning Alerts", 'count(ALERTS{alertstate="firing", severity="warning"})',
                     grid_x=16, grid_y=0, grid_w=8, grid_h=4,
                     unit="short", panel_id=3),
        _table_panel("Alert Details", 'ALERTS{alertstate="firing"}',
                      grid_x=0, grid_y=4, grid_w=24, grid_h=10, panel_id=4),
    ]
    return _wrap_dashboard("Active Alerts", panels,
                          tags=["infra-rag", "alerts", "ad-hoc"], refresh="10s")


def _dashboard_error_investigation(target: str | None) -> dict[str, Any]:
    """Cross-domain investigation: CPU + Memory + Error logs + Alerts."""
    instance = target
    title = f"Investigation: {target}" if target else "System Investigation"

    panels = [
        _stat_panel("CPU", promql_cpu(instance),
                     grid_x=0, grid_y=0, grid_w=6, grid_h=4, panel_id=1),
        _stat_panel("Memory", promql_memory(instance),
                     grid_x=6, grid_y=0, grid_w=6, grid_h=4, panel_id=2),
        _stat_panel("Disk", promql_disk(instance),
                     grid_x=12, grid_y=0, grid_w=6, grid_h=4, panel_id=3),
        _stat_panel("Alerts", 'count(ALERTS{alertstate="firing"})',
                     grid_x=18, grid_y=0, grid_w=6, grid_h=4,
                     unit="short", panel_id=4),
        _timeseries_panel("CPU + Memory Over Time", promql_cpu(instance),
                          grid_x=0, grid_y=4, grid_w=24, grid_h=8, panel_id=5),
        _logs_panel(
            "Error Logs",
            f'log.level:ERROR OR log.level:FATAL' + (f' AND host.name:{target}' if target else ''),
            grid_x=0, grid_y=12, grid_w=24, grid_h=10, panel_id=6,
        ),
    ]
    return _wrap_dashboard(title, panels,
                          tags=["infra-rag", "investigation", "ad-hoc"],
                          time_from="now-30m", refresh="10s")


# ─── Public API ───


def generate_dashboard(
    question: str,
    query_type: QueryType,
    domain: QueryDomain,
    target: str | None = None,
    evidence: RetrievedEvidence | None = None,
    baseline: BaselineStats | None = None,
) -> dict[str, Any]:
    """Generate a Grafana dashboard JSON based on query context.

    Returns the dashboard JSON (ready for Grafana API).
    """
    # Select template based on query type and domain
    if query_type in {QueryType.CPU_SPIKE, QueryType.MEMORY_PRESSURE, QueryType.DISK_ALERT}:
        return _dashboard_host_overview(target)

    if query_type in {QueryType.LATENCY_ANOMALY, QueryType.TRACE_LATENCY}:
        return _dashboard_service_latency(target)

    if query_type in {QueryType.ALERT_ACTIVE, QueryType.ALERT_HISTORY}:
        return _dashboard_alerts_overview()

    if query_type == QueryType.SERVICE_DOWN or domain == QueryDomain.CROSS_DOMAIN:
        return _dashboard_error_investigation(target)

    if query_type == QueryType.NETWORK_THROUGHPUT:
        instance = target
        panels = [
            _timeseries_panel("Network Receive", promql_network_rx(instance),
                              grid_x=0, grid_y=0, grid_w=12, grid_h=8,
                              unit="Bps", panel_id=1),
            _timeseries_panel("Network Transmit", promql_network_tx(instance),
                              grid_x=12, grid_y=0, grid_w=12, grid_h=8,
                              unit="Bps", panel_id=2),
        ]
        title = f"Network: {target}" if target else "Network Overview"
        return _wrap_dashboard(title, panels, tags=["infra-rag", "network", "ad-hoc"])

    # Default: host overview
    return _dashboard_host_overview(target)


def create_adhoc_dashboard(
    question: str,
    query_type: QueryType,
    domain: QueryDomain,
    target: str | None = None,
    evidence: RetrievedEvidence | None = None,
    baseline: BaselineStats | None = None,
    time_from: str = "now-1h",
    time_to: str = "now",
) -> dict[str, Any]:
    """Generate a dashboard and push it to Grafana. Returns {uid, url, title}."""
    dashboard_json = generate_dashboard(
        question, query_type, domain, target, evidence, baseline,
    )

    # Override time range
    dashboard_json["time"] = {"from": time_from, "to": time_to}

    try:
        result = grafana_client.create_dashboard(dashboard_json)
        return {
            "uid": result["uid"],
            "url": result["url"],
            "title": dashboard_json.get("title", "Ad-hoc Dashboard"),
            "panels": len(dashboard_json.get("panels", [])),
            "created": True,
        }
    except Exception as e:
        logger.error(f"Failed to create ad-hoc dashboard: {e}")
        # Fallback: return the JSON so user can import manually
        return {
            "uid": dashboard_json.get("uid", ""),
            "url": "",
            "title": dashboard_json.get("title", "Ad-hoc Dashboard"),
            "panels": len(dashboard_json.get("panels", [])),
            "created": False,
            "json": dashboard_json,
            "error": str(e),
        }
