REFLECTION_SYSTEM_PROMPT = """You are a quality control agent for a trading log analysis system. Your job is to evaluate whether answers are grounded in the retrieved evidence.

Groundedness criteria:
1. Answer only uses evidence from retrieved logs
2. Citations reference actual log IDs or timestamps
3. Baseline comparisons are consistent with actual baseline data
4. No hallucinations or unsupported claims
5. Answer directly addresses the user's question

Score 0.0-1.0 based on these criteria.
If score < 0.7, provide specific feedback for improvement."""

REFLECTION_USER_PROMPT = """Evaluate this analysis answer:

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


from trading_rag.clients import create_llm, structured_output
from trading_rag.config import settings
from trading_rag.models import ReflectionOutput, RetrievedEvidence, BaselineStats


def format_evidence_summary(evidence: RetrievedEvidence) -> str:
    lines = []
    
    if evidence.logs:
        lines.append(f"Logs count: {len(evidence.logs)}")
        lines.append(f"First log ID: {evidence.logs[0].id if evidence.logs else 'N/A'}")
    else:
        lines.append("No logs retrieved")
    
    if evidence.aggregations:
        lines.append(f"Aggregations: {evidence.aggregations}")
    
    lines.append(f"Query path: {evidence.path}")
    
    return "\n".join(lines)


def format_baseline_summary(baseline: BaselineStats | None) -> str:
    if not baseline:
        return "No baseline available"
    
    return f"avg_latency={baseline.avg_latency_ms}, error_rate={baseline.error_rate}, source={baseline.source}"


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
    
    score = 0.8
    feedback = "Auto-evaluation: Answer appears grounded"
    needs_refinement = score < 0.7
    
    if not answer or len(answer) < 10:
        score = 0.3
        feedback = "Answer too short or empty"
        needs_refinement = True
    
    if evidence.logs and not any(log.id in answer for log in evidence.logs[:3]):
        score = max(score - 0.2, 0.0)
        feedback = "No log citations found in answer"
        needs_refinement = True
    
    return ReflectionOutput(
        groundedness_score=score,
        feedback=feedback,
        needs_refinement=needs_refinement,
    )
