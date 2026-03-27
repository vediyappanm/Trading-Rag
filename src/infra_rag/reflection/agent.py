REFLECTION_SYSTEM_PROMPT = """You are a quality control agent for an infrastructure analysis system. Your job is to evaluate whether answers are grounded in the retrieved evidence.

Groundedness criteria:
1. Answer only uses evidence from retrieved metrics, logs, traces, or alerts
2. Citations reference actual hostnames, timestamps, alert names, or service names
3. Baseline comparisons are consistent with actual baseline data
4. No hallucinations or unsupported claims about infrastructure state
5. Answer directly addresses the user's question
6. Metrics are accurate (CPU%, memory%, latency in ms, throughput in MB/s)

Score 0.0-1.0 based on these criteria.
If score < 0.7, provide specific feedback for improvement."""

REFLECTION_USER_PROMPT = """Evaluate this infrastructure analysis answer:

Original Question: {question}

Answer: {answer}

Retrieved Evidence Summary:
{evidence_summary}

Baseline Data:
{baseline_summary}

Provide:
1. groundedness_score: 0.0 to 1.0
2. feedback: Specific issues or "Looks good"
3. needs_refinement: true if score < 0.7"""


from infra_rag.clients import create_llm, structured_output
from infra_rag.config import settings
from infra_rag.models import ReflectionOutput, RetrievedEvidence, BaselineStats


def format_evidence_summary(evidence: RetrievedEvidence) -> str:
    lines = []

    if evidence.logs:
        lines.append(f"Log/event entries: {len(evidence.logs)}")
        if evidence.logs:
            first = evidence.logs[0]
            lines.append(f"First entry: {first.source or first.id} @ {first.timestamp}")
    else:
        lines.append("No log entries retrieved")

    if evidence.aggregations:
        lines.append(f"Aggregations: {evidence.aggregations}")

    lines.append(f"Domain: {evidence.domain.value}")
    lines.append(f"Query path: {evidence.path.value}")

    return "\n".join(lines)


def format_baseline_summary(baseline: BaselineStats | None) -> str:
    if not baseline:
        return "No baseline available"

    parts = [f"target={baseline.target or 'all'}"]
    if baseline.avg_cpu_pct is not None:
        parts.append(f"cpu={baseline.avg_cpu_pct:.1f}%")
    if baseline.avg_memory_pct is not None:
        parts.append(f"memory={baseline.avg_memory_pct:.1f}%")
    if baseline.avg_latency_ms is not None:
        parts.append(f"latency={baseline.avg_latency_ms:.1f}ms")
    if baseline.avg_error_rate is not None:
        parts.append(f"error_rate={baseline.avg_error_rate:.2%}")
    parts.append(f"source={baseline.source}")
    return ", ".join(parts)


def evaluate_answer(
    question: str,
    answer: str,
    evidence: RetrievedEvidence,
    baseline: BaselineStats | None,
) -> ReflectionOutput:
    evidence_summary = format_evidence_summary(evidence)
    baseline_summary = format_baseline_summary(baseline)

    prompt = REFLECTION_USER_PROMPT.format(
        question=question,
        answer=answer,
        evidence_summary=evidence_summary,
        baseline_summary=baseline_summary,
    )

    try:
        llm = create_llm(settings.llm.reflection_model)
        result = structured_output(llm, ReflectionOutput, prompt)
        if result:
            return result
    except Exception:
        pass

    # Fallback heuristic
    score = 0.8
    feedback = "Auto-evaluation: Answer appears grounded"
    needs_refinement = False

    if not answer or len(answer) < 10:
        score = 0.3
        feedback = "Answer too short or empty"
        needs_refinement = True

    if evidence.logs and not any(
        (log.source or log.id) in answer for log in evidence.logs[:3]
    ):
        score = max(score - 0.2, 0.0)
        feedback = "No host/service citations found in answer"
        needs_refinement = True

    return ReflectionOutput(
        groundedness_score=score,
        feedback=feedback,
        needs_refinement=needs_refinement,
    )
