import re


EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
# Only match phone numbers with non-digit separators (dashes, spaces, dots, parens).
# Pure digit sequences must not be redacted — they may be IDs used in queries.
PHONE_RE = re.compile(
    r"\b(?:\+?\d{1,3}[-.\s])(?:\(?\d{2,4}\)?[-.\s])?\d{3,4}[-.\s]\d{4}\b"
)


def redact_pii(text: str) -> str:
    if not text:
        return text
    redacted = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    redacted = PHONE_RE.sub("[REDACTED_PHONE]", redacted)
    return redacted
