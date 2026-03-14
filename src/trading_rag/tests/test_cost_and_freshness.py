from datetime import datetime, timezone, timedelta

from trading_rag.cost import CostBudget
from trading_rag.freshness import FreshnessContract
from trading_rag.models import QueryType


def test_cost_budget_exceeded():
    budget = CostBudget()
    state = {"total_llm_tokens": budget.max_tokens + 1, "llm_calls": 0, "cost_usd": 0.0}
    assert budget.exceeded(state) is True


def test_freshness_labels():
    contract = FreshnessContract()
    qt = QueryType.SPIKE_DETECTION
    now = datetime.now(timezone.utc)
    assert contract.label(now, qt) in {"LIVE", "RECENT"}
    stale_time = now - timedelta(seconds=contract.ttl_seconds(qt) + 1)
    assert contract.label(stale_time, qt) == "STALE"
