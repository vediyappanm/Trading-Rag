# Architecture Reference

## Table of Contents
1. [System Architecture](#system-architecture)
2. [LangGraph Pipeline](#langgraph-pipeline)
3. [Query Routing Logic](#query-routing-logic)
4. [Data Source Selection](#data-source-selection)
5. [Resilience Architecture](#resilience-architecture)
6. [Docker Stack](#docker-stack)

---

## System Architecture

```
┌─────────────── DATA SOURCES ──────────────────┐
│  Servers → Node Exporter ──┐                   │
│  Apps → Filebeat ──────────┤                   │
│  Network → SNMP Exporter ──┤→ Prometheus       │
│  Probes → Blackbox ────────┤   │ remote_write  │
│  DBs → DB Exporters ───────┘   ↓               │
│                            Logstash (4 pipes)   │
│  Alertmanager ──webhook──→ Logstash            │
│  Syslog ─────────────────→ Logstash            │
└───────────────────┬───────────────────────────┘
                    ↓
┌─────────── ELASTICSEARCH ─────────────────────┐
│  infra-metrics  infra-logs   infra-alerts      │
│  infra-traces   infra-network infra-blackbox   │
└───────────────────┬───────────────────────────┘
                    ↓
┌─────────── AGENTIC RAG ──────────────────────┐
│  Router → classify domain/type/source         │
│  Retrieval → Prometheus | ES | Grafana | Multi│
│  Analysis → LLM synthesis + Grafana links     │
│  Reflection → groundedness quality gate       │
└───────────────────┬───────────────────────────┘
                    ↓
              Chat UI (InfraWatch)
```

---

## LangGraph Pipeline

```
START → ROUTER → RETRIEVAL → ANALYSIS → REFLECTION
                     ↓              ↑         │
                  [cached?]    [refine]   [score ≥ 0.9?]
                   yes→END       ↑←────── no (max 3x)
                                          yes→ FINAL → END
```

### State Machine (AgentState)

Key fields flowing through the pipeline:
- `question`: user's raw query
- `router_output`: domain, type, path, data_source, target, esql_query, promql_query
- `evidence`: logs + aggregations from any data source
- `baseline`: 7-day statistical baseline for comparison
- `analysis`: LLM-generated answer with citations
- `groundedness_score`, `correctness_score`, `citation_score`: quality gates

---

## Query Routing Logic

### Domain Classification

| Keywords | Domain | Typical Path |
|----------|--------|-------------|
| cpu, processor, load | `infra_metrics` | `metric_aggregation` or `prometheus_live` |
| memory, ram, oom, swap | `infra_metrics` | `metric_aggregation` or `prometheus_live` |
| disk, storage, filesystem | `infra_metrics` | `metric_aggregation` or `prometheus_live` |
| log, error, exception, stack trace | `infra_logs` | `log_search` |
| trace, span, tracing | `infra_traces` | `trace_search` |
| alert, firing, alertmanager | `infra_alerts` | `alert_search` or `prometheus_live` |
| snmp, switch, router, firewall | `infra_network` | `network_query` |
| uptime, probe, blackbox | `infra_uptime` | `structured_esql` |
| why is, what happened, investigate | `cross_domain` | `cross_index` |
| dashboard, grafana, panel | any | `grafana_dashboard` |

### Live vs Historical Detection

- **Live** (DataSource.PROMETHEUS): "right now", "currently", "current", "live", "what is the"
- **Dashboard** (DataSource.GRAFANA): "dashboard", "grafana", "panel", "graph"
- **Historical** (DataSource.ELASTICSEARCH): everything else
- **Multi** (DataSource.MULTI): "why is X down", "root cause", "investigate"

---

## Data Source Selection

```
User: "What is the CPU right now on web-01?"
  → is_live=True, target="web-01"
  → DataSource.PROMETHEUS
  → promql: 100 - (avg by(instance)(rate(node_cpu_seconds_total{mode="idle", instance=~"web-01.*"}[5m])) * 100)
  → Calls Prometheus /api/v1/query
  → Returns current value + Grafana Explore link

User: "Show me error logs from the last 24 hours"
  → is_live=False
  → DataSource.ELASTICSEARCH
  → esql: FROM "infra-logs" | WHERE log.level IN ("ERROR","FATAL") ...
  → Returns log entries from ES

User: "Show me the Grafana dashboard for web-01"
  → is_dashboard=True, target="web-01"
  → DataSource.GRAFANA
  → Searches Grafana /api/search
  → Returns dashboard links with ?var-host=web-01

User: "Why is web-01 down?"
  → DataSource.MULTI
  → Parallel: Prometheus up{} + ES error logs + ES alerts
  → LLM correlates all three for root cause
```

---

## Resilience Architecture

### Circuit Breakers (per client)
- Threshold: 5 consecutive failures
- Cooldown: 30 seconds
- Applies to: Elasticsearch, Redis, Prometheus, Grafana

### Caching Strategy
- **L1 (Exact)**: Hash of query + context → Redis (TTL: 300s)
- **L2 (Semantic)**: Embedding similarity > 0.95 → Redis
- **Evidence cache**: Per-retrieval results → Redis (TTL: 60s)

### Cost Controls
- Max tokens per query: 50,000
- Max LLM calls per query: 6
- Max USD per query: $0.15
- Enforced via CostBudget in workflow

### Freshness TTLs
- CPU spike: 15s
- Service down: 10s
- Error search: 30s
- Capacity planning: 600s

---

## Docker Stack

11 services in `docker-compose-full.yml`:

| Service | Port | Role |
|---------|------|------|
| elasticsearch | 9200 | Unified data store |
| kibana | 5601 | Data exploration UI |
| logstash | 5044, 8080, 8081, 5140 | Data pipeline hub |
| prometheus | 9090 | Metric collection + PromQL |
| alertmanager | 9093 | Alert routing |
| grafana | 3000 | Visualization + dashboards |
| node-exporter | 9100 | Host metrics |
| blackbox-exporter | 9115 | HTTP/ICMP probes |
| filebeat | — | Log shipping |
| redis | 6379 | Caching |
| infra-rag | 8123 | RAG application |
