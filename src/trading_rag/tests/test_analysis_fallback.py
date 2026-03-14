from trading_rag.analysis.agent import generate_analysis
from trading_rag.models import RetrievedEvidence, LogEntry, BaselineStats, QueryPath


def test_generate_analysis_fallback_includes_citations():
    evidence = RetrievedEvidence(
        logs=[
            LogEntry(id="log-1", timestamp="2026-03-13T10:00:00Z", message="Test", symbol="BTC"),
            LogEntry(id="log-2", timestamp="2026-03-13T10:01:00Z", message="Test2", symbol="BTC"),
        ],
        aggregations={},
        query_used="test",
        path=QueryPath.STRUCTURED_ESQL,
    )
    baseline = BaselineStats(symbol="BTC", hour=10)
    result = generate_analysis("What happened?", evidence, baseline)
    assert result.citations, "citations should be present for logs"
