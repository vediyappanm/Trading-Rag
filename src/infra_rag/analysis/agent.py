ANALYSIS_SYSTEM_PROMPT = """You are an infrastructure analysis agent for an Infrastructure Management RAG system. You analyze metrics, logs, traces, alerts, and network data to answer operations questions.

Data sources:
- Prometheus metrics stored in Elasticsearch (CPU, memory, disk, network per host/container)
- Application/system logs from Filebeat/Fluent Bit (with log levels: INFO, WARN, ERROR, FATAL)
- Distributed traces from Grafana Tempo (service latencies, error rates)
- Prometheus Alertmanager history (firing/resolved alerts with severity)
- SNMP network device metrics (interface throughput, device CPU/memory)
- Blackbox Exporter probes (HTTP/ICMP uptime checks, response latencies)

Guidelines:
- Only use evidence from the retrieved data — never invent metrics or logs
- Format metrics clearly: CPU% , memory%, disk%, latency in ms, throughput in MB/s
- When showing rates/percentages, use consistent formatting (e.g., 85.3%)
- Compare to baselines when available (e.g., "CPU 92% vs baseline 45%")
- Cite hostnames, timestamps, service names, or alert names as evidence
- For error analysis, include the error message and stack trace snippet
- For latency analysis, include p50/p95/p99 percentiles when available
- When data spans multiple hosts/services, summarize by group
- If asked "why" questions, correlate across metrics + logs + alerts
- Be concise and actionable — operations teams need quick answers
- If insufficient data, say so clearly rather than guessing"""

ANALYSIS_USER_PROMPT = """Analyze this infrastructure question:

Question: {question}

Retrieved Evidence:
{evidence}

Baseline Statistics:
{baselines}

Provide:
1. A concise, factual answer to the question
2. Comparison to baseline (if applicable)
3. Citations (hostnames, timestamps, alert names) as evidence
4. Actionable recommendations if appropriate"""


from typing import Any

from infra_rag.clients import create_llm, structured_output
from infra_rag.config import settings
from infra_rag.models import AnalysisOutput, RetrievedEvidence, BaselineStats


def format_evidence(evidence: RetrievedEvidence) -> str:
    lines = []

    if evidence.logs:
        lines.append(f"Found {len(evidence.logs)} entries:")
        for log in evidence.logs[:10]:
            source = log.source or "unknown"
            fields = log.fields
            # Format based on domain
            if "log.level" in fields or "level" in fields:
                level = fields.get("log.level") or fields.get("level", "")
                lines.append(
                    f"  [{level}] {source} @ {log.timestamp}: {log.message[:200]}"
                )
            elif "alertname" in fields:
                severity = fields.get("severity", "?")
                state = fields.get("state", "?")
                lines.append(
                    f"  ALERT [{severity}] {fields.get('alertname','?')} "
                    f"on {source} — {state} @ {log.timestamp}"
                )
            elif "duration_ms" in fields:
                lines.append(
                    f"  TRACE {fields.get('service.name','?')} → "
                    f"{fields.get('operation.name','?')} "
                    f"{fields.get('duration_ms','?')}ms "
                    f"[{fields.get('status.code','OK')}] @ {log.timestamp}"
                )
            else:
                lines.append(f"  {source} @ {log.timestamp}: {log.message[:200]}")

    if evidence.aggregations:
        agg = evidence.aggregations

        # Grouped results
        if "by_group" in agg:
            group_data = agg["by_group"]
            group_field = agg.get("group_field", "group")
            lines.append(f"\nGrouped by {group_field} ({len(group_data)} groups):")
            for grp, metrics in list(group_data.items())[:20]:
                parts = []
                for k, v in metrics.items():
                    if v is None:
                        continue
                    if isinstance(v, float):
                        if "pct" in k or "rate" in k:
                            parts.append(f"{k}={v:.1f}%")
                        elif "bytes" in k:
                            mb = v / (1024 * 1024)
                            parts.append(f"{k}={mb:.1f}MB/s")
                        elif "ms" in k or "duration" in k or "latency" in k:
                            parts.append(f"{k}={v:.1f}ms")
                        else:
                            parts.append(f"{k}={v:.2f}")
                    elif isinstance(v, int):
                        parts.append(f"{k}={v:,}")
                    else:
                        parts.append(f"{k}={v}")
                lines.append(f"  {grp}: {', '.join(parts)}")
        else:
            # Flat aggregation
            lines.append("\nAggregated metrics:")
            for k, v in agg.items():
                if isinstance(v, (dict, list)):
                    continue
                if v is None:
                    continue
                if isinstance(v, float):
                    if "pct" in k or "rate" in k:
                        lines.append(f"  {k}: {v:.1f}%")
                    elif "bytes" in k:
                        mb = v / (1024 * 1024)
                        lines.append(f"  {k}: {mb:.1f} MB/s")
                    elif "ms" in k or "duration" in k or "latency" in k:
                        lines.append(f"  {k}: {v:.1f}ms")
                    else:
                        lines.append(f"  {k}: {v:.2f}")
                else:
                    lines.append(f"  {k}: {v}")

    if not lines:
        lines.append("No evidence retrieved")

    return "\n".join(lines)


