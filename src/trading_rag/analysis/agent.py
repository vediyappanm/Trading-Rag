ANALYSIS_SYSTEM_PROMPT = """You are a trading log analysis agent for Finspot Rag, analyzing real NSE/NFO/BSE order data from the Noren OMS (Indian stock broker platform) serving 65+ brokers.

Dataset: Jan 20, 2026 — 274,595 orders, 4,380+ symbols, 65 brokers, exchanges: NSE (164,928), NFO (93,003), BSE (14,425), CDS (980), BFO (1,061).

Known OrdStatus codes (stored as integer ASCII values):
- 48 = FILLED (144,758 orders = 52.72% fill rate)
- 56 = REJECTED (2,728 orders = 0.99%)
- 65 = OPEN/ACCEPTED
- 110 = NEW (pending submission)
- 67 = CANCELLED (0 in this dataset)
- 50, 52, 54, 98, 109, 115 = intermediate/pending states (trigger, modify, batch, etc.)

Other fields:
- TransType: B=Buy (158,735), S=Sell (115,860)
- ExchSeg: NSE, NFO, BSE, BFO, CDS, NSLB
- Product: I=Intraday(MIS), C=Delivery(CNC), M=Margin(NRML)
- PriceToFill is in paise — divide by 100 for rupees (e.g. 23935 = ₹239.35)
- QtyToFill is order quantity in shares or lots
- ticker field: base symbol for equity (RELIANCE), but for F&O equals full TradingSymbol (e.g. NIFTY20JAN26F)

Guidelines:
- Only use evidence from the retrieved logs and aggregations
- State prices in rupees (₹) after converting from paise
- The true overall fill rate is 52.72% (144758/274595) — use this as baseline
- Include baseline comparisons when available
- Cite order IDs (NorenOrdNum) or timestamps as evidence
- Be concise and factual — this is used by trading operations teams
- If asked to compare execution vs feed data but only execution data is available, provide the
  execution analysis and state "Feed log data is not available in the current dataset" — do NOT abstain
- Always answer with available data; only say "Insufficient evidence" if there is literally no data at all"""

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
    STATUS_MAP = {48: "FILLED", 65: "OPEN", 110: "NEW", 67: "CANCELLED", 56: "REJECTED",
                  50: "TRIGGER_PENDING", 52: "MODIFIED", 54: "MODIFY_PENDING", 98: "BATCH",
                  109: "MODIFIED_2", 115: "SUSPENDED"}

    if evidence.logs:
        lines.append(f"Found {len(evidence.logs)} order entries:")
        for log in evidence.logs[:5]:
            extra = log.fields
            status = STATUS_MAP.get(extra.get("OrdStatus"), str(extra.get("OrdStatus", "")))
            price_paise = extra.get("PriceToFill", 0)
            price_rs = f"₹{price_paise/100:.2f}" if price_paise else ""
            lines.append(
                f"  - Order {extra.get('NorenOrdNum','?')} | {extra.get('TradingSymbol','?')} "
                f"| {extra.get('TransType','?')} {extra.get('QtyToFill','?')} {price_rs} "
                f"| {status} | {extra.get('ExchSeg','?')} | {log.timestamp}"
            )

    if evidence.aggregations:
        agg = evidence.aggregations
        # Grouped results (by symbol/broker/exchange)
        if "by_symbol" in agg:
            group_data = agg["by_symbol"]
            lines.append(f"\nGrouped results ({len(group_data)} groups):")
            for grp, metrics in list(group_data.items())[:20]:
                # Format key metrics compactly
                parts = []
                for k, v in metrics.items():
                    if k.startswith("avg_") or k.startswith("total_") or k in (
                        "fill_rate", "reject_rate", "cancel_rate",
                        "rejected_orders", "filled_orders", "buy_orders", "sell_orders",
                        "cancelled_orders", "total_orders",
                    ):
                        if isinstance(v, float):
                            if k.endswith("rate"):
                                parts.append(f"{k}={v:.2%}")
                            else:
                                parts.append(f"{k}={v:.0f}")
                        elif v is not None:
                            parts.append(f"{k}={v}")
                lines.append(f"  {grp}: {', '.join(parts)}")
        else:
            # Flat aggregation
            lines.append("\nAggregated metrics:")
            scalar_keys = [
                "total_orders", "filled", "fill_rate", "rejected", "reject_rate",
                "cancelled", "cancel_rate", "open_orders", "new_orders",
                "buy_orders", "sell_orders", "avg_qty", "total_qty",
                "rejected_orders", "cancelled_orders",
            ]
            for k in scalar_keys:
                if k in agg and agg[k] is not None:
                    v = agg[k]
                    if k.endswith("rate") and isinstance(v, float):
                        lines.append(f"  {k}: {v:.2%}")
                    elif isinstance(v, float):
                        lines.append(f"  {k}: {v:.2f}")
                    else:
                        lines.append(f"  {k}: {v}")
            # Any remaining non-metadata keys
            skip = set(scalar_keys) | {"avg_latency_ms", "avg_volume", "error_rate",
                                        "p95_latency_ms", "total_count", "total_volume"}
            for k, v in agg.items():
                if k not in skip and not isinstance(v, (dict, list)) and v is not None:
                    lines.append(f"  {k}: {v}")

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
    
    if not evidence.logs and not evidence.aggregations:
        if isinstance(evidence.query_used, str) and evidence.query_used.startswith("unavailable"):
            analysis_result = "Data sources are temporarily unavailable. Please try again."
        else:
            analysis_result = "No matching trading data found for that query."
        return AnalysisOutput(answer=analysis_result, citations=[])

    metrics = evidence.aggregations
    analysis_result = ""

    # Grouped results (by broker / symbol / exchange)
    if "by_symbol" in metrics:
        group_data = metrics["by_symbol"]
        top = sorted(group_data.items(),
                     key=lambda x: x[1].get("rejected_orders") or x[1].get("total_orders") or 0,
                     reverse=True)
        lines = []
        for grp, m in top[:10]:
            parts = []
            for k in ("total_orders", "rejected_orders", "filled_orders", "fill_rate",
                      "reject_rate", "buy_orders", "sell_orders", "cancelled_orders"):
                v = m.get(k)
                if v is not None:
                    if k.endswith("rate") and isinstance(v, float):
                        parts.append(f"{k.replace('_', ' ')}={v:.2%}")
                    else:
                        parts.append(f"{k.replace('_', ' ')}={int(v) if isinstance(v, float) else v}")
            lines.append(f"  **{grp}**: {', '.join(parts)}")
        analysis_result = f"Results ({len(group_data)} groups):\n\n" + "\n".join(lines)

    # Flat aggregate (single row result)
    elif metrics:
        total_orders = metrics.get("total_orders") or 0
        filled = metrics.get("filled") or 0
        rejected = metrics.get("rejected") or metrics.get("rejected_orders") or 0
        cancelled = metrics.get("cancelled") or metrics.get("cancelled_orders") or 0
        open_o = metrics.get("open_orders") or 0
        new_o = metrics.get("new_orders") or 0
        fill_rate = metrics.get("fill_rate") or 0
        buy_orders = metrics.get("buy_orders") or 0
        sell_orders = metrics.get("sell_orders") or 0
        reject_rate = metrics.get("reject_rate") or (rejected / total_orders if total_orders else 0)

        parts = [f"Total orders: **{int(total_orders):,}**"]
        if filled:   parts.append(f"Filled: {int(filled):,} ({fill_rate:.1%})")
        if rejected: parts.append(f"Rejected: {int(rejected):,} ({reject_rate:.2%})")
        # Show cancelled even if 0 — "0 cancelled" is a valid, informative answer
        cancel_asked = any(kw in question.lower() for kw in ("cancel", "cancelled"))
        if cancelled or cancel_asked:
            parts.append(f"Cancelled: {int(cancelled):,}")
        if open_o:   parts.append(f"Open: {int(open_o):,}")
        if new_o:    parts.append(f"New/Pending: {int(new_o):,}")
        if buy_orders and sell_orders:
            parts.append(f"Buy: {int(buy_orders):,} / Sell: {int(sell_orders):,}")
        analysis_result = ", ".join(parts) + "."

    if baseline_comparison:
        analysis_result += f"\n\nBaseline: {baseline_comparison}"

    return AnalysisOutput(
        answer=analysis_result,
        baseline_comparison=baseline_comparison,
        citations=citations,
    )
