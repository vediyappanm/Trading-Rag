---
name: infra-rag
description: Agentic RAG system for infrastructure monitoring and management. Collects all data from Prometheus, Grafana, ELK (Elasticsearch, Logstash, Kibana), Filebeat, SNMP exporters, Blackbox exporters, and Node exporters into Elasticsearch as a unified store, then uses a LangGraph multi-agent pipeline (Router → Retrieval → Analysis → Reflection) with LLM-powered query classification to answer infrastructure questions via chat. Supports live PromQL queries against Prometheus, Grafana dashboard deep-linking, historical log/metric/trace/alert analysis from ES, and cross-domain root cause investigation. Use this skill whenever someone asks about infrastructure monitoring, observability pipelines, ELK stack setup, Prometheus + Grafana integration, agentic RAG for DevOps/SRE, or building chat-based infrastructure management tools.
---

# InfraWatch RAG — Infrastructure Management Skill

An agentic RAG system that collects all infrastructure data into ELK and provides intelligent chat-based querying across metrics, logs, traces, alerts, and network data using a multi-agent LangGraph pipeline with live Prometheus and Grafana integration.

## Architecture Overview

```
Data Sources → Exporters → Prometheus → Logstash → Elasticsearch (unified store)
                                                          ↓
                                              Agentic RAG (LangGraph)
                                    Router → Retrieval → Analysis → Reflection
                                                          ↓
                                                    Chat UI (InfraWatch)
```

### Three Data Source Strategy

| Source | Use Case | Protocol |
|--------|----------|----------|
| **Prometheus** (live) | Current metrics, active alerts, target health | HTTP API + PromQL |
| **Elasticsearch** (historical) | Logs, traces, aggregated metrics, alert history | ES\|QL + Search API |
| **Grafana** (dashboards) | Dashboard discovery, deep-links, panel metadata | HTTP API |

## Core Components

### 1. Data Collection Layer

All infrastructure data flows into Elasticsearch through Logstash pipelines:

- **Prometheus metrics** → `remote_write` → Logstash HTTP input → `infra-metrics-*` index
- **Application/system logs** → Filebeat/Fluent Bit → Logstash Beats input → `infra-logs-*` index
- **Prometheus alerts** → Alertmanager webhook → Logstash HTTP input → `infra-alerts-*` index
- **Distributed traces** → Grafana Tempo → `infra-traces-*` index
- **SNMP/network metrics** → SNMP Exporter → Prometheus → `infra-network-*` index
- **HTTP/ICMP probes** → Blackbox Exporter → Prometheus → `infra-blackbox-*` index
- **Syslog** → Logstash syslog input → `infra-logs-*` index

Config files are in `config/` — see `references/data-pipeline.md` for details.

### 2. Query Router (LLM + Regex Fallback)

The router classifies every incoming question into:

- **Domain**: `infra_metrics`, `infra_logs`, `infra_traces`, `infra_alerts`, `infra_network`, `infra_uptime`, `cross_domain`
- **Query Type**: `cpu_spike`, `memory_pressure`, `disk_alert`, `error_search`, `alert_active`, `trace_latency`, `service_down`, etc.
- **Query Path**: `prometheus_live`, `metric_aggregation`, `log_search`, `trace_search`, `alert_search`, `grafana_dashboard`, `cross_index`
- **Data Source**: `prometheus` (live), `elasticsearch` (historical), `grafana` (dashboards), `multi` (combined)
- **Target**: extracted hostname, service name, IP, or container name

Live detection keywords: "right now", "currently", "live", "at this moment"
Dashboard detection keywords: "dashboard", "grafana", "panel", "graph", "chart"

Source: `src/infra_rag/router/agent.py`

### 3. Retrieval Layer

Three retrieval backends:

**Prometheus Live** (`_retrieve_from_prometheus`):
- Executes instant PromQL queries via Prometheus HTTP API
- Returns current metric values grouped by instance
- Generates Grafana Explore deep-links automatically
- Fetches active alerts via `/api/v1/alerts`

