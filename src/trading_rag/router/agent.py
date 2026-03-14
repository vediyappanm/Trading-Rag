ROUTER_SYSTEM_PROMPT = """You are a query router for a trading log analysis system. Your job is to analyze user questions and determine the best retrieval path.

Available paths:
1. structured_esql: Use when the query asks for structured data, metrics, aggregations, or specific numeric analysis
2. dual_index_correlation: Use when the query involves correlating execution logs with feed logs or comparing two data sources
3. semantic_incident: Use when the query asks about incidents, issues, problems, or events that happened

Output format:
- query_type: One of spike_detection, baseline_compare, venue_analysis, order_drilldown, feed_correlation, exploratory
- query_path: The selected path
- confidence: A score from 0.0 to 1.0 indicating your confidence in the routing decision
- time_window: Extract the time range from the query. IMPORTANT: If no time range is specified, ALWAYS default to the last 24 hours.
- symbol: Extract the main trading symbol (e.g., AAPL). IMPORTANT: If the user is comparing TWO OR MORE symbols (e.g., 'AAPL vs TSLA'), set symbol to null and I will group by all detected symbols automatically.
- esql_query: An ES|QL query for structured paths, or null if not applicable.
- reasoning: Brief explanation of why you chose this path"""

ROUTER_USER_PROMPT = """Analyze this trading log query:

{query}

Determine:
1. Which retrieval path best matches this query
2. Which query_type best matches this query
3. Extract any time window mentioned (e.g., "last hour", "today", "2024-01-01 to 2024-01-02")
4. Extract any trading symbol mentioned
5. Provide your confidence score
6. Provide an ES|QL query if query_type is structured, else null

Current time: {current_time}

Respond with your routing decision."""

from datetime import datetime, timedelta
from typing import Any
import re

from trading_rag.clients import create_llm, structured_output
from trading_rag.config import settings
from trading_rag.models import RouterOutput, QueryPath, TimeWindow, QueryType


def parse_time_from_query(query: str, current_time: datetime) -> TimeWindow | None:
    query_lower = query.lower()
    
    time_patterns = [
        (r"last\s+(\d+)\s+hours?", lambda m: current_time - timedelta(hours=int(m.group(1)))),
        (r"last\s+(\d+)\s+hour", lambda m: current_time - timedelta(hours=int(m.group(1)))),
        (r"last\s+hour", lambda m: current_time - timedelta(hours=1)),
        (r"last\s+(\d+)\s+days?", lambda m: current_time - timedelta(days=int(m.group(1)))),
        (r"last\s+day", lambda m: current_time - timedelta(days=1)),
        (r"today", lambda m: current_time.replace(hour=0, minute=0, second=0, microsecond=0)),
        (r"yesterday", lambda m: (current_time - timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)),
    ]
    
    for pattern, parser in time_patterns:
        if re.search(pattern, query_lower):
            match = re.search(pattern, query_lower)
            start = parser(match)
            end = current_time
            return TimeWindow(start=start, end=end)
    
    date_pattern = r"(\d{4}-\d{2}-\d{2})\s+(?:to|until|-)\s+(\d{4}-\d{2}-\d{2})"
    date_match = re.search(date_pattern, query)
    if date_match:
        try:
            start = datetime.fromisoformat(date_match.group(1))
            end = datetime.fromisoformat(date_match.group(2))
            return TimeWindow(start=start, end=end)
        except ValueError:
            pass
    
    return None


