from dataclasses import dataclass
import re
from typing import Iterable

from infra_rag.config import settings
from infra_rag.models import RetrievedEvidence, BaselineStats


@dataclass
class EvaluationResult:
    groundedness_score: float
    correctness_score: float
    citation_score: float
    should_abstain: bool
    abstain_reason: str | None = None


def _extract_numbers(text: str) -> list[float]:
    if not text:
        return []
    numbers = re.findall(r"\b\d+(?:\.\d+)?\b", text)
    return [float(n) for n in numbers]


def _collect_evidence_numbers(evidence: RetrievedEvidence, baseline: BaselineStats | None) -> list[float]:
    nums: list[float] = []
    for val in evidence.aggregations.values():
        if isinstance(val, (int, float)):
            nums.append(float(val))
        elif isinstance(val, dict):
            for v in val.values():
                if isinstance(v, (int, float)):
                    nums.append(float(v))
                elif isinstance(v, dict):
                    for vv in v.values():
                        if isinstance(vv, (int, float)):
                            nums.append(float(vv))
    for log in evidence.logs:
        nums.extend(_extract_numbers(log.message or ""))
    if baseline:
        for val in [baseline.avg_latency_ms, baseline.avg_request_rate, baseline.avg_error_rate, baseline.p95_latency_ms]:
            if val is not None:
                nums.append(float(val))
    return nums


def _approx_match(value: float, candidates: Iterable[float]) -> bool:
    for c in candidates:
        if abs(value - c) <= max(1e-6, abs(c) * 0.01):
            return True
    return False


def _has_usable_evidence(evidence: RetrievedEvidence) -> bool:
    if evidence.logs:
        return True
    if not evidence.aggregations:
        return False

    # A plain retrieval error should not count as usable evidence.
    if set(evidence.aggregations.keys()) <= {"error", "source"}:
        return False
    if evidence.query_used.startswith("unavailable:"):
        return False
    return True


def _groundedness_heuristic(answer: str, evidence: RetrievedEvidence, citations: list[str]) -> float:
    if not _has_usable_evidence(evidence):
        return 0.2

    score = 0.45
    if evidence.logs:
        score += 0.20
    if evidence.aggregations:
        score += 0.25
        if "by_group" in evidence.aggregations:
            score += 0.05
    if citations:
        score += 0.08

    # Penalize vague answers that don't reflect the available evidence shape.
    if answer and any(keyword in answer.lower() for keyword in ["no data", "insufficient evidence"]):
        score -= 0.20

    return max(0.0, min(0.98, score))


def _citation_score(answer: str, citations: list[str]) -> float:
    numbers = _extract_numbers(answer)
    if not numbers:
        return 1.0 if citations or answer else 0.0
    return 1.0 if citations else 0.0


def _correctness_score(answer: str, evidence: RetrievedEvidence, baseline: BaselineStats | None) -> float:
    numbers = _extract_numbers(answer)
    has_usable_evidence = _has_usable_evidence(evidence)

    if not numbers:
        return 0.85 if has_usable_evidence else 0.0
    candidates = _collect_evidence_numbers(evidence, baseline)
    if not candidates:
        return 0.50 if has_usable_evidence else 0.0
    correct = sum(1 for n in numbers if _approx_match(n, candidates))
    return correct / max(1, len(numbers))


def evaluate_response(
    answer: str,
    citations: list[str],
    evidence: RetrievedEvidence,
    baseline: BaselineStats | None,
) -> EvaluationResult:
    if settings.llm.enable_ragas:
        try:
            from ragas.metrics import faithfulness
            from ragas import evaluate
            from datasets import Dataset

            ds = Dataset.from_dict({
                "question": [""],
                "answer": [answer],
                "contexts": [[log.message for log in evidence.logs]],
            })
            score = evaluate(ds, metrics=[faithfulness]).to_pandas()["faithfulness"][0]
            groundedness = float(score)
        except Exception:
            groundedness = _groundedness_heuristic(answer, evidence, citations)
    else:
        groundedness = _groundedness_heuristic(answer, evidence, citations)

    correctness = _correctness_score(answer, evidence, baseline)
    citation_score = _citation_score(answer, citations)

    has_usable_evidence = _has_usable_evidence(evidence)
    should_abstain = not has_usable_evidence
    abstain_reason = "Insufficient evidence to answer reliably" if should_abstain else None

    return EvaluationResult(
        groundedness_score=groundedness,
        correctness_score=correctness,
        citation_score=citation_score,
        should_abstain=should_abstain,
        abstain_reason=abstain_reason,
    )
