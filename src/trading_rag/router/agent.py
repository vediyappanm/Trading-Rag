ROUTER_SYSTEM_PROMPT = """You are a query router for the Finspot Rag trading log analysis system. The data is real NSE/NFO/BSE order journal logs from the Noren OMS (Order Management System) serving 65+ Indian stock brokers.

Key fields in the Elasticsearch index 'trading-execution-logs':
- @timestamp: order event time
- TradingSymbol: full symbol (e.g. RELIANCE-EQ, NIFTY27JAN26F, ICICIBANK27JAN26C1000)
- ticker: base symbol without suffix (e.g. RELIANCE, NIFTY, ICICIBANK)
- OrdStatus: 48=FILLED, 65=OPEN/PENDING, 110=NEW, 67=CANCELLED, 56=REJECTED
- QtyToFill: order quantity (shares/lots)
- PriceToFill: order price in paise (divide by 100 for rupees)
- TransType: B=Buy, S=Sell
- ExchSeg: NSE, NFO, BSE, BFO, CDS
- BrokerId: broker code (EST, ISB, CSB, MES, etc.)
- Product: I=Intraday, C=Delivery/CNC, M=Margin
- PriceType: LMT=Limit, MKT=Market, SL-LMT=Stop-Loss-Limit, SL-MKT=Stop-Loss-Market
- NorenOrdNum: unique order number
- AcctId: client account ID
- msg_type: ordupd, login, logout

Available paths:
1. structured_esql: Use for metrics, aggregations, counts, volume analysis, broker analysis, exchange analysis
2. dual_index_correlation: Use for buy/sell imbalance analysis, order flow patterns, TransType comparisons
3. semantic_incident: Use when asking about specific orders, accounts, or keyword searches

Output format:
- query_type: One of spike_detection, baseline_compare, venue_analysis, order_drilldown, feed_correlation, exploratory
- query_path: The selected path
- confidence: A score from 0.0 to 1.0
- time_window: Extract the time range. IMPORTANT: If no time range is specified, ALWAYS default to the last 24 hours.
- symbol: Extract the base ticker (e.g. RELIANCE, NIFTY, ICICIBANK). If comparing multiple symbols, set to null.
- esql_query: An ES|QL query for structured paths, or null if not applicable.
- reasoning: Brief explanation of routing decision"""

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
    # Only match explicit absolute date ranges — relative patterns (today/yesterday/last N hours)
    # are unreliable since data is historical (Jan 2026). Let caller apply the 365-day default.
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


