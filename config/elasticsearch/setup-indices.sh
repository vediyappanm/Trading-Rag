#!/bin/bash
# Setup Elasticsearch index templates for Infrastructure RAG
# Run: bash config/elasticsearch/setup-indices.sh

ES_HOST="${ES_HOST:-http://localhost:9200}"

echo "Setting up Elasticsearch index templates on $ES_HOST..."

# ─── infra-metrics template ───
curl -s -X PUT "$ES_HOST/_index_template/infra-metrics" -H 'Content-Type: application/json' -d '{
  "index_patterns": ["infra-metrics-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "index.lifecycle.name": "infra-retention-30d"
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "host.name": { "type": "keyword" },
        "host.ip": { "type": "ip" },
        "service.name": { "type": "keyword" },
        "container.name": { "type": "keyword" },
        "container.id": { "type": "keyword" },
        "prometheus.job": { "type": "keyword" },
        "prometheus.instance": { "type": "keyword" },
        "metric.name": { "type": "keyword" },
        "metric.value": { "type": "float" },
        "metric.unit": { "type": "keyword" },
        "cpu.usage_pct": { "type": "float" },
        "memory.usage_pct": { "type": "float" },
        "swap.usage_pct": { "type": "float" },
        "disk.usage_pct": { "type": "float" },
        "network.bytes_in": { "type": "float" },
        "network.bytes_out": { "type": "float" },
        "value": { "type": "float" },
        "labels": { "type": "object", "dynamic": true }
      }
    }
  },
  "priority": 200
}'
echo ""

# ─── infra-logs template ───
curl -s -X PUT "$ES_HOST/_index_template/infra-logs" -H 'Content-Type: application/json' -d '{
  "index_patterns": ["infra-logs-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0,
      "index.lifecycle.name": "infra-retention-30d"
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "host.name": { "type": "keyword" },
        "host.ip": { "type": "ip" },
        "service.name": { "type": "keyword" },
        "container.name": { "type": "keyword" },
        "source": { "type": "keyword" },
        "agent.type": { "type": "keyword" },
        "log.level": { "type": "keyword" },
        "message": { "type": "text", "fields": { "keyword": { "type": "keyword", "ignore_above": 512 } } },
        "error.message": { "type": "text" },
        "error.stack_trace": { "type": "text" }
      }
    }
  },
  "priority": 200
}'
echo ""

# ─── infra-traces template ───
curl -s -X PUT "$ES_HOST/_index_template/infra-traces" -H 'Content-Type: application/json' -d '{
  "index_patterns": ["infra-traces-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "trace.id": { "type": "keyword" },
        "span.id": { "type": "keyword" },
        "parent.span.id": { "type": "keyword" },
        "service.name": { "type": "keyword" },
        "operation.name": { "type": "keyword" },
        "duration_ms": { "type": "float" },
        "status.code": { "type": "keyword" }
      }
    }
  },
  "priority": 200
}'
echo ""

# ─── infra-alerts template ───
curl -s -X PUT "$ES_HOST/_index_template/infra-alerts" -H 'Content-Type: application/json' -d '{
  "index_patterns": ["infra-alerts-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "alertname": { "type": "keyword" },
        "severity": { "type": "keyword" },
        "state": { "type": "keyword" },
        "instance": { "type": "keyword" },
        "host.name": { "type": "keyword" },
        "description": { "type": "text" },
        "summary": { "type": "text" }
      }
    }
  },
  "priority": 200
}'
echo ""

# ─── infra-network template ───
curl -s -X PUT "$ES_HOST/_index_template/infra-network" -H 'Content-Type: application/json' -d '{
  "index_patterns": ["infra-network-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "host.name": { "type": "keyword" },
        "device.name": { "type": "keyword" },
        "interface.name": { "type": "keyword" },
        "interface.bytes_in": { "type": "float" },
        "interface.bytes_out": { "type": "float" },
        "device.cpu_pct": { "type": "float" },
        "device.memory_pct": { "type": "float" }
      }
    }
  },
  "priority": 200
}'
echo ""

# ─── infra-blackbox template ───
curl -s -X PUT "$ES_HOST/_index_template/infra-blackbox" -H 'Content-Type: application/json' -d '{
  "index_patterns": ["infra-blackbox-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 0
    },
    "mappings": {
      "properties": {
        "@timestamp": { "type": "date" },
        "probe.target": { "type": "keyword" },
        "probe.type": { "type": "keyword" },
        "probe.duration_ms": { "type": "float" },
        "probe.success": { "type": "boolean" }
      }
    }
  },
  "priority": 200
}'
echo ""

# ─── ILM policy for 30-day retention ───
curl -s -X PUT "$ES_HOST/_ilm/policy/infra-retention-30d" -H 'Content-Type: application/json' -d '{
  "policy": {
    "phases": {
      "hot": {
        "min_age": "0ms",
        "actions": {
          "rollover": {
            "max_age": "1d",
            "max_primary_shard_size": "10gb"
          }
        }
      },
      "delete": {
        "min_age": "30d",
        "actions": {
          "delete": {}
        }
      }
    }
  }
}'
echo ""

echo "Index templates created successfully!"