def extract_symbol(query: str) -> str | None:
    patterns = [
        r"\b([A-Z]{1,5})\b",
        r"symbol[:\s]+([A-Z]+)",
        r"([A-Z]{2,5})\s+(?:stock|token|forex)",
    ]
    
    common_words = {
        "I", "A", "THE", "API", "UTC", "USD", "EUR", "GBP", "JSON", "SQL", "ES", "LLM", "AI",
        "WHAT", "HOW", "WHEN", "WHERE", "WHY", "SHOW", "LIST", "GET", "FIND", "ALL", "ANY",
        "AND", "OR", "BUT", "NOT", "FOR", "WITH", "FROM", "TO", "IN", "ON", "AT", "BY", "LAST",
        "ME", "OF", "VS", "VERSUS", "OVER", "COMPARE",
        "HOUR", "HOURS", "DAY", "DAYS", "TODAY", "YESTERDAY",
        "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "BEING", "HAVE", "HAS", "HAD", "DO", "DOES", "DID",
        "TRADES", "LOGS", "DATA", "FEED", "EXEC", "AVG", "MAX", "MIN", "SUM", "COUNT"
    }
    
    # Try more specific patterns first
    for pattern in patterns[1:]:
        match = re.search(pattern, query.upper())
        if match:
            symbol = match.group(1)
            if symbol not in common_words:
                return symbol
                
    # Fallback to general 1-5 letter word match (scan all candidates)
    for match in re.finditer(patterns[0], query.upper()):
        symbol = match.group(1)
        if symbol not in common_words:
            return symbol
    
    return None


def route_query(query: str) -> RouterOutput:
    current_time = datetime.utcnow()
    
    time_window = parse_time_from_query(query, current_time)
    if time_window is None:
        # Default to last 24 hours for better retrieval coverage
        time_window = TimeWindow(
            start=current_time - timedelta(hours=24),
            end=current_time,
        )
    
    symbols = _extract_symbols(query)
    symbol = symbols[0] if len(symbols) == 1 else None
    
    query_lower = query.lower()
    
    query_type = QueryType.BASELINE_COMPARE
    if any(kw in query_lower for kw in ["incident", "issue", "problem", "error", "failure", "outage", "what happened"]):
        query_path = QueryPath.SEMANTIC_INCIDENT
        query_type = QueryType.EXPLORATORY
        confidence = 0.85
    elif any(kw in query_lower for kw in ["correlate", "correlation", "compare", "feed", "execution vs", "feed vs"]):
        query_path = QueryPath.DUAL_INDEX_CORRELATION
        query_type = QueryType.FEED_CORRELATION
        confidence = 0.80
    elif any(kw in query_lower for kw in ["venue", "gateway", "exchange"]):
        query_path = QueryPath.STRUCTURED_ESQL
        query_type = QueryType.VENUE_ANALYSIS
        confidence = 0.85
    elif any(kw in query_lower for kw in ["order", "order_id", "order id", "drilldown"]):
        query_path = QueryPath.STRUCTURED_ESQL
        query_type = QueryType.ORDER_DRILLDOWN
        confidence = 0.80
    elif any(kw in query_lower for kw in ["spike", "spiked", "p95", "p99"]):
        query_path = QueryPath.STRUCTURED_ESQL
        query_type = QueryType.SPIKE_DETECTION
        confidence = 0.85
    elif any(kw in query_lower for kw in ["how many", "average", "total", "count", "sum", "metrics", "statistics", "latency", "volume"]):
        query_path = QueryPath.STRUCTURED_ESQL
        query_type = QueryType.BASELINE_COMPARE
        confidence = 0.90
    else:
        query_path = QueryPath.STRUCTURED_ESQL
        query_type = QueryType.BASELINE_COMPARE
        confidence = 0.60
    
    import logging
    logger = logging.getLogger(__name__)
    logger.info(f"Routing query: '{query}'")
    logger.info(f"Detected Symbol: {symbol}, Path: {query_path}")
    logger.info(f"Time Window: {time_window.start.isoformat()} to {time_window.end.isoformat()}")
    
    esql_query = _default_esql(query_type, time_window, symbols, query)
    reasoning = f"Query contains keywords suggesting {query_path.value} path. Time window: {time_window.start.isoformat()} to {time_window.end.isoformat()}. Symbol: {symbol or 'none'}"
    
    return RouterOutput(
        query_type=query_type,
        query_path=query_path,
        confidence=confidence,
        time_window=time_window,
        symbol=symbol,
        esql_query=esql_query,
        reasoning=reasoning,
    )