# Words that look like tickers but should never be treated as trading symbols.
# This is used by both the regex extractor and the LLM symbol validator.
_COMMON_WORDS: set[str] = {
    "I", "A", "THE", "API", "UTC", "USD", "EUR", "GBP", "JSON", "SQL", "ES", "LLM", "AI",
    "WHAT", "HOW", "WHEN", "WHERE", "WHY", "SHOW", "LIST", "GET", "FIND", "ALL", "ANY",
    "AND", "OR", "BUT", "NOT", "FOR", "WITH", "FROM", "TO", "IN", "ON", "AT", "BY", "LAST",
    "ME", "OF", "VS", "VERSUS", "OVER", "COMPARE",
    "HOUR", "HOURS", "DAY", "DAYS", "TODAY", "YESTERDAY",
    "IS", "ARE", "WAS", "WERE", "BE", "BEEN", "BEING", "HAVE", "HAS", "HAD", "DO", "DOES", "DID",
    "TRADES", "LOGS", "DATA", "FEED", "EXEC", "AVG", "MAX", "MIN", "SUM", "COUNT",
    # Indian market / Noren field terms
    "EQ", "NSE", "NFO", "BSE", "BFO", "CDS", "FUT", "OPT", "CE", "PE",
    "LMT", "MKT", "SL", "AMO", "CNC", "MIS", "NRML",
    "BUY", "SELL", "ORDER", "ORDERS", "BROKER", "BROKERS", "ACCOUNT",
    "FILL", "FILLED", "CANCEL", "CANCELLED", "REJECT", "REJECTED",
    "INTRADAY", "DELIVERY", "MARGIN", "MARKET", "LIMIT", "STOP",
    # F&O / market terms that look like tickers
    "FUTURES", "FUTURE", "OPTIONS", "OPTION", "CALLS", "PUTS",
    "VOLUMES", "VOLUME", "VERSUS", "COMPARE",
    # Venue/exchange words that LLM sometimes returns as symbol
    "VENUE", "VENUES", "EXCHANGE", "EXCHANGES", "SEGMENT", "SEGMENTS",
    # Common English words often misidentified as tickers
    "WHICH", "MOST", "MORE", "THEIR", "THERE", "THESE", "THOSE", "THAN",
    "EACH", "MANY", "MUCH", "SOME", "SUCH", "VERY", "ALSO", "JUST", "ONLY",
    "EVEN", "BOTH", "THEN", "WELL", "INTO",
    "TOP", "HIGH", "LOW", "BEST", "RATE", "GOOD", "LESS",
    "MAKE", "TAKE", "GIVE", "COME", "KNOW", "LOOK", "WANT", "NEED",
    "TELL", "SHOW", "FEEL", "SEEM", "KEEP", "CALL", "WORK", "MEAN",
    "LONG", "BACK", "REAL", "SAME", "TURN", "MOVE",
    "LIVE", "SAID", "SAYS", "USED", "BASE", "OPEN",
    "CASE", "KIND", "HELP", "NEXT", "LATE", "AWAY", "NEAR",
    "PAST", "TRUE", "TYPE", "ONCE", "FORM", "SIDE", "AREA", "PART",
    "SORT", "RANK", "STAT", "INFO", "LEVEL", "VALUE", "TOTAL",
    "ACTIVE", "STATUS", "REPORT", "RESULT", "QUERY", "FLOW",
    "ANALYSIS", "PATTERN", "PRICE", "TRADE", "TRANSACTION",
    "LATENCY", "OVERALL", "RATES",
    "COUNTS", "METRICS", "STATS", "PENDING",
    "ACROSS", "BETWEEN", "DURING", "WITHIN", "ALONG", "ABOUT",
    "ABOVE", "BELOW", "BEFORE", "AFTER", "UNDER", "SINCE",
    "HAVING", "USING", "BASED", "GIVEN", "WHILE",
    "EVERY", "OTHER", "FIRST", "SECOND", "THIRD", "FOURTH",
    "BREAKDOWN", "SUMMARY", "OVERVIEW", "DETAILS", "COMPARISON",
    "SYMBOLS", "ACCOUNTS", "PRODUCTS", "GROUPS",
    "MINUTES", "SECONDS", "MONTHS", "WEEKS", "YEARS",
    "NUMBER", "NUMBERS", "AMOUNT", "AMOUNTS",
    "SYSTEM", "SYSTEMS", "SERVER", "SERVICE", "DATABASE",
    "PLATFORM", "NETWORK", "CHANNEL", "ENGINE", "MODULE",
    # -TION endings that look like tickers
    "REJECTION", "CANCELLATION", "EXECUTION",
    "CALCULATION", "CORRELATION", "DISTRIBUTION", "CONFIRMATION",
    "ALLOCATION", "COMPLETION", "EXPIRATION", "GENERATION",
    "INDICATION", "NOTIFICATION", "OBSERVATION", "RESOLUTION",
    "SITUATION", "VALIDATION", "VARIATION",
    # Other verbose words
    "PLACED", "TRADED", "AVERAGE", "FILTERED", "GROUPED",
    "SORTED", "RANKED", "COUNTED", "SELECTED",
    # Words that appear in common questions but aren't tickers
    "HAPPENED", "UPDATED", "RECENT", "RECENTLY", "EXECUTED", "PROCESSED",
    "RECEIVED", "CREATED", "MODIFIED", "DELETED", "FAILED", "PASSED",
    "STARTED", "STOPPED", "RUNNING", "WAITING",
    "CHANGED", "MOVED", "TRANSFERRED",
    # Adverbs / temporal qualifiers
    "RECENTLY", "CURRENTLY", "USUALLY", "TYPICALLY", "GENERALLY",
    "ALWAYS", "NEVER", "OFTEN", "SOMETIMES", "ALREADY", "STILL",
    "AGAIN", "MAYBE", "PLEASE", "LATEST", "PREVIOUS", "EARLIER",
    # Personal / possessive pronouns
    "MY", "YOUR", "OUR", "WE", "YOU", "US", "IT", "ITS", "HE", "HIS", "HER",
    # Short common words missing from earlier
    "IF", "SO", "UP", "NO", "GO", "AN", "AS", "DO", "BE",
    "NOW", "OUT", "OFF", "OWN", "OLD", "NEW", "FEW", "TWO",
    # Common verbs (imperative / question forms)
    "CAN", "COULD", "WILL", "WOULD", "SHOULD", "MAY", "MIGHT", "SHALL",
    "TRACE", "CHECK", "TRACK", "FIND", "MONITOR", "WATCH", "ALERT", "FLAG",
    "REVIEW", "DETECT", "IDENTIFY", "INVESTIGATE",
    # Adjectives describing market events
    "EXACT", "PRECISE", "SPECIFIC", "GENERAL", "SHARP", "HEAVY",
    "RAPID", "SUDDEN", "SLOW", "FAST", "QUICK", "LARGE", "SMALL",
    "HIGH", "LOW", "DEEP", "WIDE", "NARROW", "STRONG", "WEAK",
    # Nouns that look like tickers
    "STRATEGY", "SPIKE", "SPIKES", "OUTAGE", "FAILURE", "ALERT",
    "THRESHOLD", "SIGNAL", "CONCERN", "RISK", "LOSS", "GAIN", "PROFIT",
    "PERFORMANCE", "DEVIATION", "DIVERGENCE", "MISMATCH", "IMBALANCE",
    # Verb forms from trading context questions
    "EXECUTING", "HAPPENING", "COLLAPSING", "COLLAPSED", "SPIKING",
    "DROPPING", "RISING", "DECLINING", "FALLING", "SURGING", "CRASHING",
    "RECOVERING", "MONITORING", "TRACKING", "CHECKING", "COMPARING",
    "LOOKING", "GETTING", "PLACING", "TRADING", "ROUTING",
    # Adverbs / qualifiers missing earlier
    "HEAVILY", "SHARPLY", "RAPIDLY", "SPECIFICALLY", "EXACTLY",
    "PRECISELY", "SIGNIFICANTLY", "DRAMATICALLY", "SUBSTANTIALLY",
    "BROADLY", "LARGELY", "MOSTLY", "PARTLY", "SLIGHTLY",
    # Question / compound-query words
    "COMBINED", "TOGETHER", "SEPARATELY", "VERSUS", "AGAINST",
    "MISALIGNED", "ALIGNED", "CORRELATED", "UNCORRELATED",
    "BREAKDOWN", "DRILLDOWN", "ROLLUP", "AGGREGATE",
    # Options / derivatives terms that aren't tickers
    "OTM", "ITM", "ATM", "EXPIRY", "EXPIRATION", "EXERCISE", "STRIKE",
    "PREMIUM", "DELTA", "GAMMA", "THETA", "VEGA",
    # More English words from user queries
    "LIKE", "RIGHT", "MONEY", "GENERAL", "AROUND", "EXACT",
    "WHY", "ALSO", "TELL", "HAPPENING", "TRACE", "OUTAGE", "COLLAPSE",
    # Demonstratives / determiners
    "THIS", "THAT", "THOSE", "THESE",
    # Time / place nouns
    "TIME", "TIMES", "DATE", "MOMENT", "PERIOD", "POINT",
    "PLACE", "LOCATION", "POSITION",
    # Generic financial terms not tied to a single symbol
    "SYMBOL", "TOKEN", "TOKENS", "ASSET", "INSTRUMENT",
    "CONTRACT", "SECURITY", "POSITION", "HOLDING",
    # Plural / alternate forms of words already in the list
    "DROP", "DROPS", "RISE", "RISES", "FALL", "FALLS",
    "JUMP", "JUMPS", "MOVE", "MOVES", "SHIFT", "SHIFTS",
    "REJECTIONS", "CANCELLATIONS", "FILLS", "EXECUTIONS",
    "ERRORS", "ISSUES", "PROBLEMS", "INCIDENTS", "FAILURES",
    "ALERTS", "SIGNALS", "TRIGGERS",
    # Sentence structure words
    "ALSO", "BOTH", "AND", "BUT", "OR", "NOR", "EITHER",
    "NEITHER", "HOWEVER", "ALTHOUGH", "BECAUSE", "SINCE",
    "WHILE", "WHEREAS", "UNLESS", "UNTIL", "THOUGH",
}