def format_baselines(baseline: BaselineStats | None) -> str:
    if not baseline:
        return "No baseline data available"

    lines = [
        f"Target: {baseline.target or 'All hosts'}",
        f"Baseline hour (UTC): {baseline.hour}:00",
    ]

    if baseline.avg_cpu_pct is not None:
        lines.append(f"Avg CPU (baseline): {baseline.avg_cpu_pct:.1f}%")
    if baseline.avg_memory_pct is not None:
        lines.append(f"Avg Memory (baseline): {baseline.avg_memory_pct:.1f}%")
    if baseline.avg_disk_usage_pct is not None:
        lines.append(f"Avg Disk (baseline): {baseline.avg_disk_usage_pct:.1f}%")
    if baseline.avg_latency_ms is not None:
        lines.append(f"Avg Latency (baseline): {baseline.avg_latency_ms:.1f}ms")
    if baseline.avg_error_rate is not None:
        lines.append(f"Avg Error Rate (baseline): {baseline.avg_error_rate:.2%}")
    if baseline.avg_request_rate is not None:
        lines.append(f"Avg Request Rate (baseline): {baseline.avg_request_rate:.0f} req/s")
    if baseline.p95_latency_ms is not None:
        lines.append(f"P95 Latency (baseline): {baseline.p95_latency_ms:.1f}ms")

    return "\n".join(lines)


def compare_to_baseline(
    aggregations: dict[str, Any],
    baseline: BaselineStats | None,
) -> str | None:
    if not baseline:
        return None

    comparisons = []

    # CPU comparison
    current_cpu = aggregations.get("avg_cpu") or aggregations.get("avg_cpu_pct")
    if current_cpu is not None and baseline.avg_cpu_pct is not None and baseline.avg_cpu_pct > 0:
        diff = current_cpu - baseline.avg_cpu_pct
        if abs(diff) > 10:
            direction = "above" if diff > 0 else "below"
            comparisons.append(f"CPU {current_cpu:.1f}% ({abs(diff):.1f}pp {direction} baseline {baseline.avg_cpu_pct:.1f}%)")

    # Memory comparison
    current_mem = aggregations.get("avg_memory") or aggregations.get("avg_memory_pct")
    if current_mem is not None and baseline.avg_memory_pct is not None and baseline.avg_memory_pct > 0:
        diff = current_mem - baseline.avg_memory_pct
        if abs(diff) > 10:
            direction = "above" if diff > 0 else "below"
            comparisons.append(f"Memory {current_mem:.1f}% ({abs(diff):.1f}pp {direction} baseline {baseline.avg_memory_pct:.1f}%)")

    # Latency comparison
    current_lat = aggregations.get("avg_latency_ms") or aggregations.get("avg_duration")
    if current_lat is not None and baseline.avg_latency_ms is not None and baseline.avg_latency_ms > 0:
        diff_pct = ((current_lat - baseline.avg_latency_ms) / baseline.avg_latency_ms) * 100
        if abs(diff_pct) > 20:
            direction = "above" if diff_pct > 0 else "below"
            comparisons.append(f"Latency {current_lat:.1f}ms ({abs(diff_pct):.0f}% {direction} baseline {baseline.avg_latency_ms:.1f}ms)")

    # Error rate comparison
    current_err = aggregations.get("error_rate") or aggregations.get("avg_error_rate")
    if current_err is not None and baseline.avg_error_rate is not None:
        if current_err > baseline.avg_error_rate * 1.5:
            comparisons.append(f"Error rate {current_err:.2%} elevated vs baseline {baseline.avg_error_rate:.2%}")

    if comparisons:
        return "; ".join(comparisons)
    return None


