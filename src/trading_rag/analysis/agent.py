ANALYSIS_SYSTEM_PROMPT = """You are a trading log analysis agent for QuantSight, analyzing real NSE/NFO/BSE order data from the Noren OMS (Indian stock broker platform) serving 65+ brokers.

Data context:
- OrdStatus: 48=FILLED, 65=OPEN, 110=NEW, 67=CANCELLED, 56=REJECTED
- TransType: B=Buy, S=Sell
- ExchSeg: NSE=equity, NFO=F&O, BSE=equity, BFO=BSE F&O, CDS=currency
- Product: I=Intraday(MIS), C=Delivery(CNC), M=Margin(NRML)
- PriceToFill is in paise — divide by 100 for rupees (e.g. 23935 = ₹239.35)
- QtyToFill is order quantity in shares or lots
- Baseline metrics: avg_latency_ms = avg order quantity, avg_volume = total orders in period, error_rate = non-fill rate

Guidelines:
- Only use evidence from the retrieved logs and aggregations
- State prices in rupees (₹) after converting from paise
- Include baseline comparisons when available
- Cite order IDs (NorenOrdNum) or timestamps as evidence
- Be concise and factual — this is used by trading operations teams"""

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
        lines.append(f"Found {len(evidence.logs)} order entries:")
        for log in evidence.logs[:5]:
            extra = log.fields
            status_map = {48: "FILLED", 65: "OPEN", 110: "NEW", 67: "CANCELLED", 56: "REJECTED"}
            status = status_map.get(extra.get("OrdStatus"), str(extra.get("OrdStatus", "")))
            price_paise = extra.get("PriceToFill", 0)
            price_rs = f"₹{price_paise/100:.2f}" if price_paise else ""
            lines.append(
                f"  - Order {extra.get('NorenOrdNum','?')} | {extra.get('TradingSymbol','?')} "
                f"| {extra.get('TransType','?')} {extra.get('QtyToFill','?')} {price_rs} "
                f"| {status} | {extra.get('ExchSeg','?')} | {log.timestamp}"
            )
    
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
        f"Symbol: {baseline.symbol or 'All symbols'}",
        f"Baseline hour (UTC): {baseline.hour}:00 — averaged over last 30 days",
    ]

    if baseline.avg_latency_ms is not None:
        lines.append(f"Avg order quantity (baseline): {baseline.avg_latency_ms:.2f} shares/lots")
    if baseline.avg_volume is not None:
        lines.append(f"Total orders in baseline period: {baseline.avg_volume:.0f}")
    if baseline.error_rate is not None:
        lines.append(f"Non-fill rate (baseline): {baseline.error_rate:.2%}")
    if baseline.p95_latency_ms is not None:
        lines.append(f"P95 order quantity (baseline): {baseline.p95_latency_ms:.2f}")
    
    return "\n".join(lines)


def compare_to_baseline(
    aggregations: dict[str, Any],
    baseline: BaselineStats | None,
) -> str | None:
    if not baseline:
        return None
    
    comparisons = []
    
    # avg_latency_ms repurposed as avg order quantity
    if baseline.avg_latency_ms is not None and "avg_latency_ms" in aggregations:
        current = aggregations["avg_latency_ms"]
        normal = baseline.avg_latency_ms
        if current is not None and normal > 0:
            diff_pct = ((current - normal) / normal) * 100
            if diff_pct > 20:
                comparisons.append(f"avg order quantity {diff_pct:.1f}% above baseline ({current:.0f} vs {normal:.0f})")
            elif diff_pct < -20:
                comparisons.append(f"avg order quantity {abs(diff_pct):.1f}% below baseline ({current:.0f} vs {normal:.0f})")

    # error_rate repurposed as non-fill rate
    if baseline.error_rate is not None and "error_rate" in aggregations:
        current = aggregations["error_rate"]
        normal = baseline.error_rate
        if current is not None and current > normal * 1.5:
            comparisons.append(f"non-fill rate {current:.2%} elevated vs baseline {normal:.2%}")

    # avg_volume repurposed as total orders
    if baseline.avg_volume is not None and "avg_volume" in aggregations:
        current = aggregations["avg_volume"]
        normal = baseline.avg_volume
        if current is not None and normal > 0:
            diff_pct = ((current - normal) / normal) * 100
            if abs(diff_pct) > 20:
                direction = "above" if diff_pct > 0 else "below"
                comparisons.append(f"order count {abs(diff_pct):.1f}% {direction} baseline ({current:.0f} vs {normal:.0f})")
    
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
            total_orders = metrics.get("total_orders") or metrics.get("avg_volume") or 0
            avg_qty = metrics.get("avg_qty") or metrics.get("avg_latency_ms") or 0
            total_qty = metrics.get("total_qty") or metrics.get("total_volume") or 0
            fill_rate = metrics.get("fill_rate") or (1.0 - (metrics.get("error_rate") or 0))
            buy_orders = metrics.get("buy_orders") or 0
            sell_orders = metrics.get("sell_orders") or 0
            m_str = (
                f"Total Orders: {total_orders:.0f}, "
                f"Avg Qty: {avg_qty:.2f}, "
                f"Total Qty: {total_qty:.0f}, "
                f"Fill Rate: {fill_rate:.2%}, "
                f"Buy: {buy_orders:.0f} / Sell: {sell_orders:.0f}"
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