def extract_symbol(query: str) -> str | None:
    patterns = [
        r"\b([A-Z]{2,15})\b",
        r"symbol[:\s]+([A-Z]+)",
        r"([A-Z]{2,15})\s+(?:stock|token|forex)",
    ]

    # Try more specific patterns first
    for pattern in patterns[1:]:
        match = re.search(pattern, query.upper())
        if match:
            symbol = match.group(1)
            if symbol not in _COMMON_WORDS:
                return symbol

    # Fallback to general 2-15 letter word match (scan all candidates)
    for match in re.finditer(patterns[0], query.upper()):
        symbol = match.group(1)
        if symbol not in _COMMON_WORDS:
            return symbol

    return None


def route_query(query: str) -> RouterOutput:
    current_time = datetime.utcnow()
    
    time_window = parse_time_from_query(query, current_time)
    if time_window is None:
        # Default to last 365 days — covers all historical/ingested data
        time_window = TimeWindow(
            start=current_time - timedelta(days=365),
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
        # Always override time window to 365 days — LLM sets "today" which misses historical data
        current_time = datetime.utcnow()
        result.time_window = TimeWindow(
            start=current_time - timedelta(days=365),
            end=current_time,
        )
        # Trust the LLM's symbol when it identified one — avoids false positives like
        # "VOLUMES" and "FUTURES" being picked up by regex from queries like
        # "Compare buy vs sell volumes for NIFTY futures today".
        # However, validate the LLM symbol against _COMMON_WORDS — the LLM sometimes
        # returns "VENUES", "UPDATED", "HAPPENED" etc. which are not trading symbols.
        llm_sym = str(result.symbol or "").strip().upper()
        if llm_sym and llm_sym not in ("NULL", "NONE", "") and llm_sym not in _COMMON_WORDS:
            if "," in llm_sym:
                candidates = [s.strip() for s in llm_sym.split(",") if s.strip() and s.strip() not in _COMMON_WORDS]
                symbols = candidates
                result.symbol = candidates[0] if len(candidates) == 1 else None
            else:
                symbols = [llm_sym]
                result.symbol = llm_sym
        else:
            # LLM found no valid symbol — fall back to regex extraction
            symbols = _extract_symbols(query)
            result.symbol = symbols[0] if len(symbols) == 1 else None
        # Always regenerate ESQL from our smart defaults — LLM often produces bad queries
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

    ql = query.lower()
    base = f'@timestamp >= "{time_window.start.isoformat()}" AND @timestamp <= "{time_window.end.isoformat()}"'

    # Symbol filter: use STARTS_WITH(TradingSymbol, ...) because F&O symbols like
    # NIFTY20JAN26F have no dash — their `ticker` field equals the full symbol, not base name.
    # STARTS_WITH covers both equity (RELIANCE-EQ → RELIANCE) and F&O (NIFTY20JAN26F → NIFTY).
    if symbols:
        if len(symbols) == 1:
            sym = symbols[0].upper()
            # Include BrokerId so queries like "how many KTD orders rejected" work
            # (KTD is a broker code, not a TradingSymbol)
            symbol_filter = (
                f'AND (BrokerId == "{sym}" OR ticker == "{sym}" OR STARTS_WITH(TradingSymbol, "{sym}"))'
            )
        else:
            parts = " OR ".join(
                f'BrokerId == "{s.upper()}" OR ticker == "{s.upper()}" OR STARTS_WITH(TradingSymbol, "{s.upper()}")'
                for s in symbols
            )
            symbol_filter = f"AND ({parts})"
    else:
        symbol_filter = ""

    # ── Broker-level queries ──
    if any(kw in ql for kw in ["broker", "brokerid"]):
        if any(kw in ql for kw in ["reject", "rejected"]):
            return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd" AND OrdStatus == 56
| STATS rejected_orders = COUNT() BY BrokerId
| SORT rejected_orders DESC
| LIMIT 20
"""
        if any(kw in ql for kw in ["cancel", "cancelled"]):
            # Use SUM(CASE) instead of WHERE OrdStatus==67 filter — the dataset has 0 cancelled
            # orders, so filtering produces empty rows (→ abstain). SUM(CASE) returns all brokers
            # with their cancelled count (0), giving evidence to answer "no cancellations".
            return f"""
FROM "trading-execution-logs"
| WHERE {base} AND msg_type == "ordupd"
| STATS cancelled_orders = SUM(CASE(OrdStatus == 67, 1, 0)),
        total_orders = COUNT() BY BrokerId
| SORT cancelled_orders DESC
| LIMIT 20
"""
        if any(kw in ql for kw in ["fill", "filled"]):
            return f"""
FROM "trading-execution-logs"
| WHERE {base} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        filled_orders = SUM(CASE(OrdStatus == 48, 1, 0)),
        fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)) BY BrokerId
