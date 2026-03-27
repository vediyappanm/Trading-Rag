"""Quick routing validation — tests that queries are classified correctly."""

import sys
import json

# Add project src to path
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent.parent / "src"))

from infra_rag.router.agent import route_query
from infra_rag.models import DataSource, QueryPath, QueryDomain


TEST_CASES = [
    {
        "query": "What is the CPU usage right now?",
        "expect_source": DataSource.PROMETHEUS,
        "expect_path": QueryPath.PROMETHEUS_LIVE,
        "expect_domain": QueryDomain.INFRA_METRICS,
    },
    {
        "query": "Show me all error logs from the last hour",
        "expect_source": DataSource.ELASTICSEARCH,
        "expect_path": QueryPath.LOG_SEARCH,
        "expect_domain": QueryDomain.INFRA_LOGS,
    },
    {
        "query": "What alerts are currently firing?",
        "expect_source": DataSource.PROMETHEUS,
        "expect_path": QueryPath.PROMETHEUS_LIVE,
        "expect_domain": QueryDomain.INFRA_ALERTS,
    },
    {
        "query": "Show me the Grafana dashboard for web-01",
        "expect_source": DataSource.GRAFANA,
        "expect_path": QueryPath.GRAFANA_DASHBOARD,
        "expect_domain": QueryDomain.INFRA_METRICS,
    },
    {
        "query": "Why is the API server down?",
        "expect_source": DataSource.MULTI,
        "expect_path": QueryPath.CROSS_INDEX,
        "expect_domain": QueryDomain.CROSS_DOMAIN,
    },
    {
        "query": "Show me memory usage on web-01",
        "expect_source": DataSource.ELASTICSEARCH,
        "expect_domain": QueryDomain.INFRA_METRICS,
    },
    {
        "query": "What's the current memory on web-01?",
        "expect_source": DataSource.PROMETHEUS,
        "expect_path": QueryPath.PROMETHEUS_LIVE,
    },
    {
        "query": "Show me disk usage across all hosts",
        "expect_source": DataSource.ELASTICSEARCH,
        "expect_domain": QueryDomain.INFRA_METRICS,
    },
    {
        "query": "Show me network throughput for switch-01",
        "expect_source": DataSource.ELASTICSEARCH,
        "expect_domain": QueryDomain.INFRA_NETWORK,
    },
    {
        "query": "Investigate high latency on order-service",
        "expect_source": DataSource.MULTI,
        "expect_domain": QueryDomain.CROSS_DOMAIN,
    },
]


def main():
    passed = 0
    failed = 0
    results = []

    for tc in TEST_CASES:
        query = tc["query"]
        result = route_query(query)

        errors = []
        if "expect_source" in tc and result.data_source != tc["expect_source"]:
            errors.append(f"source: got {result.data_source.value}, expected {tc['expect_source'].value}")
        if "expect_path" in tc and result.query_path != tc["expect_path"]:
            errors.append(f"path: got {result.query_path.value}, expected {tc['expect_path'].value}")
        if "expect_domain" in tc and result.domain != tc["expect_domain"]:
            errors.append(f"domain: got {result.domain.value}, expected {tc['expect_domain'].value}")

        status = "PASS" if not errors else "FAIL"
        if errors:
            failed += 1
        else:
            passed += 1

        results.append({
            "query": query,
            "status": status,
            "errors": errors,
            "actual": {
                "domain": result.domain.value,
                "source": result.data_source.value,
                "path": result.query_path.value,
                "target": result.target,
            },
        })

        icon = "✓" if status == "PASS" else "✗"
        print(f"  {icon} {query}")
        if errors:
            for e in errors:
                print(f"    → {e}")

    print(f"\n{passed}/{passed+failed} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
