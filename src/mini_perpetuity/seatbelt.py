"""Ingest seatbelt (plan §3): reject text that looks like secrets or an SSN.

Not a guarantee: a regex list misses things and can reject harmless text.
"""

import re

PATTERNS = {
    "SSN-shaped number": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS access key id": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "credential assignment": re.compile(
        r"(?i)\b(?:password|passwd|secret|api[_-]?key|token)\s*[:=]\s*\S{6,}"
    ),
}


class Rejected(Exception):
    pass


def check_text(*texts: str | None) -> None:
    """Raise Rejected naming the first pattern found (never echoing the matched text)."""
    for text in texts:
        for name, pat in PATTERNS.items():
            if text and pat.search(text):
                raise Rejected(f"input rejected by ingest seatbelt ({name} pattern)")