| SORT total_orders DESC
| LIMIT 20
"""
        # Generic broker breakdown
        return f"""
FROM "trading-execution-logs"
| WHERE {base} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        avg_qty = AVG(QtyToFill),
        fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)),
        reject_rate = AVG(CASE(OrdStatus == 56, 1.0, 0.0)) BY BrokerId
| SORT total_orders DESC
| LIMIT 20
"""

    # ── Symbol / top symbols ──
    if any(kw in ql for kw in ["top", "symbol", "most traded", "active"]):
        return f"""
FROM "trading-execution-logs"
| WHERE {base} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        buy_orders = SUM(CASE(TransType == "B", 1, 0)),
        sell_orders = SUM(CASE(TransType == "S", 1, 0)),
        fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)) BY TradingSymbol
| SORT total_orders DESC
| LIMIT 20
"""

    # ── Exchange / venue analysis — check BEFORE fill rate so
    #    "compare fill rate between NSE and BSE venues" goes here, not to fill-rate branch ──
    if query_type == QueryType.VENUE_ANALYSIS or any(kw in ql for kw in ["exchange", "venue", "exch", "nsefnfo", "nse", "bse", "nfo"]):
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        avg_qty = AVG(QtyToFill),
        fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)) BY ExchSeg
| SORT total_orders DESC
| LIMIT 10
"""

    # ── Fill / rejection / cancel rate queries — overall stats ──
    if any(kw in ql for kw in ["cancel", "cancelled", "cancellation", "cancellations"]):
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        cancelled_orders = SUM(CASE(OrdStatus == 67, 1, 0)),
        cancel_rate = AVG(CASE(OrdStatus == 67, 1.0, 0.0))
