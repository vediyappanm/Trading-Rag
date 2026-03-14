ANALYSIS_SYSTEM_PROMPT = """You are a trading log analysis agent. Your job is to synthesize accurate, grounded answers from retrieved evidence and baseline comparisons.

Guidelines:
- Only use evidence from the retrieved logs
- Include baseline comparisons when available (e.g., "x% above normal")
- Cite specific log IDs or timestamps as evidence
- Be concise and direct in your answers
- If insufficient evidence, clearly state what information is missing"""

ANALYSIS_USER_PROMPT = """Generate an answer to this trading log question:

Question: {question}

Retrieved Evidence:
{evidence}

Baseline Statistics:
{baselines}

Provide:
1. A concise answer to the question
2. Comparison to baseline (if applicable)
3. Citations (log IDs or timestamps) used as evidence"""


from typing import Any

from trading_rag.clients import create_llm, structured_output
from trading_rag.config import settings
from trading_rag.models import AnalysisOutput, RetrievedEvidence, BaselineStats


def format_evidence(evidence: RetrievedEvidence) -> str:
    lines = []
    
    if evidence.logs:
        lines.append(f"Found {len(evidence.logs)} log entries:")
        for log in evidence.logs[:5]:
            lines.append(f"  - ID: {log.id}, Time: {log.timestamp}, Message: {log.message[:100]}")
    
    if evidence.aggregations:
        lines.append("\nAggregations:")
        for key, value in evidence.aggregations.items():
            lines.append(f"  - {key}: {value}")
    
    if not lines:
        lines.append("No evidence retrieved")
    
    return "\n".join(lines)


def format_baselines(baseline: BaselineStats | None) -> str:
    if not baseline:
        return "No baseline data available"
    
    lines = [
        f"Symbol: {baseline.symbol or 'All'}",
        f"Hour: {baseline.hour}:00",
    ]
    
    if baseline.avg_latency_ms is not None:
        lines.append(f"Average latency: {baseline.avg_latency_ms:.2f}ms")
    if baseline.avg_volume is not None:
        lines.append(f"Average volume: {baseline.avg_volume:.2f}")
    if baseline.error_rate is not None:
        lines.append(f"Error rate: {baseline.error_rate:.2%}")
    if baseline.p95_latency_ms is not None:
        lines.append(f"P95 latency: {baseline.p95_latency_ms:.2f}ms")
    
    return "\n".join(lines)


def compare_to_baseline(
    aggregations: dict[str, Any],
    baseline: BaselineStats | None,
) -> str | None:
    if not baseline:
        return None
    
    comparisons = []
    
    if baseline.avg_latency_ms is not None and "avg_latency_ms" in aggregations:
        current = aggregations["avg_latency_ms"]
        normal = baseline.avg_latency_ms
        if current is not None and normal > 0:
            diff_pct = ((current - normal) / normal) * 100
            if diff_pct > 10:
                comparisons.append(f"latency {diff_pct:.1f}% above normal")
            elif diff_pct < -10:
                comparisons.append(f"latency {abs(diff_pct):.1f}% below normal")
    
    if baseline.error_rate is not None and "error_rate" in aggregations:
        current = aggregations["error_rate"]
        normal = baseline.error_rate
        if current is not None and current > normal * 1.5:
            comparisons.append(f"error rate {current:.2%} is elevated (normal: {normal:.2%})")
    
    if baseline.avg_volume is not None and "avg_volume" in aggregations:
        current = aggregations["avg_volume"]
        normal = baseline.avg_volume
        if current is not None and normal > 0:
            diff_pct = ((current - normal) / normal) * 100
            if abs(diff_pct) > 20:
                if diff_pct > 0:
                    comparisons.append(f"volume {diff_pct:.1f}% above normal")
                else:
                    comparisons.append(f"volume {abs(diff_pct):.1f}% below normal")
    
    if comparisons:
        return ", ".join(comparisons)
    return None


def generate_analysis(
    question: str,
    evidence: RetrievedEvidence,
    baseline: BaselineStats | None,
) -> AnalysisOutput:
    evidence_str = format_evidence(evidence)
    baseline_str = format_baselines(baseline)
    
    baseline_comparison = compare_to_baseline(evidence.aggregations, baseline)
    
    citations = [log.id for log in evidence.logs[:5]]
    if not citations and evidence.aggregations:
        citations = [f"agg:{k}" for k in evidence.aggregations.keys()]
    
    evidence_text = evidence_str
    if evidence.aggregations:
        evidence_text += f"\n\nAggregated metrics: {evidence.aggregations}"
    
    prompt = ANALYSIS_USER_PROMPT.format(
        question=question,
        evidence=evidence_text,
        baselines=baseline_str,
    )
    
    try:
        llm = create_llm(settings.llm.analysis_model)
        result = structured_output(llm, AnalysisOutput, prompt)
        
        if result:
            result.baseline_comparison = baseline_comparison or result.baseline_comparison
            result.citations = citations or result.citations
            if not result.citations and (evidence.logs or evidence.aggregations):
                result.citations = citations
            return result
    except Exception:
        pass
    
    analysis_result = "Analysis complete based on retrieved evidence."
    if baseline_comparison:
        analysis_result += f"\n\nBaseline Comparison: {baseline_comparison}."
    
    if evidence.aggregations:
        metrics = evidence.aggregations
        if "detected_symbols" in metrics:
            m_lines = []
            for sym in metrics["detected_symbols"]:
                sym_m = metrics.get(f"{sym}_metrics", {})
                m_lines.append(
                    f"**{sym}**: Avg Latency: {sym_m.get('avg_latency_ms', 0):.2f}ms, "
                    f"Error Rate: {sym_m.get('error_rate', 0):.2%}"
                )
            analysis_result += f"\n\nPer-Symbol Breakdown:\n" + "\n".join(m_lines)
        else:
            avg_latency = metrics.get("avg_latency_ms") or 0
            avg_volume = metrics.get("avg_volume") or 0
            total_volume = metrics.get("total_volume") or 0
            error_rate = metrics.get("error_rate") or 0
            m_str = (
                f"Avg Latency: {avg_latency:.2f}ms, "
                f"Avg Volume: {avg_volume:.2f}, "
                f"Total Volume: {total_volume}, "
                f"Error Rate: {error_rate:.2%}"
            )
            analysis_result += f"\n\nKey Metrics: {m_str}"
    
    if not evidence.logs and not evidence.aggregations:
        if isinstance(evidence.query_used, str) and evidence.query_used.startswith("unavailable"):
            analysis_result = "Data sources are temporarily unavailable, so no logs or metrics could be retrieved."
        else:
            analysis_result = "No matching trading logs or metrics found for the specified period."

    return AnalysisOutput(
        answer=analysis_result,
        baseline_comparison=baseline_comparison,
        citations=citations,
    )
