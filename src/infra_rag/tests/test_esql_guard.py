from infra_rag.esql_guard import ESQLGuard, ESQLValidationError


class DummyClient:
    def __init__(self):
        self.client = self

    def field_caps(self, index: str, fields: str):
        return {
            "fields": {
                "message": {"text": {"searchable": True}},
                "host.name": {"keyword": {"searchable": True}},
            }
        }


def test_esql_guard_rewrites_text_fields_and_injects_limit():
    guard = ESQLGuard(DummyClient())
    query = 'FROM "infra-logs"\n| WHERE message == "connection refused"'
    patched, warnings = guard.validate_and_patch(query)
    assert "MATCH(message" in patched
    assert "LIMIT" in patched
    assert warnings


def test_esql_guard_log_keep_does_not_include_metric_fields():
    guard = ESQLGuard(DummyClient())
    query = 'FROM "infra-logs"\n| WHERE log.level == "ERROR"\n| LIMIT 10'
    patched, _ = guard.validate_and_patch(query)
    assert "| KEEP" in patched
    assert "message" in patched
    assert "metric.name" not in patched
    assert "metric.unit" not in patched
