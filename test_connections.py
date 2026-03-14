from trading_rag.clients import es_client, redis_client
import sys

print("Testing Elasticsearch...")
try:
    info = es_client.client.info()
    print("Elasticsearch Success!")
    print(info)
except Exception as e:
    print(f"Elasticsearch Failed: {e}")

print("\nTesting Redis...")
try:
    redis_client.client.ping()
    print("Redis Success!")
except Exception as e:
    print(f"Redis Failed: {e}")
