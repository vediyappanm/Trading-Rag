from datetime import datetime, timedelta
import random
from elasticsearch.helpers import bulk

from trading_rag.clients import es_client
from trading_rag.config import settings

def ingest_high_volume_data_bulk(count=1000):
    es = es_client.client
    now = datetime.utcnow()
    
    symbols = ["AAPL", "GOOGL", "MSFT", "TSLA", "AMZN", "NVDA", "META"]
    
    print(f"Starting BULK ingestion: {count} logs per index...")

    # Prepare Execution Logs
    execution_actions = []
    for i in range(count):
        minutes_back = random.randint(1, 1440)
        timestamp = now - timedelta(minutes=minutes_back)
        symbol = random.choice(symbols)
        
        is_spike = (symbol == "NVDA" and 240 <= minutes_back <= 300)
        
        if is_spike:
            latency = random.uniform(500.0, 1200.0)
            status = "error" if random.random() > 0.4 else "success"
            message = "Latency threshold exceeded" if status == "error" else "Delayed execution"
        else:
            latency = random.uniform(10.0, 80.0)
            status = "success" if random.random() > 0.05 else "error"
            message = "Order executed successfully" if status == "success" else "Execution timeout"

        action = {
            "_index": settings.elasticsearch.execution_logs_index,
            "_source": {
                "@timestamp": timestamp.isoformat(),
                "symbol": symbol,
                "latency_ms": latency,
                "volume": random.randint(50, 10000),
                "status": status,
                "message": message,
                "venue": random.choice(["NASDAQ", "NYSE", "ARCA", "BATS", "IEX"]),
                "type": "execution"
            }
        }
        execution_actions.append(action)

    # Prepare Feed Logs
    feed_actions = []
    for i in range(count):
        timestamp = now - timedelta(minutes=random.randint(1, 1440))
        action = {
            "_index": settings.elasticsearch.feed_logs_index,
            "_source": {
                "@timestamp": timestamp.isoformat(),
                "symbol": random.choice(symbols),
                "price": random.uniform(100.0, 2000.0),
                "bid_ask_spread": random.uniform(0.005, 0.2),
                "message": "Market data update",
                "type": "feed"
            }
        }
        feed_actions.append(action)

    # Prepare Incidents
    incident_actions = []
    incident_samples = [
        {"symbol": "TSLA", "description": "Minor connectivity lag detected in the Chicago gateway affecting TSLA routing."},
        {"symbol": "AAPL", "description": "NYSE price bridge experienced a 2-second freeze during peak volume."},
        {"symbol": None, "description": "Global VPS maintenance performed; possible intermittent latency spikes expected."},
        {"symbol": "NVDA", "description": "High-priority alert: Data center power fluctuation in Tokyo region caused NVDA order timeouts."},
        {"symbol": "TSLA", "description": "Packet loss detected on the primary fiber link between London and New York, specifically impacting TSLA execution feed."}
    ]
    for sample in incident_samples:
        timestamp = now - timedelta(minutes=random.randint(1, 1440))
        action = {
            "_index": settings.elasticsearch.incidents_index,
            "_source": {
                "@timestamp": timestamp.isoformat(),
                "symbol": sample["symbol"],
                "description": sample["description"],
                "severity": random.choice(["low", "medium", "high"]),
                "type": "incident"
            }
        }
        incident_actions.append(action)

    # Execute Bulks
    print("Bulk pushing execution logs...")
    success, failed = bulk(es, execution_actions)
    print(f"  Execution logs: {success} succeeded, {failed} failed")

    print("Bulk pushing market feed logs...")
    success, failed = bulk(es, feed_actions)
    print(f"  Feed logs: {success} succeeded, {failed} failed")

    print("Bulk pushing incident reports...")
    success, failed = bulk(es, incident_actions)
    print(f"  Incidents: {success} succeeded, {failed} failed")
    
    print("Refreshing indices...")
    es.indices.refresh(index=settings.elasticsearch.execution_logs_index)
    es.indices.refresh(index=settings.elasticsearch.feed_logs_index)
    es.indices.refresh(index=settings.elasticsearch.incidents_index)
    
    print("Successfully ingested 2000+ logs and incidents into the VPS via BULK mode!")

if __name__ == "__main__":
    ingest_high_volume_data_bulk(1000)
