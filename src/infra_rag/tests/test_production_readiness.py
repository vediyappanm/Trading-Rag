import base64
from datetime import datetime, timedelta

from fastapi.testclient import TestClient

from infra_rag.analysis.agent import generate_analysis
from infra_rag.evaluation.agent import evaluate_response
from infra_rag.main import app
from infra_rag.models import (
    BaselineStats,
    DataSource,
    LogEntry,
    QueryDomain,
    QueryPath,
    QueryType,
    RetrievedEvidence,
)
from infra_rag.router.agent import route_query


def _basic_auth(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    return {"Authorization": f"Basic {token}"}


def test_route_query_for_core_ops_prompts():
    alerts = route_query("What alerts are currently firing?")
    assert alerts.domain == QueryDomain.INFRA_ALERTS
    assert alerts.query_type == QueryType.ALERT_ACTIVE
    assert alerts.query_path == QueryPath.PROMETHEUS_LIVE

    overview = route_query("Show me CPU and memory usage across all hosts")
    assert overview.domain == QueryDomain.INFRA_METRICS
    assert overview.query_type == QueryType.EXPLORATORY
    assert overview.query_path == QueryPath.PROMETHEUS_LIVE

    errors = route_query("Show me all error logs from the last hour")
    assert errors.domain == QueryDomain.INFRA_LOGS
    assert errors.query_type == QueryType.ERROR_SEARCH
    assert errors.query_path == QueryPath.LOG_SEARCH

    host = route_query("Investigate CPU and memory on 10.10.1.55")
    assert host.target == "10.10.1.55"
    assert host.query_path == QueryPath.PROMETHEUS_LIVE


def test_evaluation_does_not_abstain_with_aggregated_live_evidence():
    evidence = RetrievedEvidence(
        logs=[],
        aggregations={
            "by_group": {
                "10.10.0.101": {"cpu": 29.7, "memory": 32.7},
                "10.10.1.55": {"cpu": 15.0, "memory": 95.7},
            },
            "group_field": "instance",
        },
        query_used="promql: multi overview",
        path=QueryPath.PROMETHEUS_LIVE,
        domain=QueryDomain.INFRA_METRICS,
        data_source=DataSource.PROMETHEUS,
    )
    baseline = BaselineStats(hour=14, avg_cpu_pct=21.0, avg_memory_pct=46.0)
    answer = "Across all hosts, 10.10.1.55 has the highest memory usage at 95.7% while 10.10.0.101 is at 29.7% CPU."

    result = evaluate_response(answer, ["10.10.1.55", "10.10.0.101"], evidence, baseline)

    assert result.should_abstain is False
    assert result.groundedness_score >= 0.75
    assert result.correctness_score > 0


def test_analysis_fallback_stays_text_first_without_dashboard_links():
    evidence = RetrievedEvidence(
        logs=[
            LogEntry(
                id="log-1",
                timestamp=datetime.utcnow() - timedelta(minutes=5),
                message="database connection timeout",
                source="api-02",
                fields={"log.level": "ERROR"},
            )
        ],
        aggregations={},
        query_used="esql: errors",
        path=QueryPath.LOG_SEARCH,
        domain=QueryDomain.INFRA_LOGS,
        data_source=DataSource.ELASTICSEARCH,
        grafana_links=["https://grafana.local/explore"],
    )

    result = generate_analysis("Show me all error logs from the last hour", evidence, None)

    assert "Grafana" not in result.answer
    assert isinstance(result.answer, str)


def test_basic_auth_protects_api_and_health_stays_exempt(monkeypatch):
    monkeypatch.setattr("infra_rag.auth.settings.api.auth_enabled", True)
    monkeypatch.setattr("infra_rag.auth.settings.api.auth_username", "admin")
    monkeypatch.setattr("infra_rag.auth.settings.api.auth_password", "secret")

    client = TestClient(app)

    unauthorized = client.get("/metrics")
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "unauthorized"

    health = client.get("/health")
    assert health.status_code == 200

    authorized = client.get("/metrics", headers=_basic_auth("admin", "secret"))
    assert authorized.status_code == 200
    assert "metrics" in authorized.json()


def test_ask_returns_stable_json_error_envelope(monkeypatch):
    async def _boom(_query: str):
        raise RuntimeError("unexpected failure")

    monkeypatch.setattr("infra_rag.auth.settings.api.auth_enabled", False)
    monkeypatch.setattr("infra_rag.api.routes.run_workflow", _boom)

    client = TestClient(app)
    response = client.post("/ask", json={"query": "show me cpu"})

    assert response.status_code == 500
    payload = response.json()
    assert payload["error"]["code"] == "http_error"
    assert payload["error"]["message"] == "Internal server error"


def test_stream_returns_final_error_event_on_failure(monkeypatch):
    async def _broken_stream(_query: str):
        raise RuntimeError("rate limit from upstream")
        yield  # pragma: no cover

    monkeypatch.setattr("infra_rag.auth.settings.api.auth_enabled", False)
    monkeypatch.setattr("infra_rag.api.routes.astream_workflow", _broken_stream)

    client = TestClient(app)
    response = client.post("/ask/stream", json={"query": "show me cpu"})

    assert response.status_code == 200
    body = response.text
    assert '"type": "error"' in body
    assert '"code": "rate_limit"' in body
    assert '"final": true' in body
