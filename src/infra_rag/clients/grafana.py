"""Grafana HTTP API client for dashboard/panel discovery and deep-linking."""

import json
import logging
from typing import Any
from urllib.parse import urlencode

import httpx

from infra_rag.config import settings
from infra_rag.resilience import CircuitBreaker

logger = logging.getLogger(__name__)

_grafana_breaker = CircuitBreaker(
    "grafana",
    failure_threshold=5,
    reset_timeout_s=30,
)


class GrafanaClient:
    def __init__(self):
        self._client: httpx.Client | None = None

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if settings.grafana.api_key:
                headers["Authorization"] = f"Bearer {settings.grafana.api_key}"
            self._client = httpx.Client(
                base_url=settings.grafana.url,
                timeout=settings.grafana.request_timeout_s,
                verify=settings.grafana.verify_certs,
                headers=headers,
            )
        return self._client

    def search_dashboards(
        self, query: str = "", tag: str | None = None, limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search dashboards by query or tag."""
        if not _grafana_breaker.allow():
            raise RuntimeError("Grafana circuit breaker is open")

        params: dict[str, Any] = {"type": "dash-db", "limit": limit}
        if query:
            params["query"] = query
        if tag:
            params["tag"] = tag

        try:
            resp = self.client.get("/api/search", params=params)
            resp.raise_for_status()
            _grafana_breaker.record_success()
            return resp.json()
        except Exception as e:
            logger.error(f"Grafana search failed: {e}")
            _grafana_breaker.record_failure()
            raise

    def get_dashboard(self, uid: str) -> dict[str, Any]:
        """Get full dashboard by UID."""
        if not _grafana_breaker.allow():
            raise RuntimeError("Grafana circuit breaker is open")
        try:
            resp = self.client.get(f"/api/dashboards/uid/{uid}")
            resp.raise_for_status()
            _grafana_breaker.record_success()
            return resp.json()
        except Exception as e:
            logger.error(f"Grafana get dashboard failed: {e}")
            _grafana_breaker.record_failure()
            raise

    def get_dashboard_panels(self, uid: str) -> list[dict[str, Any]]:
        """Extract panel info (id, title, type, PromQL queries) from a dashboard."""
        data = self.get_dashboard(uid)
        dashboard = data.get("dashboard", {})
        panels = []

        def extract(panel: dict) -> dict:
            info: dict[str, Any] = {
                "id": panel.get("id"),
                "title": panel.get("title", ""),
                "type": panel.get("type", ""),
            }
            targets = panel.get("targets", [])
            if targets:
                info["queries"] = [t.get("expr", "") for t in targets if t.get("expr")]
            return info

        for panel in dashboard.get("panels", []):
            panels.append(extract(panel))
            for sub in panel.get("panels", []):
                panels.append(extract(sub))

        return panels

    def build_dashboard_url(
        self,
        uid: str,
        from_ts: str = "now-1h",
        to_ts: str = "now",
        var_host: str | None = None,
        panel_id: int | None = None,
    ) -> str:
        """Build a deep-link URL to a Grafana dashboard or specific panel."""
        base = f"{settings.grafana.url.rstrip('/')}/d/{uid}"
        params: dict[str, str] = {"from": from_ts, "to": to_ts}
        if var_host:
            params["var-host"] = var_host
            params["var-instance"] = var_host
            params["var-node"] = var_host
        if panel_id is not None:
            params["viewPanel"] = str(panel_id)
        return f"{base}?{urlencode(params)}"

    def build_explore_url(
        self,
        promql: str,
        from_ts: str = "now-1h",
        to_ts: str = "now",
        datasource: str = "Prometheus",
    ) -> str:
        """Build a Grafana Explore URL for an ad-hoc PromQL query."""
        left = json.dumps({
            "datasource": datasource,
            "queries": [{"expr": promql, "refId": "A"}],
            "range": {"from": from_ts, "to": to_ts},
        })
        return f"{settings.grafana.url}/explore?left={left}"

    def find_dashboards_for_target(self, target: str) -> list[dict[str, Any]]:
        """Find dashboards relevant for a hostname or service."""
        results = []
        seen_uids: set[str] = set()

        # Direct search
        try:
            for d in self.search_dashboards(query=target, limit=5):
                uid = d.get("uid", "")
                if uid not in seen_uids:
                    results.append(d)
                    seen_uids.add(uid)
        except Exception:
            pass

        # Tag-based search
        for tag in ["node", "host", "server", "infrastructure", "application", "service"]:
            try:
                for d in self.search_dashboards(tag=tag, limit=3):
                    uid = d.get("uid", "")
                    if uid not in seen_uids:
                        results.append(d)
                        seen_uids.add(uid)
            except Exception:
                pass

        return results[:10]

    def create_dashboard(self, dashboard_json: dict[str, Any], folder_id: int = 0) -> dict[str, Any]:
        """Create or update a dashboard via the Grafana API. Returns uid + url."""
        if not _grafana_breaker.allow():
            raise RuntimeError("Grafana circuit breaker is open")

        payload = {
            "dashboard": dashboard_json,
            "folderId": folder_id,
            "overwrite": True,
        }
        try:
            resp = self.client.post("/api/dashboards/db", json=payload)
            resp.raise_for_status()
            _grafana_breaker.record_success()
            result = resp.json()
            return {
                "uid": result.get("uid", ""),
                "url": f"{settings.grafana.url.rstrip('/')}{result.get('url', '')}",
                "id": result.get("id"),
                "status": result.get("status", ""),
            }
        except Exception as e:
            logger.error(f"Grafana create dashboard failed: {e}")
            _grafana_breaker.record_failure()
            raise

    def delete_dashboard(self, uid: str) -> bool:
        """Delete a dashboard by UID."""
        try:
            resp = self.client.delete(f"/api/dashboards/uid/{uid}")
            return resp.status_code == 200
        except Exception:
            return False

    def is_available(self) -> bool:
        try:
            resp = self.client.get("/api/health")
            return resp.status_code == 200
        except Exception:
            return False

    def close(self):
        if self._client:
            self._client.close()


grafana_client = GrafanaClient()
