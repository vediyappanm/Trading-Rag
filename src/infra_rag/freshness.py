from datetime import datetime
from infra_rag.config import settings
from infra_rag.models import QueryType


class FreshnessContract:
    _TTL_MAP = {
        QueryType.CPU_SPIKE: "ttl_cpu_spike",
        QueryType.MEMORY_PRESSURE: "ttl_memory_pressure",
        QueryType.DISK_ALERT: "ttl_disk_alert",
        QueryType.LATENCY_ANOMALY: "ttl_latency_anomaly",
        QueryType.SERVICE_DOWN: "ttl_service_down",
        QueryType.ERROR_SEARCH: "ttl_error_search",
        QueryType.ALERT_HISTORY: "ttl_alert_history",
        QueryType.ALERT_ACTIVE: "ttl_alert_history",
        QueryType.CAPACITY_PLANNING: "ttl_capacity_planning",
        QueryType.BASELINE_COMPARE: "ttl_baseline_compare",
        QueryType.EXPLORATORY: "ttl_exploratory",
    }

    def ttl(self, query_type: QueryType | None) -> int:
        if query_type is None:
            return settings.freshness.ttl_exploratory
        attr = self._TTL_MAP.get(query_type, "ttl_exploratory")
        return getattr(settings.freshness, attr, 300)

    def label(self, cached_at: str | None, query_type: QueryType | None) -> str:
        ttl = self.ttl(query_type)
        if cached_at:
            try:
                cached_time = datetime.fromisoformat(cached_at)
                age_s = (datetime.utcnow() - cached_time).total_seconds()
                if age_s <= ttl:
                    return "fresh"
                elif age_s <= ttl * 3:
                    return "stale"
                return "expired"
            except Exception:
                pass
        if ttl <= 30:
            return "realtime"
        elif ttl <= 120:
            return "near-realtime"
        return "historical"
