"""Regex-based local redaction for common transcript PII and credentials."""

import re


_PASSWORD_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:password|passwd|pwd|passcode|passphrase)\b\s*(?::|=|\bis\b)\s*)"
    r'("(?:\\.|[^"\\])*"|\'(?:\\.|[^\'\\])*\'|[^\s,;]+)'
)
_API_KEY_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[ _-]?key|access[ _-]?token|secret[ _-]?key|client[ _-]?secret)\b"
    r"\s*(?::|=|\bis\b)\s*)([^\s,;]+)"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")
_KNOWN_API_TOKEN = re.compile(
    r"(?<![A-Za-z0-9])(?:"
    r"sk-[A-Za-z0-9_-]{16,}|"
    r"AIza[0-9A-Za-z_-]{30,}|"
    r"gh[pousr]_[A-Za-z0-9_]{20,}|"
    r"github_pat_[A-Za-z0-9_]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}"
    r")(?![A-Za-z0-9])"
)
_CREDIT_CARD_CANDIDATE = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_PHONE_NUMBER = re.compile(
    r"(?<![\w])"
    r"(?:\+?\d{1,3}[ .-]?)?"
    r"(?:\(?\d{2,4}\)?[ .-]?)"
    r"\d{3}[ .-]?\d{4}"
    r"(?:\s*(?:x|ext\.?)\s*\d{1,6})?"
    r"(?!\w)",
    re.IGNORECASE,
)


def _redact_assignment(match: re.Match[str], tag: str) -> str:
    """Keep the credential label while replacing its assigned value."""
    return f"{match.group(1)}[{tag}]"


def _passes_luhn(number: str) -> bool:
    """Return whether a digit-only card candidate passes the Luhn checksum."""
    digits = [int(character) for character in number]
    if not 13 <= len(digits) <= 19:
        return False

    checksum = 0
    parity = len(digits) % 2
    for index, digit in enumerate(digits):
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        checksum += digit
    return checksum % 10 == 0


def _redact_credit_card(match: re.Match[str]) -> str:
    candidate = match.group(0)
    digits = re.sub(r"\D", "", candidate)
    return "[REDACTED_CREDIT_CARD]" if _passes_luhn(digits) else candidate


def redact_pii(text: str) -> str:
    """Replace common phone numbers, payment cards, API keys, and passwords.

    Redaction runs locally and preserves non-sensitive text. It is designed for
    common textual formats; it is not a substitute for a data-loss-prevention
    scanner or human review of high-risk material.
    """
    if not isinstance(text, str):
        raise TypeError("text must be a string")

    redacted = _PASSWORD_ASSIGNMENT.sub(
        lambda match: _redact_assignment(match, "REDACTED_PASSWORD"), text
    )
    redacted = _API_KEY_ASSIGNMENT.sub(
        lambda match: _redact_assignment(match, "REDACTED_API_KEY"), redacted
    )
    redacted = _BEARER_TOKEN.sub("[REDACTED_API_KEY]", redacted)
    redacted = _KNOWN_API_TOKEN.sub("[REDACTED_API_KEY]", redacted)
    redacted = _CREDIT_CARD_CANDIDATE.sub(_redact_credit_card, redacted)
    redacted = _PHONE_NUMBER.sub("[REDACTED_PHONE]", redacted)
    return redacted
