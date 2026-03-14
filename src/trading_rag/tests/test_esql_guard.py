from trading_rag.esql_guard import ESQLGuard, ESQLValidationError


class DummyClient:
    def __init__(self):
        self.client = self

    def field_caps(self, index: str, fields: str):
        return {
            "fields": {
                "status_description": {"text": {"searchable": True}},
                "symbol": {"keyword": {"searchable": True}},
            }
        }


def test_esql_guard_rewrites_text_fields_and_injects_limit():
    guard = ESQLGuard(DummyClient())
    query = 'FROM "trading-execution-logs"\n| WHERE status_description == "Rejected"'
    patched, warnings = guard.validate_and_patch(query)
    assert "MATCH(status_description" in patched
    assert "LIMIT" in patched
    assert warnings
