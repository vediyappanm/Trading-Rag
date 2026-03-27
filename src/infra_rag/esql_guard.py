import re
from typing import Iterable

from infra_rag.config import settings


class ESQLValidationError(Exception):
    pass


class ESQLGuard:
    """Mapping-aware ES|QL validator and rewriter."""

    _RESERVED = {
        "FROM", "WHERE", "AND", "OR", "NOT", "IN", "LIKE", "MATCH", "QSTR", "KQL",
        "EVAL", "STATS", "BY", "LIMIT", "SORT", "DESC", "ASC", "KEEP", "DROP",
        "COUNT", "SUM", "AVG", "MIN", "MAX", "PERCENTILE", "CASE", "WHEN", "THEN",
        "ELSE", "END", "AS", "IS", "NULL", "TRUE", "FALSE"
    }

    def __init__(self, es_client):
        self._es = es_client
        self._text_fields, self._keyword_fields = self._load_field_caps()
        self._text_only_fields = self._text_fields - self._keyword_fields

    def _load_field_caps(self) -> tuple[set[str], set[str]]:
        try:
            caps = self._es.client.field_caps(
                index=settings.elasticsearch.field_caps_index_pattern,
                fields="*",
            )
        except Exception:
            return set(), set()

        text_fields = set()
        keyword_fields = set()
        for field, info in caps.get("fields", {}).items():
            if "text" in info and info["text"].get("searchable"):
                text_fields.add(field)
            if "keyword" in info and info["keyword"].get("searchable"):
                keyword_fields.add(field)
        return text_fields, keyword_fields

    def validate_and_patch(self, query: str) -> tuple[str, list[str]]:
        warnings: list[str] = []
        patched = query.strip()

        if not patched.lower().startswith("from"):
            raise ESQLValidationError("ES|QL must start with FROM")

        if "LIMIT" not in patched.upper():
            patched += f"\n| LIMIT {settings.elasticsearch.esql_limit_default}"
            warnings.append("LIMIT injected")

        patched = self._rewrite_text_equals(patched, warnings)
        patched = self._rewrite_match_on_keyword(patched, warnings)

        if "| KEEP" not in patched.upper() and "STATS" not in patched.upper():
            keep_fields = ", ".join(self._valid_keep_fields(patched))
            if keep_fields:
                patched += f"\n| KEEP {keep_fields}"
                warnings.append("KEEP injected")

        conflicts = self._detect_type_conflicts(patched)
        if conflicts:
            raise ESQLValidationError(f"Cross-index type conflicts: {sorted(conflicts)}")

        return patched, warnings

    def _valid_keep_fields(self, query: str) -> list[str]:
        default_fields = self._default_keep_fields_for_index(self._index_from_query(query))
        if not self._keyword_fields and not self._text_fields:
            return default_fields
        fields = set(default_fields)
        all_fields = self._keyword_fields | self._text_fields
        return [f for f in fields if f in all_fields or f.startswith("@")]

    def _index_from_query(self, query: str) -> str:
        from_match = re.search(r'FROM\s+"([^"]+)"', query, re.IGNORECASE)
        return from_match.group(1) if from_match else settings.elasticsearch.field_caps_index_pattern

    def _default_keep_fields_for_index(self, index: str) -> list[str]:
        es = settings.elasticsearch

        if "logs" in index:
            return es.log_keep_fields
        if "metrics" in index:
            return es.metric_keep_fields
        if "traces" in index:
            return [
                "@timestamp", "service.name", "host.name", "source",
                "trace.id", "span.id", "operation.name", "duration_ms", "status.code",
            ]
        if "alerts" in index:
            return [
                "@timestamp", "host.name", "source", "message",
                "alertname", "severity", "state", "instance", "description", "summary",
            ]
        if "network" in index:
            return [
                "@timestamp", "host.name", "source", "service.name",
                "device.name", "interface.name", "interface.bytes_in", "interface.bytes_out",
                "device.cpu_pct", "device.memory_pct",
            ]
        if "blackbox" in index:
            return [
                "@timestamp", "host.name", "source",
                "probe.target", "probe.duration_ms", "probe.success", "probe.type",
            ]
        return es.log_keep_fields

    def _rewrite_text_equals(self, query: str, warnings: list[str]) -> str:
        patched = query
        for field in self._text_only_fields:
            # WHERE field == "value"
            pattern = re.compile(rf"(\bWHERE|\bAND)\s+{re.escape(field)}\s*(==|=)\s*\"([^\"]+)\"", re.IGNORECASE)

            def _repl(match: re.Match) -> str:
                prefix = match.group(1)
                value = match.group(3)
                warnings.append(f"Rewrote {field} equality to MATCH()")
                return f"{prefix} MATCH({field}, \"{value}\")"

            patched = pattern.sub(_repl, patched)
        return patched

    def _rewrite_match_on_keyword(self, query: str, warnings: list[str]) -> str:
        patched = query
        for field in self._keyword_fields:
            pattern = re.compile(rf"MATCH\(\s*{re.escape(field)}\s*,\s*\"([^\"]+)\"\s*\)", re.IGNORECASE)

            def _repl(match: re.Match) -> str:
                value = match.group(1)
                warnings.append(f"Rewrote MATCH({field}) to equality for keyword field")
                return f'{field} == "{value.upper()}"'

            patched = pattern.sub(_repl, patched)
        return patched

    def _detect_type_conflicts(self, query: str) -> set[str]:
        fields = self._extract_fields(query)
        if not fields:
            return set()
        # Use the specific index from the FROM clause, not the wildcard pattern.
        # The wildcard (infra-*) matches multiple indices which may have different
        # field types — using it causes false-positive conflicts.
        index = self._index_from_query(query)
        try:
            caps = self._es.client.field_caps(
                index=index,
                fields=",".join(sorted(fields)),
            )
        except Exception:
            return set()

        conflicts = set()
        for field, info in caps.get("fields", {}).items():
            if len(info.keys()) > 1:
                conflicts.add(field)
        return conflicts

    def _extract_fields(self, query: str) -> set[str]:
        tokens = re.findall(r"\b[a-zA-Z_][\w\.]*\b", query)
        fields = set()
        for token in tokens:
            upper = token.upper()
            if upper in self._RESERVED:
                continue
            if upper.startswith("_"):
                continue
            fields.add(token)
        return fields
