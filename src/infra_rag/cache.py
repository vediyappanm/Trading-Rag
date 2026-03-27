"""
Two-tier query cache using Redis.

Tier 1 — Exact cache: hash of the query string + context. Very fast, short TTL.
Tier 2 — (Future) Semantic cache: embedding similarity. Needs embedding model.
"""
import hashlib
import json
from datetime import datetime, timezone

from infra_rag.clients.redis import redis_client
from infra_rag.config import settings
from infra_rag.freshness import FreshnessContract
from infra_rag.models import QueryType


def _bucket_time(ts: datetime | None) -> str:
    bucket = settings.api.cache_bucket_seconds
    if bucket <= 0:
        return "nobucket"
    if ts is None:
        ts = datetime.now(timezone.utc)
    epoch = int(ts.timestamp())
    bucket_start = epoch - (epoch % bucket)
    return str(bucket_start)


def _hash_query(query: str, cache_context: str | None = None) -> str:
    """Create a stable hash key for a query string plus optional context."""
    base = query.strip().lower()
    if cache_context:
        base = f"{base}|{cache_context}"
    query_hash = hashlib.sha256(base.encode()).hexdigest()
    return query_hash[:16]


def build_cache_context(
    query_path: str | None,
    symbol: str | None,
    time_window_end: datetime | None,
) -> str:
    bucket = _bucket_time(time_window_end)
    return f"path={query_path or 'none'}|symbol={symbol or 'none'}|bucket={bucket}"


def get_cached_answer(query: str, cache_context: str | None = None) -> dict | None:
    """Check Tier-1 exact cache. Returns full response dict or None."""
    key = _hash_query(query, cache_context)
    cached = redis_client.get_cached_query(key)
    if cached:
        cached["from_cache"] = True
        try:
            if cached.get("cached_at") and cached.get("query_type"):
                qt = QueryType(cached["query_type"])
                cached_at = datetime.fromisoformat(cached["cached_at"])
                cached["data_freshness"] = FreshnessContract().label(cached_at, qt)
        except Exception:
            pass
        return cached
    return None


def set_cached_answer(query: str, response: dict, cache_context: str | None = None) -> None:
    """Store a response in the Tier-1 cache."""
    key = _hash_query(query, cache_context)
    if response.get("error"):
        return
    response = dict(response)
    response["cached_at"] = datetime.now(timezone.utc).isoformat()
    redis_client.set_cached_query(key, response, ttl=settings.api.cache_ttl_seconds)


def _semantic_key() -> str:
    return settings.redis.semantic_cache_index


def get_semantic_cached_answer(query_embedding: list[float]) -> dict | None:
    if not settings.api.semantic_cache_enabled:
        return None
    try:
        client = redis_client.client
        idx = _semantic_key()
        vector = bytes(bytearray(float(x).hex().encode() for x in query_embedding))
        # Requires RediSearch vector index; if not available, skip.
        res = client.execute_command(
            "FT.SEARCH", idx,
            "*=>[KNN 1 @embedding $vec AS score]",
            "PARAMS", 2, "vec", vector,
            "RETURN", 2, "payload", "cached_at",
            "SORTBY", "score", "ASC",
            "DIALECT", 2,
        )
        if res and isinstance(res, list) and len(res) >= 2:
            payload = None
            for item in res[2:]:
                if isinstance(item, list):
                    for i in range(0, len(item), 2):
                        if item[i] == "payload":
                            payload = item[i + 1]
            if payload:
                cached = json.loads(payload)
                cached["from_cache"] = True
                cached["cache_type"] = "semantic"
                return cached
    except Exception:
        return None
    return None


def set_semantic_cached_answer(query_embedding: list[float], response: dict) -> None:
    if not settings.api.semantic_cache_enabled:
        return
    if response.get("error"):
        return
    try:
        client = redis_client.client
        idx = _semantic_key()
        key = _hash_query(str(query_embedding))  # stable id
        payload = json.dumps(dict(response, cached_at=datetime.now(timezone.utc).isoformat()))
        vector = bytes(bytearray(float(x).hex().encode() for x in query_embedding))
        client.hset(key, mapping={"embedding": vector, "payload": payload, "cached_at": datetime.now(timezone.utc).isoformat()})
        client.execute_command("FT.ADDHASH", idx, key, 1.0, "REPLACE", "FIELDS", "embedding", vector, "payload", payload)
    except Exception:
        return


def get_cached_evidence(key: str) -> dict | None:
    try:
        data = redis_client.client.get(f"evidence:{key}")
        if data:
            return json.loads(data)
    except Exception:
        return None
    return None


def set_cached_evidence(key: str, evidence: dict) -> None:
    try:
        redis_client.client.setex(
            f"evidence:{key}",
            settings.api.evidence_cache_ttl_seconds,
            json.dumps(evidence),
        )
    except Exception:
        return
