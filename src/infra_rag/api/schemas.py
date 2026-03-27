from pydantic import BaseModel, Field


class AskRequest(BaseModel):
    query: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Infrastructure question to analyze",
    )


class AskResponse(BaseModel):
    answer: str
    domain: str = ""
    baseline_comparison: str | None = None
    citations: list[str] = Field(default_factory=list)
    query_path: str
    reflections: int = 0
    processing_time_ms: int = 0
    from_cache: bool = False


class AskV2Response(BaseModel):
    answer: str
    domain: str = ""
    citations: list[dict] = Field(default_factory=list)
    confidence: str
    groundedness_score: float | None = None
    correctness_score: float | None = None
    citation_score: float | None = None
    retrieval_stats: dict = Field(default_factory=dict)
    data_freshness: str | None = None
    cached_at: str | None = None
    latency_ms: int = 0
    from_cache: bool = False
    cost_usd: float | None = None
    cost_limit_hit: bool = False


class ErrorDetail(BaseModel):
    message: str
    context: dict | None = None
