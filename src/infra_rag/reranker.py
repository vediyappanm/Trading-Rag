import re
from typing import Iterable

from infra_rag.models import RetrievedEvidence, LogEntry
from infra_rag.config import settings


def _tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-zA-Z0-9_]+", text.lower()))


def rerank_evidence(evidence: RetrievedEvidence, query: str, top_k: int | None = None) -> RetrievedEvidence:
    if not evidence.logs:
        return evidence

    if top_k is None:
        top_k = settings.api.rerank_top_k

    q_tokens = _tokenize(query)
    scored: list[tuple[float, LogEntry]] = []

    for log in evidence.logs:
        msg = log.message or ""
        overlap = len(q_tokens & _tokenize(msg))
        score = overlap
        scored.append((score, log))

    scored.sort(key=lambda x: x[0], reverse=True)
    top_logs = [log for _, log in scored[:top_k]]
    return RetrievedEvidence(
        logs=top_logs,
        aggregations=evidence.aggregations,
        query_used=evidence.query_used,
        path=evidence.path,
    )
