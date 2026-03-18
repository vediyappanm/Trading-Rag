python -m trading_rag.main

curl -k -u elastic:kNWuqeumJPWRDiWVF-Ak "https://localhost:9200/_cat/indices?v"


curl -k -u elastic:kNWuqeumJPWRDiWVF-Ak "https://localhost:9200/trading-execution-logs/_search?pretty&size=5"




pip install apscheduler
python -m trading_rag.ingest.poller --file /home/admin/Journal.log.txt --interval 5



# Kill the old server first (find its PID)
netstat -ano | findstr :8000
taskkill /PID <PID> /F

# Start fresh
cd "c:\Users\ELCOT\OneDrive\Desktop\Rag"
python -m uvicorn trading_rag.main:app --host 0.0.0.0 --port 8000
