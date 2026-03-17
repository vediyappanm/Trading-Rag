from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ElasticsearchSettings(BaseSettings):
    host: str = Field(default="http://localhost:9200")
    username: str = Field(default="elastic")
    password: str = Field(default="changeme")
    verify_certs: bool = Field(default=True)
    request_timeout_s: int = Field(default=20)
    max_retries: int = Field(default=2)
    retry_on_timeout: bool = Field(default=True)
    circuit_breaker_failures: int = Field(default=5)
    circuit_breaker_reset_s: int = Field(default=30)
    execution_logs_index: str = Field(default="trading-execution-logs")
    feed_logs_index: str = Field(default="trading-feed-logs")
    incidents_index: str = Field(default="trading-incidents")
    field_caps_index_pattern: str = Field(default="trading-*")
    esql_limit_default: int = Field(default=500)
    esql_keep_fields: list[str] = Field(default_factory=lambda: [
        "@timestamp", "TradingSymbol", "ticker", "OrdStatus", "QtyToFill",
        "PriceToFill", "TransType", "ExchSeg", "BrokerId", "Product",
        "PriceType", "NorenOrdNum", "AcctId", "msg_type"
    ])


class RedisSettings(BaseSettings):
    host: str = Field(default="localhost")
    port: int = Field(default=6379)
    password: str = Field(default="")
    db: int = Field(default=0)
    socket_timeout_s: int = Field(default=2)
    socket_connect_timeout_s: int = Field(default=2)
    circuit_breaker_failures: int = Field(default=5)
    circuit_breaker_reset_s: int = Field(default=30)
    semantic_cache_index: str = Field(default="semantic_cache")


class LLMSettings(BaseSettings):
    provider: str = Field(default="openai")
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")
    groq_api_key: str = Field(default="")
    router_model: str = Field(default="gpt-4o-mini")
    analysis_model: str = Field(default="gpt-4o-mini")
    reflection_model: str = Field(default="gpt-4o-mini")
    request_timeout_s: int = Field(default=20)
    max_retries: int = Field(default=2)
    enable_reflection: bool = Field(default=True)
    max_reflections: int = Field(default=3)
    cost_per_1k_tokens_usd: float = Field(default=0.01)
    enable_ragas: bool = Field(default=False)


class APISettings(BaseSettings):
    host: str = Field(default="0.0.0.0")
    port: int = Field(default=8000)
    log_level: str = Field(default="INFO")
    enable_gzip: bool = Field(default=True)
    gzip_min_size: int = Field(default=1000)
    max_log_limit: int = Field(default=200)
    retrieval_timeout_s: int = Field(default=15)
    cache_ttl_seconds: int = Field(default=300)
    cache_bucket_seconds: int = Field(default=300)
    cors_origins: list[str] = Field(default_factory=lambda: ["*"])
    rate_limit_rps: int = Field(default=10)
    cost_budget_max_tokens: int = Field(default=50000)
    cost_budget_max_llm_calls: int = Field(default=6)
    cost_budget_max_usd: float = Field(default=0.15)
    evidence_cache_ttl_seconds: int = Field(default=60)
    semantic_cache_enabled: bool = Field(default=False)
    semantic_cache_similarity: float = Field(default=0.95)
    rerank_top_k: int = Field(default=8)


class PostgresSettings(BaseSettings):
    dsn: str = Field(default="")
    pg_schema: str = Field(default="public")


class FreshnessSettings(BaseSettings):
    ttl_spike_detection: int = Field(default=30)
    ttl_order_drilldown: int = Field(default=60)
    ttl_baseline_compare: int = Field(default=300)
    ttl_venue_analysis: int = Field(default=120)
    ttl_feed_correlation: int = Field(default=30)
    ttl_exploratory: int = Field(default=600)


import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", 
        env_nested_delimiter="__", 
        extra="ignore"
    )

    elasticsearch: ElasticsearchSettings = Field(default_factory=ElasticsearchSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    api: APISettings = Field(default_factory=APISettings)
    postgres: PostgresSettings = Field(default_factory=PostgresSettings)
    freshness: FreshnessSettings = Field(default_factory=FreshnessSettings)


settings = Settings()
