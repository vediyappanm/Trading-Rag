from datetime import datetime, timedelta
import random
import uuid

from trading_rag.clients import es_client
from trading_rag.config import settings

def ingest_test_data():
    es = es_client.client
    now = datetime.utcnow()
    
    # Create indices if they don't exist
    for index in [
        settings.elasticsearch.execution_logs_index,
        settings.elasticsearch.feed_logs_index,
        settings.elasticsearch.incidents_index
    ]:
        if not es.indices.exists(index=index):
            es.indices.create(index=index)
            print(f"Created index {index}")
            
    # Ingest Execution Logs
    print("Ingesting execution logs...")
    symbols = ["AAPL", "GOOGL", "MSFT", "TSLA"]
    for i in range(100):
        timestamp = now - timedelta(minutes=random.randint(1, 120))
        doc = {
            "@timestamp": timestamp.isoformat(),
            "symbol": random.choice(symbols),
            "latency_ms": random.uniform(5.0, 150.0),
            "volume": random.randint(100, 5000),
            "status": "success" if random.random() > 0.1 else "error",
            "message": "Order executed successfully" if random.random() > 0.1 else "Execution timeout",
            "type": "execution"
        }
        es.index(index=settings.elasticsearch.execution_logs_index, document=doc)
        
    # Ingest Feed Logs
    print("Ingesting feed logs...")
    for i in range(100):
        timestamp = now - timedelta(minutes=random.randint(1, 120))
        doc = {
            "@timestamp": timestamp.isoformat(),
            "symbol": random.choice(symbols),
            "price": random.uniform(100.0, 1000.0),
            "bid_ask_spread": random.uniform(0.01, 0.5),
            "message": "Price update received",
            "type": "feed"
        }
        es.index(index=settings.elasticsearch.feed_logs_index, document=doc)
        
    # Ingest Incidents
    print("Ingesting incidents...")
    incidents = [
        ("AAPL execution latency spike", "AAPL"),
        ("Feed disconnected for TSLA", "TSLA"),
        ("Database timeout error affecting order routing", None),
        ("High error rate on MSFT executions", "MSFT")
    ]
    for desc, sym in incidents:
        timestamp = now - timedelta(hours=random.randint(1, 24))
        doc = {
            "@timestamp": timestamp.isoformat(),
            "description": desc,
            "symbol": sym,
            "severity": random.choice(["high", "medium", "low"]),
            "status": "resolved"
        }
        es.index(index=settings.elasticsearch.incidents_index, document=doc)
        
    # Refresh to make searchable
    es.indices.refresh(index=settings.elasticsearch.execution_logs_index)
    es.indices.refresh(index=settings.elasticsearch.feed_logs_index)
    es.indices.refresh(index=settings.elasticsearch.incidents_index)
    
    print("Successfully ingested test data!")
    
if __name__ == "__main__":
    ingest_test_data()
