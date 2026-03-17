# QuantSight Deployment & Implementation Plan

This document summarizes the full technical implementation and deployment strategy for the QuantSight (Trading RAG) system.

## 1. Core Implementation Status
- [x] **Rebranding**: System renamed from "Trading RAG" to **QuantSight**.
- [x] **Frontend Resilience**: Robust streaming JSON parser implemented in `index.html` to prevent corruption errors on large data sets.
- [x] **Backend Optimization**: Intelligent stream filtering implemented in `workflow.py` to strip out heavy raw logs while preserving citations.
- [x] **Visual Pipeline**: 5-step technical "Thinking" log implemented in the UI for transparency.

## 2. Infrastructure & Deployment
The application is containerized and deployed to the VPS at `173.249.2.23`.

### Containerization (`Dockerfile` & `docker-compose.yml`)
- The app runs in a Python 3.11 environment on port `8000` (internal).
- It is mapped to port **`8123`** on the host to avoid conflicts with existing services.

### CI/CD Pipeline (`.github/workflows/deploy.yml`)
- **Trigger**: Every push to the `main` branch.
- **Action**: 
  1. Pulls the latest code on the VPS.
  2. Rebuilds the Docker containers.
  3. Restart the application.

### Nginx Reverse Proxy
- **Domain**: `trading-rag.santhira.com`
- **Configuration**: Handles standard web traffic (Port 80) and forwards it correctly to the Docker container.
- **SSE Support**: Specifically configured to allow Server-Sent Events for real-time AI streaming.

## 3. How to Update / Deploy
1. Make changes to your local files (`index.html`, `src/`, etc.).
2. Commit and push:
   ```bash
   git add .
   git commit -m "Update message"
   git push origin main
   ```
3. The GitHub Action will handle the deployment automatically.

## 4. Current Web Access
- **Primary**: [http://trading-rag.santhira.com](http://trading-rag.santhira.com)
- **Direct IP**: [http://173.249.2.23:8123/](http://173.249.2.23:8123/)
