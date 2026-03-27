"""Prometheus HTTP API client for live PromQL queries."""

import logging
from datetime import datetime
from typing import Any

import httpx

from infra_rag.config import settings
from infra_rag.resilience import CircuitBreaker

logger = logging.getLogger(__name__)

_prom_breaker = CircuitBreaker(
    "prometheus",
    failure_threshold=5,
    reset_timeout_s=30,
)


class PrometheusClient:
    def __init__(self):
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            auth = None
            if settings.prometheus.username and settings.prometheus.password:
                auth = (settings.prometheus.username, settings.prometheus.password)
            self._client = httpx.Client(
                base_url=settings.prometheus.url,
                timeout=settings.prometheus.request_timeout_s,
                verify=settings.prometheus.verify_certs,
                auth=auth,
            )
        return self._client

    def query_instant(self, promql: str, time: datetime | None = None) -> dict[str, Any]:
        """Execute an instant PromQL query (current value)."""
        if not _prom_breaker.allow():
            raise RuntimeError("Prometheus circuit breaker is open")

        params: dict[str, str] = {"query": promql}
        if time:
            params["time"] = time.isoformat()

        logger.info(f"Prometheus instant query: {promql}")
        try:
            resp = self.client.get("/api/v1/query", params=params)
            resp.raise_for_status()
            data = resp.json()
            _prom_breaker.record_success()
            if data.get("status") != "success":
                raise RuntimeError(f"Prometheus error: {data.get('error', 'unknown')}")
            return data["data"]
        except Exception as e:
            logger.error(f"Prometheus query failed: {e}")
            _prom_breaker.record_failure()
            raise

    def query_range(
        self,
        promql: str,
        start: datetime,
        end: datetime,
        step: str = "60s",
    ) -> dict[str, Any]:
        """Execute a range PromQL query (time series)."""
        if not _prom_breaker.allow():
            raise RuntimeError("Prometheus circuit breaker is open")

        params = {
            "query": promql,
            "start": str(start.timestamp()),
            "end": str(end.timestamp()),
            "step": step,
        }

        logger.info(f"Prometheus range query: {promql} [{start} -> {end}]")
        try:
            resp = self.client.get("/api/v1/query_range", params=params)
            resp.raise_for_status()
            data = resp.json()
            _prom_breaker.record_success()
            if data.get("status") != "success":
                raise RuntimeError(f"Prometheus error: {data.get('error', 'unknown')}")
            return data["data"]
        except Exception as e:
            logger.error(f"Prometheus range query failed: {e}")
            _prom_breaker.record_failure()
            raise

    def get_alerts(self) -> list[dict[str, Any]]:
        """Get currently firing alerts from Prometheus."""
        if not _prom_breaker.allow():
            raise RuntimeError("Prometheus circuit breaker is open")
        try:
            resp = self.client.get("/api/v1/alerts")
            resp.raise_for_status()
            data = resp.json()
            _prom_breaker.record_success()
            return data.get("data", {}).get("alerts", [])
        except Exception as e:
            logger.error(f"Prometheus alerts query failed: {e}")
            _prom_breaker.record_failure()
            raise

    def get_targets(self) -> list[dict[str, Any]]:
        """Get all scrape targets and their health status."""
        if not _prom_breaker.allow():
            raise RuntimeError("Prometheus circuit breaker is open")
        try:
            resp = self.client.get("/api/v1/targets")
            resp.raise_for_status()
            data = resp.json()
            _prom_breaker.record_success()
            return data.get("data", {}).get("activeTargets", [])
        except Exception as e:
            logger.error(f"Prometheus targets query failed: {e}")
            _prom_breaker.record_failure()
            raise

    def get_label_values(self, label: str) -> list[str]:
        """Get all values for a label (e.g., 'instance', 'job')."""
        try:
            resp = self.client.get(f"/api/v1/label/{label}/values")
            resp.raise_for_status()
            return resp.json().get("data", [])
        except Exception:
            return []

    def is_available(self) -> bool:
        try:
            resp = self.client.get("/api/v1/status/buildinfo")
            return resp.status_code == 200
        except Exception:
            return False

    def close(self):
        if self._client:
            self._client.close()


prom_client = PrometheusClient()


# ─── PromQL template helpers ───

def promql_cpu(instance: str | None = None) -> str:
    inst_filter = f', instance=~"{instance}.*"' if instance else ""
    return f'100 - (avg by(instance) (rate(node_cpu_seconds_total{{mode="idle"{inst_filter}}}[5m])) * 100)'

def promql_memory(instance: str | None = None) -> str:
    filt = f'{{instance=~"{instance}.*"}}' if instance else ""
    return f"(1 - (node_memory_MemAvailable_bytes{filt} / node_memory_MemTotal_bytes{filt})) * 100"

def promql_disk(instance: str | None = None, mountpoint: str = "/") -> str:
    parts = [f'mountpoint="{mountpoint}"']
    if instance:
        parts.append(f'instance=~"{instance}.*"')
    filt = ", ".join(parts)
    return f"(1 - (node_filesystem_avail_bytes{{{filt}}} / node_filesystem_size_bytes{{{filt}}})) * 100"

def promql_network_rx(instance: str | None = None) -> str:
    filt = f'{{instance=~"{instance}.*"}}' if instance else ""
    return f"rate(node_network_receive_bytes_total{filt}[5m])"

def promql_network_tx(instance: str | None = None) -> str:
    filt = f'{{instance=~"{instance}.*"}}' if instance else ""
    return f"rate(node_network_transmit_bytes_total{filt}[5m])"

def promql_up(job: str | None = None) -> str:
    filt = f'{{job="{job}"}}' if job else ""
    return f"up{filt}"
