from datetime import datetime, timedelta

from trading_rag.retrieval.services import retrieve_execution_logs
from trading_rag.models import TimeWindow, QueryPath


class DummyESClient:
    def __init__(self):
        self.last_query = None

    def execute_esql(self, query: str, time_window: dict | None = None):
        self.last_query = query
        return {"values": []}

    def get_execution_logs_index(self) -> str:
        return "exec-index"


def test_execution_logs_query_has_sort_and_limit(monkeypatch):
    dummy = DummyESClient()
    monkeypatch.setattr("trading_rag.retrieval.services.es_client", dummy)
    now = datetime(2026, 3, 13, 12, 0, 0)
    tw = TimeWindow(start=now - timedelta(hours=1), end=now)
    result = retrieve_execution_logs(tw, symbol=None, limit=10)
    assert result.path == QueryPath.STRUCTURED_ESQL
    assert "SORT @timestamp DESC" in dummy.last_query
    assert "LIMIT 10" in dummy.last_query
