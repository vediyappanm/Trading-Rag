from datetime import datetime, timezone

from trading_rag.config import settings
from trading_rag.models import QueryType


class FreshnessContract:
    def ttl_seconds(self, query_type: QueryType) -> int:
        return {
            QueryType.SPIKE_DETECTION: settings.freshness.ttl_spike_detection,
            QueryType.ORDER_DRILLDOWN: settings.freshness.ttl_order_drilldown,
            QueryType.BASELINE_COMPARE: settings.freshness.ttl_baseline_compare,
            QueryType.VENUE_ANALYSIS: settings.freshness.ttl_venue_analysis,
            QueryType.FEED_CORRELATION: settings.freshness.ttl_feed_correlation,
            QueryType.EXPLORATORY: settings.freshness.ttl_exploratory,
        }.get(query_type, settings.freshness.ttl_exploratory)

    def label(self, cached_at: datetime | None, query_type: QueryType) -> str:
        ttl = self.ttl_seconds(query_type)
        if cached_at is None:
            return "LIVE"
        age = (datetime.now(timezone.utc) - cached_at).total_seconds()
        if age < ttl * 0.5:
            return "LIVE"
        if age < ttl:
            return "RECENT"
        return "STALE"
