import requests

url = "http://localhost:8000/ask"
payload = {"query": "How many AAPL executions were there in the last 2 hours?"}

print("Sending request to RAG API...")
response = requests.post(url, json=payload)
print(f"Status Code: {response.status_code}")
print("Response Body:")
print(response.json())
