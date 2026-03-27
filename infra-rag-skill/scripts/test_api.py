"""Quick API smoke test — verifies the /ask endpoint works end-to-end."""

import sys
import json
import httpx

BASE_URL = "http://localhost:8000"

QUERIES = [
    "What is the CPU usage right now?",
    "Show me all error logs from the last hour",
    "What alerts are currently firing?",
    "Show me the Grafana dashboard for web-01",
    "Why is the API server down?",
]


def main():
    print(f"Testing InfraWatch RAG at {BASE_URL}\n")

    # Health check
    try:
        resp = httpx.get(f"{BASE_URL}/health/ready", timeout=10)
        health = resp.json()
        print(f"Health: {json.dumps(health, indent=2)}\n")
    except Exception as e:
        print(f"Health check failed: {e}")
        return 1

    # Test queries
    for query in QUERIES:
        print(f"─── {query}")
        try:
            resp = httpx.post(
                f"{BASE_URL}/v2/ask",
                json={"query": query},
                timeout=60,
            )
            if resp.status_code == 200:
                data = resp.json()
                print(f"  Domain: {data.get('domain', '?')}")
                print(f"  Confidence: {data.get('confidence', '?')}")
                print(f"  Latency: {data.get('latency_ms', '?')}ms")
                print(f"  Cache: {data.get('from_cache', False)}")
                answer = data.get("answer", "")
                print(f"  Answer: {answer[:200]}{'...' if len(answer) > 200 else ''}")
            else:
                print(f"  ERROR {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            print(f"  ERROR: {e}")
        print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
