"""Company name normalizer.

Deterministic canonicalization for employer names: strip legal suffixes, compare
case-insensitively, and collapse near-duplicates via stdlib ``difflib``.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

__all__ = ["normalize_company", "company_match_key", "companies_equivalent"]

_WS = re.compile(r"\s+")
_SUFFIX = re.compile(
    r"(?:[,.\s]+|\b)(?:incorporated|corporation|corp|inc|llc|ltd|limited|co|company)\.?\s*$",
    re.IGNORECASE,
)


def company_match_key(raw: str) -> str:
    """Return a normalized comparison key (lowercase, suffix-stripped)."""
    text = _WS.sub(" ", raw.strip())
    if not text:
        return ""
    text = _SUFFIX.sub("", text).strip(" ,.")
    return _WS.sub(" ", text.lower()).strip()


def normalize_company(raw: str) -> str:
    """Canonicalize a company name for storage and matching."""
    if not isinstance(raw, str):
        return raw
    cleaned = _WS.sub(" ", raw.strip())
    if not cleaned:
        return cleaned

    core = _SUFFIX.sub("", cleaned).strip(" ,.")
    if not core:
        return cleaned

    lowered = cleaned.lower()
    if re.search(r"\b(corp|co|corporation|company)\b", lowered):
        return f"{core} Corporation"
    if re.search(r"\binc\b", lowered):
        return f"{core} Inc"
    if re.search(r"\bllc\b", lowered):
        return f"{core} LLC"
    if re.search(r"\b(ltd|limited)\b", lowered):
        return f"{core} Ltd"
    return core


def companies_equivalent(a: str, b: str, *, threshold: float = 0.92) -> bool:
    """True when two company strings refer to the same employer."""
    ka, kb = company_match_key(a), company_match_key(b)
    if not ka or not kb:
        return False
    if ka == kb:
        return True
    return SequenceMatcher(None, ka, kb).ratio() >= threshold
