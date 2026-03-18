from datetime import datetime
from enum import Enum
from typing import Any
import json as _json
from pydantic import BaseModel, Field, field_validator


class QueryPath(str, Enum):
    STRUCTURED_ESQL = "structured_esql"
    DUAL_INDEX_CORRELATION = "dual_index_correlation"
    SEMANTIC_INCIDENT = "semantic_incident"


class QueryType(str, Enum):
    SPIKE_DETECTION = "spike_detection"
    BASELINE_COMPARE = "baseline_compare"
    VENUE_ANALYSIS = "venue_analysis"
    ORDER_DRILLDOWN = "order_drilldown"
    FEED_CORRELATION = "feed_correlation"
    EXPLORATORY = "exploratory"


class TimeWindow(BaseModel):
    start: datetime
    end: datetime


class RouterOutput(BaseModel):
    query_type: QueryType
    query_path: QueryPath
    confidence: float = Field(ge=0.0, le=1.0)
    time_window: TimeWindow | None = None
    symbol: str | None = None
    esql_query: str | None = None
    reasoning: str


class LogEntry(BaseModel):
    id: str
    timestamp: datetime
    message: str
    symbol: str | None = None
    fields: dict[str, Any] = Field(default_factory=dict)


class RetrievedEvidence(BaseModel):
    logs: list[LogEntry] = Field(default_factory=list)
    aggregations: dict[str, Any] = Field(default_factory=dict)
    query_used: str
    path: QueryPath


class BaselineStats(BaseModel):
    symbol: str | None
    hour: int
    avg_latency_ms: float | None = None
    avg_volume: float | None = None
    error_rate: float | None = None
    p95_latency_ms: float | None = None
    source: str = "redis"


class AnalysisOutput(BaseModel):
    answer: str
    baseline_comparison: str | None = None
    citations: list[str] = Field(default_factory=list)

    @field_validator("citations", mode="before")
    @classmethod
    def coerce_citations(cls, v: object) -> list[str]:
        if v is None:
            return []
        if isinstance(v, list):
            return [str(x) for x in v if x is not None]
        if isinstance(v, str):
            s = v.strip()
            if s.lower() in ("none", "null", "n/a", ""):
                return []
            try:
                parsed = _json.loads(s)
                if isinstance(parsed, list):
                    return [str(x) for x in parsed]
            except Exception:
                pass
            return [s]
        return []


class ReflectionOutput(BaseModel):
    groundedness_score: float = Field(ge=0.0, le=1.0)
    feedback: str
    needs_refinement: bool


class FinalResponse(BaseModel):
    answer: str
    baseline_comparison: str | None = None
    citations: list[str] = Field(default_factory=list)
    query_path: QueryPath
    query_type: QueryType | None = None
    reflections: int = 0
    processing_time_ms: int = 0
    from_cache: bool = False
    groundedness_score: float | None = None
    correctness_score: float | None = None
    citation_score: float | None = None
    should_abstain: bool = False
    abstain_reason: str | None = None
    data_freshness: str | None = None
    cached_at: str | None = None
    cost_usd: float | None = None
    cost_limit_hit: bool = False
    retrieval_stats: dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    message: str
    context: dict[str, Any] | None = None
