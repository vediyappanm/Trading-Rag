import requests
import json
import sys

url = "http://127.0.0.1:8000/ask/stream"
payload = {"query": "Compare the error rate of AAPL vs TSLA over the last day."}

print(f"Streaming from {url}...")
try:
    with requests.post(url, json=payload, stream=True) as r:
        for line in r.iter_lines():
            if line:
                decoded_line = line.decode('utf-8')
                print(f"LINE LEN: {len(decoded_line)}")
                if decoded_line.startswith("data: "):
                    content = decoded_line[6:]
                    try:
                        data = json.loads(content)
                        print(f"  [OK] Keys: {list(data.keys())}")
                        if data.get('analysis') and isinstance(data['analysis'], dict):
                            print(f"  [ANALYSIS] Answer length: {len(data['analysis'].get('answer', ''))}")
                        if data.get('final_response') and isinstance(data['final_response'], dict):
                            print(f"  [FINAL] Answer length: {len(data['final_response'].get('answer', ''))}")
                    except json.JSONDecodeError as e:
                        print(f"  [ERROR] JSON PARSE FATAL: {e}")
                        print(f"  TRUNCATED CONTENT: {content[:100]}... [LEN: {len(content)}]")
                        with open("failing_json.txt", "w") as f:
                            f.write(content)
                    except Exception as e:
                        print(f"  [ERROR] OTHER ERROR: {e}")
except Exception as e:
    print(f"Error: {e}")