def route_query_llm(query: str) -> RouterOutput:
    llm = create_llm(settings.llm.router_model)
    
    prompt = ROUTER_USER_PROMPT.format(
        query=query,
        current_time=datetime.utcnow().isoformat(),
    )
    
    result = structured_output(llm, RouterOutput, prompt)
    
    import logging
    logger = logging.getLogger(__name__)
    if result:
        parsed = parse_time_from_query(query, datetime.utcnow())
        if result.time_window is None:
            current_time = datetime.utcnow()
            result.time_window = TimeWindow(
                start=current_time - timedelta(hours=24),
                end=current_time,
            )
        if parsed is not None:
            result.time_window = parsed
        symbols = _extract_symbols(query)
        if result.symbol and "," in str(result.symbol):
            symbols = [s.strip().upper() for s in str(result.symbol).split(",") if s.strip()]
            result.symbol = symbols[0] if len(symbols) == 1 else None
        if result.esql_query is None or not result.esql_query.strip().lower().startswith("from"):
            result.esql_query = _default_esql(result.query_type, result.time_window, symbols, query)
        logger.info(f"LLM Routed query: '{query}'")
        logger.info(f"Decision: {result.query_path}, Symbol: {result.symbol}, Confidence: {result.confidence}")
        if result.time_window:
            logger.info(f"Time Window: {result.time_window.start.isoformat()} to {result.time_window.end.isoformat()}")
        return result
    
    logger.warning(f"LLM Routing failed for '{query}', falling back to regex.")
    return route_query(query)


def _default_esql(
    query_type: QueryType,
    time_window: TimeWindow,
    symbols: list[str],
    query: str,
) -> str | None:
    if not time_window:
        return None
    if symbols:
        symbol_values = ", ".join(f'"{s.upper()}"' for s in symbols)
        symbol_filter = f"AND symbol IN ({symbol_values})"
    else:
        symbol_filter = ""
    base = f'@timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"'

    if query_type == QueryType.SPIKE_DETECTION:
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter}
| STATS p95_latency = PERCENTILE(latency_ms, 95), error_rate = AVG(CASE(status == "error", 1.0, 0.0))
| LIMIT 1
"""
    if query_type == QueryType.BASELINE_COMPARE:
        by_symbol = " BY symbol" if len(symbols) > 1 else ""
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter}
| STATS avg_latency = AVG(latency_ms), avg_volume = AVG(volume), error_rate = AVG(CASE(status == "error", 1.0, 0.0)){by_symbol}
| LIMIT {max(1, len(symbols)) if len(symbols) > 1 else 1}
"""
    if query_type == QueryType.VENUE_ANALYSIS:
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter}
| STATS avg_latency = AVG(latency_ms), error_rate = AVG(CASE(status == "error", 1.0, 0.0)) BY venue
| SORT avg_latency DESC
| LIMIT 50
"""
    if query_type == QueryType.ORDER_DRILLDOWN:
        order_match = re.search(r"order[_\\s-]?id[:\\s]+([A-Za-z0-9\\-_.]+)", query, re.IGNORECASE)
        if not order_match:
            return None
        order_id = order_match.group(1)
        return f"""
FROM "trading-execution-logs"
| WHERE {base} AND order_id == "{order_id}" {symbol_filter}
| SORT @timestamp DESC
| LIMIT 200
| KEEP @timestamp, message, symbol, status, order_id
"""
    return None


def _extract_symbols(query: str) -> list[str]:
    symbols = []
    for match in re.finditer(r"\b([A-Z]{2,5})\b", query.upper()):
        sym = match.group(1)
        if sym and extract_symbol(sym) == sym:
            symbols.append(sym)
    # Deduplicate while preserving order
    seen = set()
    out = []
    for s in symbols:
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
