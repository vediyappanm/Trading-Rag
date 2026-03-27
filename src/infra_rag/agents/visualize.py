import json
import logging
from typing import Any

from infra_rag.config import settings
from infra_rag.clients.llm import get_llm
from infra_rag.clients.grafana import grafana_client
from infra_rag.models import DataSource

logger = logging.getLogger(__name__)

VISUALIZE_PROMPT = """You are an expert Grafana Dashboard architect.
Your task is to generate a valid Grafana Dashboard JSON based on the user's visualization request.

USER REQUEST: {query}
TARGET: {target}
DATA SOURCE: {source}

REQUIREMENTS:
1. Return ONLY a valid JSON object representing a Grafana Dashboard.
2. The dashboard should have a clear title and at least 2-3 relevant panels.
3. Use 'node_exporter' or 'prometheus' standard metrics if source is PROMETHEUS.
4. If TARGET is provided, use it in the metrics filter (e.g., {{instance=~"{target}.*"}}).
5. Set the time range to 'now-1h' to 'now' by default.
6. Ensure each panel has a unique 'id' (starting from 1).
7. Use 'graph' or 'timeseries' panel types for metrics.
8. Include a 'refresh': '5s' setting.

SCHEMA HINT:
{{
  "title": "...",
  "panels": [
    {{
      "title": "Panel Title",
      "type": "timeseries",
      "gridPos": {{"h": 8, "w": 12, "x": 0, "y": 0}},
      "targets": [
        {{ "expr": "promql_expression", "refId": "A" }}
      ]
    }}
  ],
  "schemaVersion": 36,
  "refresh": "5s"
}}

RESPONSE MUST BE ONLY THE JSON. NO OTHER TEXT.
"""

async def create_adhoc_visualization(query: str, target: str | None = None, source: str = "Prometheus") -> dict[str, Any] | None:
    """Generate a Grafana dashboard JSON via LLM and create it in Grafana."""
    llm = get_llm(model_type="analysis") # Use the stronger model for JSON generation
    
    prompt = VISUALIZE_PROMPT.format(query=query, target=target or "all", source=source)
    
    try:
        # 1. Generate JSON with LLM
        response = await llm.ainvoke(prompt)
        raw_json = response.content.strip()
        
        # Strip markdown code blocks if present
        if raw_json.startswith("```json"):
            raw_json = raw_json[7:-3].strip()
        elif raw_json.startswith("```"):
            raw_json = raw_json[3:-3].strip()
            
        dashboard_json = json.loads(raw_json)
        
        # 2. Add some defaults if missing
        dashboard_json.setdefault("title", f"Ad-hoc: {query[:30]}")
        dashboard_json.setdefault("tags", ["infra-rag", "ad-hoc"])
        dashboard_json.setdefault("timezone", "browser")
        
        # 3. Create in Grafana
        result = grafana_client.create_dashboard(dashboard_json)
        logger.info(f"Created ad-hoc dashboard: {result.get('url')}")
        
        return result
        
    except Exception as e:
        logger.error(f"Failed to create ad-hoc visualization: {e}")
        return None
