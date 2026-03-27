# Data Pipeline Reference

## Table of Contents
1. [Prometheus Remote Write](#prometheus-remote-write)
2. [Logstash Pipelines](#logstash-pipelines)
3. [Filebeat Configuration](#filebeat-configuration)
4. [Alertmanager Webhook](#alertmanager-webhook)
5. [Blackbox Exporter](#blackbox-exporter)
6. [ES Index Templates](#es-index-templates)
7. [Grafana Datasources](#grafana-datasources)

---

## Prometheus Remote Write

Prometheus scrapes metrics from exporters and forwards them to Logstash via `remote_write`:

```yaml
# config/prometheus/prometheus.yml
remote_write:
  - url: "http://logstash:8080"
    write_relabel_configs:
      - source_labels: [__name__]
        regex: "go_.*|promhttp_.*"
        action: drop  # Drop high-cardinality internal metrics

scrape_configs:
  - job_name: "node-exporter"
    static_configs:
      - targets: ["node-exporter:9100"]

  - job_name: "blackbox-http"
    metrics_path: /probe
    params:
      module: [http_2xx]
    static_configs:
      - targets:
          - http://infra-rag-app:8000/health
          - http://grafana:3000
```

### Adding New Scrape Targets

To monitor a new host/service, add it to `scrape_configs`:

```yaml
  - job_name: "my-app"
    static_configs:
      - targets: ["my-app:8080"]
    metrics_path: /metrics  # default
```

---

## Logstash Pipelines

Four pipelines process different data sources:

### Pipeline 1: Beats Input (Filebeat/Fluent Bit → infra-logs)
- **Port**: 5044
- **Source**: Filebeat, Fluent Bit
- **Target index**: `infra-logs-YYYY.MM.dd`
- **Processing**: JSON parsing, log level normalization, service name extraction

### Pipeline 2: Prometheus Input (Prometheus → infra-metrics)
- **Port**: 8080
- **Source**: Prometheus remote_write
- **Target index**: `infra-metrics-YYYY.MM.dd`
- **Processing**: Label extraction, field mapping (node_cpu → cpu.usage_pct), numeric conversion

### Pipeline 3: Alerts Input (Alertmanager → infra-alerts)
- **Port**: 8081
- **Source**: Alertmanager webhook
- **Target index**: `infra-alerts-YYYY.MM.dd`
- **Processing**: Alert array splitting, label extraction, timestamp parsing

### Pipeline 4: Syslog Input (Syslog → infra-logs)
- **Port**: 5140
- **Source**: rsyslog/syslog-ng
- **Target index**: `infra-logs-YYYY.MM.dd`
- **Processing**: Severity-to-level mapping

---

## Filebeat Configuration

Collects three types of logs:

1. **System logs**: `/var/log/syslog`, `/var/log/auth.log`, `/var/log/messages`
2. **Application logs**: `/var/log/app/**/*.log` (JSON format)
3. **Docker container logs**: `/var/lib/docker/containers/*/*.log`

Output goes to Logstash at port 5044. Docker metadata is automatically enriched.

---

## Alertmanager Webhook

All Prometheus alerts (both firing and resolved) are sent to Logstash at port 8081 via HTTP webhook. This ensures every alert event is indexed in ES for historical analysis.

Route config:
- Critical alerts → `critical` receiver → Logstash webhook
- All other alerts → `default` receiver → Logstash webhook

---

## Blackbox Exporter

Modules configured:
- `http_2xx`: HTTP probes (GET, follow redirects, expect 200-204)
- `http_post_2xx`: HTTP POST probes
- `icmp`: ICMP ping probes
- `tcp_connect`: TCP connection probes

Probe targets are configured in Prometheus scrape configs, not in Blackbox itself.

---

## ES Index Templates

Six templates are created by `config/elasticsearch/setup-indices.sh`:

| Template | Pattern | Shards | ILM Policy |
|----------|---------|--------|------------|
| `infra-metrics` | `infra-metrics-*` | 1 | 30-day retention |
| `infra-logs` | `infra-logs-*` | 1 | 30-day retention |
| `infra-traces` | `infra-traces-*` | 1 | — |
| `infra-alerts` | `infra-alerts-*` | 1 | — |
| `infra-network` | `infra-network-*` | 1 | — |
| `infra-blackbox` | `infra-blackbox-*` | 1 | — |

ILM policy `infra-retention-30d`:
- Hot phase: rollover at 1 day or 10GB
- Delete phase: after 30 days

---

## Grafana Datasources

Auto-provisioned via `config/grafana/provisioning/datasources/datasources.yml`:

1. **Prometheus** (default) — `http://prometheus:9090`
2. **Elasticsearch - Metrics** — `infra-metrics-*`
3. **Elasticsearch - Logs** — `infra-logs-*`
4. **Elasticsearch - Alerts** — `infra-alerts-*`
