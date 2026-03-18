import re


EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
# Only match phone numbers that contain non-digit separators (dashes, spaces, dots, parens).
# Pure digit sequences like Noren order IDs (e.g. 26011900049185) must NOT be redacted —
# they are queried by value and stripping them breaks order drilldown queries entirely.
PHONE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[-.\s])(?:\(?\d{2,4}\)?[-.\s])?\d{3,4}[-.\s]\d{4}\b"
)
# ACCOUNT_RE removed — a "\b\d{12,19}\b" pattern matches every Noren order ID (14 digits)
# and strips them from queries before they reach the router, breaking order lookups.


def redact_pii(text: str) -> str:
    if not text:
        return text
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    return redacted