**Elasticsearch Historical** (ES|QL queries):
- 15+ retrieval functions for metrics, logs, traces, alerts, network, blackbox
- Cross-domain search combining metrics + logs + alerts for root cause analysis
- Evidence caching (exact hash + semantic embedding)

**Grafana Dashboards** (`_retrieve_grafana_dashboards`):
- Searches dashboards by keyword and tag
- Extracts panel metadata (title, type, PromQL queries)
- Builds deep-link URLs with time range + variable substitution

Source: `src/infra_rag/retrieval/agent.py`, `src/infra_rag/retrieval/services.py`

### 4. Analysis Agent (LLM Synthesis)

The LLM receives evidence + baselines and produces:
- Concise factual answer with properly formatted metrics (CPU%, MB/s, ms)
- Baseline comparison ("CPU 92% vs 7-day baseline 45%")
- Citations (hostnames, timestamps, alert names)
- Grafana dashboard links and Explore URLs
- Data source annotation ("*Data source: Prometheus (live)*")
- Actionable recommendations

Fallback: structured answer from raw aggregations if LLM fails.

Source: `src/infra_rag/analysis/agent.py`

### 5. Reflection + Evaluation (Quality Gate)

Scores every answer on three dimensions (0.0–1.0):
- **Groundedness**: is the answer backed by evidence?
- **Correctness**: do numbers in the answer match evidence?
- **Citation**: are sources properly cited?

If scores < 0.90, the answer loops back for refinement (up to 3 iterations).
If no evidence exists, the system abstains rather than hallucinating.

Source: `src/infra_rag/evaluation/agent.py`, `src/infra_rag/reflection/agent.py`

### 6. Resilience Patterns

| Pattern | Implementation |
|---------|---------------|
| Circuit breakers | ES, Redis, Prometheus, Grafana — 5 failures → 30s cooldown |
| Two-tier cache | L1: exact hash (Redis), L2: semantic embedding similarity |
| Cost budget | 50K tokens, 6 LLM calls, $0.15 USD max per query |
| Rate limiting | 10 req/s per IP, token bucket algorithm |
| PII redaction | Email + phone stripped before LLM sees the query |
| ES\|QL guard | Text→MATCH rewrite, LIMIT injection, type conflict detection |

## Project Structure

```
Rag/
├── src/infra_rag/               # Python package
│   ├── agents/workflow.py       # LangGraph state machine
│   ├── router/agent.py          # Query classification (LLM + regex)
│   ├── retrieval/               # ES|QL, Prometheus, Grafana retrieval
│   │   ├── agent.py             # Route to correct retrieval backend
│   │   └── services.py          # 15+ ES|QL query functions
│   ├── analysis/agent.py        # LLM synthesis + Grafana link rendering
│   ├── reflection/agent.py      # Quality evaluation
│   ├── evaluation/agent.py      # Groundedness/correctness scoring
│   ├── baselines/service.py     # 7-day baseline computation from ES
│   ├── clients/                 # External service clients
│   │   ├── elasticsearch.py     # ES client with circuit breaker
│   │   ├── prometheus.py        # Prometheus HTTP API + PromQL helpers
│   │   ├── grafana.py           # Grafana API + dashboard deep-linking
│   │   ├── redis.py             # Redis client with circuit breaker
│   │   ├── llm.py               # LLM provider factory (OpenAI/Anthropic/Groq)
│   │   └── embeddings.py        # OpenAI embeddings for semantic cache
│   ├── api/routes.py            # FastAPI endpoints (/ask, /ask/stream, /v2/ask)
│   ├── config.py                # Pydantic settings (ES, Redis, Prometheus, Grafana, LLM)
│   ├── models.py                # Domain models (QueryDomain, QueryPath, DataSource, etc.)
│   └── (cache, cost, freshness, pii, esql_guard, resilience, etc.)
├── config/                      # Infrastructure configs
│   ├── prometheus/prometheus.yml # Scrape configs + remote_write
│   ├── prometheus/alerts.yml    # 9 alert rules
│   ├── logstash/pipeline/       # 4 Logstash pipelines
│   ├── alertmanager/            # Alert routing + webhook to ES
│   ├── grafana/provisioning/    # Auto-provisioned datasources
│   ├── filebeat/filebeat.yml    # Log collection config
│   ├── blackbox/blackbox.yml    # HTTP/ICMP probe modules
│   └── elasticsearch/           # Index templates + ILM policy
├── docker-compose-full.yml      # Full stack (11 services)
├── Dockerfile                   # Python 3.11 app container
├── index.html                   # Chat UI (InfraWatch)
└── pyproject.toml               # Python dependencies
```

