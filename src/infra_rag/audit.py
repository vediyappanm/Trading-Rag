import json
from datetime import datetime, timezone

from infra_rag.clients.redis import redis_client


def emit_audit_event(event: dict) -> None:
    try:
        payload = dict(event)
        payload["timestamp"] = datetime.now(timezone.utc).isoformat()
        redis_client.client.xadd("infra-rag-audit", payload)
    except Exception:
        return
