"""Location / country normalizer.

Normalize raw country strings to ISO-3166 alpha-2 codes (e.g. ``"US"``) via a
deterministic lookup. No ML / no LLM / no network.

Scope
-----
This is a small *starter* gazetteer covering common spellings, a few synonyms,
and pass-through of already-correct alpha-2 codes. It is intentionally not an
exhaustive ISO-3166 table; unmapped input returns ``None`` (unknown -> null,
never invented) so the schema's ``country`` validator (``^[A-Z]{2}$``) is only
ever fed valid codes. Free-form "city, region, country" splitting is a separate
concern handled upstream and is out of scope for this function.
"""

from __future__ import annotations

#: alias (lowercased) -> ISO-3166 alpha-2. Seed set; grow as needed.
COUNTRY_ALIASES: dict[str, str] = {
    "us": "US",
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "united states": "US",
    "united states of america": "US",
    "america": "US",
    "in": "IN",
    "india": "IN",
    "uk": "GB",
    "u.k.": "GB",
    "united kingdom": "GB",
    "great britain": "GB",
    "gb": "GB",
    "ca": "CA",
    "canada": "CA",
    "de": "DE",
    "germany": "DE",
    "fr": "FR",
    "france": "FR",
    "au": "AU",
    "australia": "AU",
}


def normalize_country(raw: str) -> str | None:
    """Normalize a raw country string to an ISO-3166 alpha-2 code.

    Parameters
    ----------
    raw:
        Raw country text, e.g. ``"United States"``, ``"usa"``, ``"India"``,
        ``"UK"``, or an already-correct code like ``"US"``.

    Returns
    -------
    str | None
        The alpha-2 code (uppercase) on a known match; otherwise ``None``.

    Examples
    --------
    >>> normalize_country("United States")
    'US'
    >>> normalize_country("india")
    'IN'
    >>> normalize_country("Atlantis") is None
    True
    """
    if not isinstance(raw, str):
        return None
    key = " ".join(raw.strip().lower().split())
    if not key:
        return None
    return COUNTRY_ALIASES.get(key)
