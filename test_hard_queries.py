import json
import urllib.request
import time

def ask(query, f):
    url = "http://127.0.0.1:8000/ask"
    data = json.dumps({"query": query}).encode()
    headers = {"Content-Type": "application/json"}
    req = urllib.request.Request(url, data=data, headers=headers)
    
    print(f"Testing Hard Query: {query}")
    try:
        t0 = time.time()
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode())
            duration = time.time() - t0
            
            f.write(f"==================================================\n")
            f.write(f"Q: {query}\n")
            f.write(f"Router Path:  {result.get('router', {}).get('query_path', result.get('query_path', 'UNKNOWN'))}\n")
            f.write(f"Router Type:  {result.get('router', {}).get('query_type', 'UNKNOWN')}\n")
            f.write(f"Latency:      {duration:.2f}s\n")
            f.write(f"--------------------------------------------------\n")
            f.write(f"Answer:\n{result.get('answer', 'No answer attached.')}\n\n")
            
    except Exception as e:
        f.write(f"==================================================\n")
        f.write(f"Q: {query}\n")
        f.write(f"Error: {e}\n\n")

queries = [
    "My top strategy was executing heavily in BANKNIFTY21JAN26F today and the fill rate collapsed. What was the exact cancel rate, reject rate, and fill rate for this symbol?",
    "Why did my order count for ICICIBANK and HDFCBANK drop so much on the NSE exchange, and which broker had the most rejections for these two tokens combined?",
    "Are there any volume spikes happening on NFO segments specifically for out-of-the-money options like NIFTY20JAN26C21000 right now?",
    "The feed volume for RELIANCE vs execution volume is misaligned. Can you check if there is an imbalance between buy and sell orders for RELIANCE-EQ, and also tell me how many orders broker ISB placed for it?",
    "Can you trace what happened to order 26011900049535 and also tell me if there was a general outage or failure on broker VFS around that time?"
]

print("Starting HARD testing suite...")
with open("test_results_detailed_hard.txt", "w", encoding="utf-8") as f:
    for q in queries:
        ask(q, f)
print("Finished testing! Results are in test_results_detailed_hard.txt")