| LIMIT 1
"""

    if any(kw in ql for kw in ["reject", "rejected"]):
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        rejected_orders = SUM(CASE(OrdStatus == 56, 1, 0)),
        reject_rate = AVG(CASE(OrdStatus == 56, 1.0, 0.0))
| LIMIT 1
"""

    if any(kw in ql for kw in ["fill rate", "overall", "status breakdown", "filled"]):
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        filled = SUM(CASE(OrdStatus == 48, 1, 0)),
        open_orders = SUM(CASE(OrdStatus == 65, 1, 0)),
        new_orders = SUM(CASE(OrdStatus == 110, 1, 0)),
        cancelled = SUM(CASE(OrdStatus == 67, 1, 0)),
        rejected = SUM(CASE(OrdStatus == 56, 1, 0)),
        fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0))
| LIMIT 1
"""

    # ── Spike detection ──
    if query_type == QueryType.SPIKE_DETECTION:
        by_clause = " BY ticker" if len(symbols) > 1 else ""
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        avg_qty = AVG(QtyToFill),
        fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)),
        cancel_rate = AVG(CASE(OrdStatus == 67, 1.0, 0.0)){by_clause}
| LIMIT 20
"""

    # ── Feed / buy-sell correlation ──
    if query_type == QueryType.FEED_CORRELATION or any(kw in ql for kw in ["buy", "sell", "imbalance", "direction"]):
        by_clause = " BY ticker" if len(symbols) > 1 else ""
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| STATS buy_orders = SUM(CASE(TransType == "B", 1, 0)),
        sell_orders = SUM(CASE(TransType == "S", 1, 0)),
        buy_qty = SUM(CASE(TransType == "B", QtyToFill, 0)),
        sell_qty = SUM(CASE(TransType == "S", QtyToFill, 0)),
        total_orders = COUNT(){by_clause}