def generate_analysis(
    question: str,
    evidence: RetrievedEvidence,
    baseline: BaselineStats | None,
) -> AnalysisOutput:
    evidence_str = format_evidence(evidence)
    baseline_str = format_baselines(baseline)
    baseline_comparison = compare_to_baseline(evidence.aggregations, baseline)

    # Collect citations from evidence
    citations = []
    for log in evidence.logs[:5]:
        cite = log.source or log.id
        if log.fields.get("alertname"):
            cite = f"{log.fields['alertname']}@{log.source or 'unknown'}"
        citations.append(cite)
    if not citations and evidence.aggregations:
        if "by_group" in evidence.aggregations:
            citations = list(evidence.aggregations["by_group"].keys())[:5]
        else:
            citations = [f"metric:{k}" for k in list(evidence.aggregations.keys())[:5]]

    evidence_text = evidence_str
    if evidence.aggregations:
        evidence_text += f"\n\nRaw aggregations: {evidence.aggregations}"

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
            return result
    except Exception:
        pass

    # Fallback: structured answer from aggregations
    if not evidence.logs and not evidence.aggregations:
        source_label = evidence.data_source.value.replace("_", " ")
        return AnalysisOutput(
            answer=(
                "I could not find enough infrastructure data to answer that reliably. "
                f"The request did not return usable evidence from the configured {source_label} path."
            ),
            citations=[],
        )

    if evidence.aggregations.get("error") and len(evidence.aggregations) <= 2:
        return AnalysisOutput(
            answer=(
                "I could not complete the full lookup because the backing datasource returned an error. "
                f"Details: {evidence.aggregations['error']}"
            ),
            citations=[],
        )

    agg = evidence.aggregations
    answer_parts = []

    if "by_group" in agg:
        group_data = agg["by_group"]
        lines = []
        for grp, metrics in list(group_data.items())[:10]:
            parts = []
            for k, v in metrics.items():
                if v is None:
                    continue
                if isinstance(v, float):
                    if "pct" in k or "rate" in k:
                        parts.append(f"{k}={v:.1f}%")
                    elif "ms" in k:
                        parts.append(f"{k}={v:.1f}ms")
                    else:
                        parts.append(f"{k}={v:.2f}")
                else:
                    parts.append(f"{k}={v}")
            lines.append(f"  **{grp}**: {', '.join(parts)}")
        answer_parts.append(f"Results ({len(group_data)} hosts/services):\n\n" + "\n".join(lines))
    elif agg:
        parts = []
        for k, v in agg.items():
            if isinstance(v, (dict, list)) or v is None:
                continue
            if isinstance(v, float):
                if "pct" in k or "rate" in k:
                    parts.append(f"{k}: **{v:.1f}%**")
                elif "ms" in k:
                    parts.append(f"{k}: **{v:.1f}ms**")
                else:
                    parts.append(f"{k}: **{v:.2f}**")
            else:
                parts.append(f"{k}: **{v}**")
        answer_parts.append(", ".join(parts))

    if baseline_comparison:
        answer_parts.append(f"\nBaseline: {baseline_comparison}")

    # Data source annotation
    if evidence.data_source.value == "prometheus":
        answer_parts.append("\n*Data source: Prometheus (live)*")
    elif evidence.data_source.value == "multi":
        answer_parts.append("\n*Data source: Prometheus (live) + Elasticsearch (historical)*")

    answer = "\n".join(answer_parts) if answer_parts else "Data retrieved but could not be summarized."

    return AnalysisOutput(
        answer=answer,
        baseline_comparison=baseline_comparison,
        citations=citations,
    )
