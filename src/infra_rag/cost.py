import math

from infra_rag.config import settings


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


class CostBudget:
    def __init__(self):
        self.max_tokens = settings.api.cost_budget_max_tokens
        self.max_calls = settings.api.cost_budget_max_llm_calls
        self.max_usd = settings.api.cost_budget_max_usd

    def update(self, state: dict, prompt: str, response: str | None = None) -> None:
        tokens = estimate_tokens(prompt) + estimate_tokens(response or "")
        state["total_llm_tokens"] = state.get("total_llm_tokens", 0) + tokens
        state["llm_calls"] = state.get("llm_calls", 0) + 1
        cost = (state["total_llm_tokens"] / 1000.0) * settings.llm.cost_per_1k_tokens_usd
        state["cost_usd"] = round(cost, 6)

    def exceeded(self, state: dict) -> bool:
        if state.get("total_llm_tokens", 0) > self.max_tokens:
            return True
        if state.get("llm_calls", 0) > self.max_calls:
            return True
        if state.get("cost_usd", 0.0) > self.max_usd:
            return True
        return False