| LIMIT 20
"""

    # ── Order drilldown ──
    if query_type == QueryType.ORDER_DRILLDOWN:
        order_match = re.search(r"\b(2\d{13})\b", query)
        if order_match:
            order_id = order_match.group(1)
            return f"""
FROM "trading-execution-logs"
| WHERE NorenOrdNum == {order_id}
| SORT @timestamp DESC
| LIMIT 10
| KEEP @timestamp, TradingSymbol, ticker, OrdStatus, QtyToFill, PriceToFill, TransType, ExchSeg, NorenOrdNum, AcctId
"""
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| SORT @timestamp DESC
| LIMIT 50
| KEEP @timestamp, TradingSymbol, ticker, OrdStatus, QtyToFill, PriceToFill, TransType, ExchSeg, NorenOrdNum, AcctId
"""

    # ── Average order quantity / size ──
    if any(kw in ql for kw in ["average", "avg"]) and any(kw in ql for kw in ["quantity", "qty", "size", "order size"]):
        by_clause = " BY ticker" if symbols else ""
        limit = max(1, len(symbols)) if symbols else 1
        return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| STATS avg_qty = AVG(QtyToFill),
        p95_qty = PERCENTILE(QtyToFill, 95),
        total_orders = COUNT(){by_clause}
| LIMIT {limit}
"""

    # ── Default: general aggregation ──
    by_clause = " BY ticker" if len(symbols) > 1 else ""
    limit = max(1, len(symbols)) if len(symbols) > 1 else 1
    return f"""
FROM "trading-execution-logs"
| WHERE {base} {symbol_filter} AND msg_type == "ordupd"
| STATS total_orders = COUNT(),
        avg_qty = AVG(QtyToFill),
        total_qty = SUM(QtyToFill),
        fill_rate = AVG(CASE(OrdStatus == 48, 1.0, 0.0)),
        buy_orders = SUM(CASE(TransType == "B", 1, 0)),
        sell_orders = SUM(CASE(TransType == "S", 1, 0)){by_clause}
| LIMIT {limit}
"""


def _extract_symbols(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    # Extended to 15 chars to handle Indian symbols like ICICIBANK, TATAMOTORS, BAJAJFINSV
    for match in re.finditer(r"\b([A-Z]{2,15})\b", query.upper()):
        sym = match.group(1)
        if sym and sym not in _COMMON_WORDS and sym not in seen:
            seen.add(sym)
            out.append(sym)
    # Safety cap: if more than 3 symbols extracted from a single query, the list
    # almost certainly contains false positives from common English words not yet in
    # _COMMON_WORDS. Return empty so no incorrect symbol_filter is applied.
    if len(out) > 3:
        return []
    return out
