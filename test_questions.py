import requests
import json
import time

URL_V1 = "http://localhost:8000/ask"
URL_V2 = "http://localhost:8000/v2/ask"

QUESTIONS = [
    {
        "name": "Structured Aggregation",
        "query": "What is the average latency and volume for AAPL in the last 4 hours?"
    },
    {
        "name": "Comparison Query",
        "query": "Compare the average latency of AAPL vs NVDA for the last 6 hours."
    },
    {
        "name": "Spike Detection",
        "query": "Was there any latency spike for NVDA in the last hour? Show p95."
    },
    {
        "name": "Venue Analysis",
        "query": "Show me the performance (latency and error rate) broken down by venue for the last day."
    },
    {
        "name": "Order Drilldown",
        "query": "Give me a detailed drilldown for order_id ORD-001."
    },
    {
        "name": "Semantic Incident",
        "query": "Were there any major connectivity issues or incidents reported for TSLA recently?"
    }
]

def run_tests():
    print("=== Trading RAG V2 Test Suite ===\n")
    
    for q in QUESTIONS:
        print(f"Testing: {q['name']}")
        print(f"Query: \"{q['query']}\"")
        
        start = time.time()
        try:
            response = requests.post(URL_V2, json={"query": q["query"]}, timeout=45)
            duration = time.time() - start
            
            if response.status_code == 200:
                data = response.json()
                print(f"SUCCESS ({duration:.2f}s)")
                print(f"Confidence: {data.get('confidence')} | Groundedness: {data.get('groundedness_score')}")
                print(f"Freshness: {data.get('data_freshness')} | Cost: ${data.get('cost_usd')}")
                print(f"Answer: {data.get('answer')[:150]}...")
                if data.get('citations'):
                    print(f"Citations: {len(data['citations'])} logs")
            else:
                print(f"FAILED ({response.status_code}): {response.text}")
        except Exception as e:
            print(f"ERROR: {str(e)}")
        
        print("-" * 40)
        time.sleep(1)

if __name__ == "__main__":
    run_tests()