## Elasticsearch Indices

| Index | Source | Key Fields |
|-------|--------|------------|
| `infra-metrics-*` | Prometheus via Logstash | `host.name`, `cpu.usage_pct`, `memory.usage_pct`, `disk.usage_pct`, `network.bytes_in/out` |
| `infra-logs-*` | Filebeat/Fluent Bit | `host.name`, `service.name`, `log.level`, `message`, `error.message` |
| `infra-traces-*` | Grafana Tempo | `trace.id`, `span.id`, `service.name`, `duration_ms`, `status.code` |
| `infra-alerts-*` | Alertmanager webhook | `alertname`, `severity`, `state`, `instance`, `description` |
| `infra-network-*` | SNMP Exporter | `host.name`, `device.name`, `interface.bytes_in/out`, `device.cpu_pct` |
| `infra-blackbox-*` | Blackbox Exporter | `probe.target`, `probe.duration_ms`, `probe.success` |

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/ask` | Synchronous infrastructure query |
| `POST` | `/ask/stream` | SSE streaming response |
| `POST` | `/v2/ask` | Enhanced with scores, costs, freshness |
| `GET` | `/health` | Liveness probe |
| `GET` | `/health/ready` | Readiness (ES + Redis + Prometheus + Grafana) |
| `GET` | `/metrics` | Observability metrics |
| `GET` | `/api/stats` | Infrastructure dashboard stats |

## Example Queries

### Live Metrics (Prometheus)
- "What is the CPU usage right now?"
- "What's the current memory on web-01?"
- "Show me live disk usage across all hosts"

### Historical Analysis (Elasticsearch)
- "Show me all error logs from the last hour"
- "Which services have the highest latency this week?"
- "How many alerts fired yesterday?"

### Dashboard Discovery (Grafana)
- "Show me the Grafana dashboard for web-01"
- "Open the network monitoring dashboard"

### Cross-Domain Investigation
- "Why is the API server down?"
- "What happened during the 2am outage?"
- "Investigate high latency on order-service"

## Setup & Deployment

```bash
# 1. Create ES index templates
bash config/elasticsearch/setup-indices.sh

# 2. Start full stack
docker compose -f docker-compose-full.yml up -d

# 3. Access points
# Chat UI:    http://localhost:8123
# Kibana:     http://localhost:5601
# Grafana:    http://localhost:3000  (admin/admin)
# Prometheus: http://localhost:9090

# 4. Dev mode (local)
pip install -e .
python -m uvicorn infra_rag.main:app --host 0.0.0.0 --port 8000 --reload
```

## Technology Stack

| Component | Technology |
|-----------|------------|
| Backend | FastAPI + Uvicorn (Python 3.11) |
| Agent Orchestration | LangGraph |
| LLM | LangChain (OpenAI / Anthropic / Groq) |
| Vector Store | Elasticsearch 8.15+ |
| Caching | Redis 7+ |
| Metrics | Prometheus + Node Exporter |
| Visualization | Grafana 11+ |
| Log Collection | Filebeat + Logstash |
| Alerting | Prometheus Alertmanager |
| Network | SNMP Exporter + Blackbox Exporter |
| Container | Docker + Docker Compose |
| Frontend | Vanilla JS (SSE streaming, dark mode) |

For detailed pipeline configs, read `references/data-pipeline.md`.
For the complete architecture flow diagram, read `references/architecture.md`.
