import asyncio
import os
import sys
from datetime import datetime, timedelta
import random

# Add src to sys.path
sys.path.insert(0, os.path.join(os.getcwd(), 'src'))

from infra_rag.clients import es_client

async def seed_data():
    print("=== SEEDING DEMO DATA FOR INFRAWATCH RAG ===")
    
    # 1. Delete legacy/old data
    legacy_indices = ['trading-execution-logs', 'infra-logs', 'infra-metrics']
    for idx in legacy_indices:
        try:
            if es_client.client.indices.exists(index=idx):
                print(f"Deleting old index: {idx}")
                es_client.client.indices.delete(index=idx)
        except Exception as e:
            print(f"Error deleting index {idx}: {e}")

    # 2. Create infra-logs index with mappings
    print("Creating infra-logs index...")
    log_mapping = {
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "log.level": {"type": "keyword"},
                "message": {"type": "text"},
                "host.name": {"type": "keyword"},
                "service.name": {"type": "keyword"},
                "container.name": {"type": "keyword"},
                "error.message": {"type": "text"},
                "error.stack_trace": {"type": "text"},
                "source": {"type": "keyword"}
            }
        }
    }
    es_client.client.indices.create(index="infra-logs", body=log_mapping)

    # 3. Create infra-metrics index with mappings
    print("Creating infra-metrics index...")
    metric_mapping = {
        "mappings": {
            "properties": {
                "@timestamp": {"type": "date"},
                "host.name": {"type": "keyword"},
                "service.name": {"type": "keyword"},
                "metric.name": {"type": "keyword"},
                "metric.value": {"type": "float"},
                "metric.unit": {"type": "keyword"},
                "cpu.usage_pct": {"type": "float"},
                "memory.usage_pct": {"type": "float"},
                "disk.usage_pct": {"type": "float"},
                "network.bytes_in": {"type": "long"},
                "network.bytes_out": {"type": "long"}
            }
        }
    }
    es_client.client.indices.create(index="infra-metrics", body=metric_mapping)

    # 4. Generate Sample Logs
    print("Pushing sample logs...")
    services = ["api-gateway", "agent-router", "retrieval-service", "llm-proxy", "cache-manager"]
    hosts = ["fs-le-isv", "fs-infra-01", "fs-db-primary"]
    levels = ["INFO", "INFO", "INFO", "WARN", "ERROR"]
    
    log_docs = []
    now = datetime.utcnow()
    for i in range(100):
        ts = now - timedelta(minutes=i)
        lv = random.choice(levels)
        svc = random.choice(services)
        hst = random.choice(hosts)
        
        msg = f"Operation {random.randint(1000, 9999)} started for unit {random.randint(10, 99)}"
        if lv == "ERROR":
            msg = f"Critical Failure in {svc}: connection timeout to backend"
            error_msg = "ConnectionTimeout: The remote host closed the connection unexpectedly"
        else:
            error_msg = ""
            
        doc = {
            "@timestamp": ts.isoformat(),
            "log.level": lv,
            "message": msg,
            "host.name": hst,
            "service.name": svc,
            "container.name": f"{svc}-{random.randint(1, 4)}",
            "error.message": error_msg,
            "source": f"/var/log/{svc}.log"
        }
        log_docs.append(doc)
    
    # Bulk push using raw client
    for d in log_docs:
        es_client.client.index(index="infra-logs", body=d)

    # 5. Generate Sample Metrics
    print("Pushing sample metrics...")
    metric_docs = []
    for h in hosts:
        for i in range(20):
            ts = now - timedelta(minutes=i*2)
            doc = {
                "@timestamp": ts.isoformat(),
                "host.name": h,
                "service.name": "system",
                "cpu.usage_pct": random.uniform(10, 85),
                "memory.usage_pct": random.uniform(20, 75),
                "disk.usage_pct": 45.5,
                "network.bytes_in": random.randint(100000, 5000000),
                "network.bytes_out": random.randint(100000, 5000000)
            }
            metric_docs.append(doc)
            
    for d in metric_docs:
        es_client.client.index(index="infra-metrics", body=d)

    print(f"SUCCESS: Pushed {len(log_docs)} logs and {len(metric_docs)} metrics.")

if __name__ == "__main__":
    asyncio.run(seed_data())
