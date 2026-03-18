import json
import urllib.request
import time

def ask(query, f):
    url = "http://127.0.0.1:8000/ask"
    data = json.dumps({"query": query}).encode()
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers)
    
    print(f"Testing: {query}")
    try:
        t0 = time.time()
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            duration = time.time() - t0
            
            f.write(f"==================================================\n")
            f.write(f"Q: {query}\n")
            f.write(f"Router Path:  {result.get('router', {}).get('query_path', 'UNKNOWN')}\n")
            f.write(f"Router Type:  {result.get('router', {}).get('query_type', 'UNKNOWN')}\n")
            f.write(f"Latency:      {duration:.2f}s\n")
            f.write(f"--------------------------------------------------\n")
            f.write(f"Answer:\n{result.get('answer', 'No answer attached.')}\n\n")
            
    except Exception as e:
        f.write(f"==================================================\n")
        f.write(f"Q: {query}\n")
        f.write(f"Error: {e}\n\n")

queries = [
    # Baseline & Aggregation
    "What is the overall fill rate?",
    "Show me the total number of orders placed in the NSE exchange today.",
    "What is the average order quantity across all brokers?",
    
    # Top N & Symbol Drilling
    "What are the top 10 most traded symbols by order count?",
    "What is the status breakdown for NIFTY20JAN26C25400?",
    "How many buy vs sell orders are there for ICICIBANK?",
    
    # Broker Analytics & Rejections
    "Which broker has the highest rejection rate?",
    "Count the total cancelled orders by broker id.",
    "Give me the fill rate for broker VFS.",
    
    # Order Specific & Semantic Incidents
    "What happened to order id 26011900049185?",
    "Why did order 26011900049586 get updated?",
    
    # Flow & Price Imbalance (Dual Index Features)
    "Compare the execution vs feed volume for RELIANCE-EQ.",
    "Is there a buy/sell imbalance for INFY-EQ today?",
    
    # Exchange / Venue Analysis
    "Compare the total order volume and fill rate between NSE and BSE venues.",
    
    # Rate Limits & Spikes
    "Did we see any P95 latency spikes or order volume spikes for TRENT-EQ recently?"
]

print("Starting extensive testing suite... This will take a moment.")
with open("test_results_detailed.txt", "w", encoding="utf-8") as f:
    for q in queries:
        ask(q, f)
print("Finished testing! Check test_results_detailed.txt for full output.")
