import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from elasticsearch import Elasticsearch, AsyncElasticsearch
from typing import Any
from datetime import datetime

from infra_rag.config import settings
from infra_rag.resilience import CircuitBreaker


_es_breaker = CircuitBreaker(
    "elasticsearch",
    failure_threshold=settings.elasticsearch.circuit_breaker_failures,
    reset_timeout_s=settings.elasticsearch.circuit_breaker_reset_s,
)


class ElasticsearchClient:
    def __init__(self):
        self._client: Elasticsearch | None = None
        self._async_client: AsyncElasticsearch | None = None

    @property
    def client(self) -> Elasticsearch:
        if self._client is None:
            self._client = Elasticsearch(
                hosts=[settings.elasticsearch.host],
                basic_auth=(
                    settings.elasticsearch.username,
                    settings.elasticsearch.password,
                ),
                verify_certs=settings.elasticsearch.verify_certs,
                request_timeout=settings.elasticsearch.request_timeout_s,
                max_retries=settings.elasticsearch.max_retries,
                retry_on_timeout=settings.elasticsearch.retry_on_timeout,
            )
        return self._client

    @property
    def async_client(self) -> AsyncElasticsearch:
        if self._async_client is None:
            self._async_client = AsyncElasticsearch(
                hosts=[settings.elasticsearch.host],
                basic_auth=(
                    settings.elasticsearch.username,
                    settings.elasticsearch.password,
                ),
                verify_certs=settings.elasticsearch.verify_certs,
                request_timeout=settings.elasticsearch.request_timeout_s,
                max_retries=settings.elasticsearch.max_retries,
                retry_on_timeout=settings.elasticsearch.retry_on_timeout,
            )
        return self._async_client

    def execute_esql(self, query: str, time_window: dict | None = None) -> dict[str, Any]:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Executing ES|QL:\n{query}")
        if not _es_breaker.allow():
            raise RuntimeError("Elasticsearch circuit breaker is open")
        
        try:
            response = self.client.esql.query(body={"query": query})
            res_body = response.body
            
            rows = res_body.get("values", [])
            logger.info(f"ES|QL Result rows: {len(rows)}")
            _es_breaker.record_success()
            return res_body
        except Exception as e:
            logger.error(f"ES|QL Query Failed: {query}")
            logger.error(f"Error: {e}")
            _es_breaker.record_failure()
            raise

    def search(self, index: str, query: dict, size: int | None = None) -> dict[str, Any]:
        if not _es_breaker.allow():
            raise RuntimeError("Elasticsearch circuit breaker is open")
        try:
            kwargs = {"index": index, "body": query}
            if size is not None:
                kwargs["size"] = size
            response = self.client.search(**kwargs)
            _es_breaker.record_success()
            return response.body
        except Exception:
            _es_breaker.record_failure()
            raise

    def aggregate(self, index: str, aggs: dict, time_range: dict | None = None) -> dict[str, Any]:
        if not _es_breaker.allow():
            raise RuntimeError("Elasticsearch circuit breaker is open")
        body = {"size": 0, "aggs": aggs}
        if time_range:
            body["query"] = {
                "bool": {
                    "filter": [
                        {"range": {"@timestamp": {"gte": time_range["start"], "lte": time_range["end"]}}}
                    ]
                }
            }
        
        try:
            response = self.client.search(index=index, body=body)
            _es_breaker.record_success()
            return response.body
        except Exception:
            _es_breaker.record_failure()
            raise

    def get_index(self, name: str) -> str:
        """Get an infrastructure index name by key (metrics, logs, traces, alerts, network, blackbox)."""
        return getattr(settings.elasticsearch, f"{name}_index", f"infra-{name}")

    def close(self):
        if self._client:
            self._client.close()
        if self._async_client:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop and loop.is_running():
                loop.create_task(self._async_client.close())
            else:
                asyncio.run(self._async_client.close())


es_client = ElasticsearchClient()
