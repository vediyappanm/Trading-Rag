from trading_rag.models import RetrievedEvidence, QueryPath, TimeWindow, QueryType
from trading_rag.retrieval.services import (
    retrieve_execution_logs,
    retrieve_with_aggregation,
    correlate_execution_and_feed,
    semantic_search_incidents,
    retrieve_with_esql_query,
)


def retrieve_evidence(
    query_path: QueryPath,
    query_type: QueryType | None,
    query: str,
    time_window: TimeWindow,
    symbol: str | None = None,
    esql_query: str | None = None,
) -> RetrievedEvidence:
    if query_path == QueryPath.STRUCTURED_ESQL:
        if esql_query:
            return retrieve_with_esql_query(esql_query, time_window, QueryPath.STRUCTURED_ESQL)
        if query_type in {QueryType.BASELINE_COMPARE, QueryType.SPIKE_DETECTION, QueryType.VENUE_ANALYSIS}:
            return retrieve_with_aggregation(time_window, symbol)
        return retrieve_execution_logs(time_window, symbol)
    
    elif query_path == QueryPath.DUAL_INDEX_CORRELATION:
        return correlate_execution_and_feed(time_window, symbol)
    
    elif query_path == QueryPath.SEMANTIC_INCIDENT:
        return semantic_search_incidents(query, time_window)
    
    return retrieve_execution_logs(time_window, symbol)
