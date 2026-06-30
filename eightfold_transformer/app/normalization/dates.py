"""Date normalizer.

Normalize raw date strings to the canonical ``"YYYY-MM"`` granularity mandated by
the schema (see :class:`ExperienceItem`). Parsing is deterministic and offline.

Supported forms (case-insensitive, surrounding whitespace tolerated)
--------------------------------------------------------------------
- ``"Jan 2024"`` / ``"January 2024"``  -> ``"2024-01"``
- ``"2024"``        (year only)        -> ``"2024-01"``  (see design note)
- ``"2024-03"`` / ``"2024/03"``        -> ``"2024-03"``
- ``"03/2024"`` / ``"03-2024"``        -> ``"2024-03"``

Determinism
-----------
- No ``datetime.now()`` and no randomness anywhere.
- :func:`dateutil.parser.parse` is given an explicit fixed ``default`` of
  ``2000-01-01`` so that any field it cannot read (notably the day, and the
  month for a year-only input) is filled from that constant rather than from
  the current wall clock. The day is then discarded by the ``YYYY-MM`` format.

Design note: year-only -> "YYYY-01"
-----------------------------------
Mapping ``"2024"`` to ``"2024-01"`` technically *fabricates* a month, which is
in slight tension with the project-wide principle "unknown -> null, never
invented". We make this choice because the assignment explicitly requires
``"2024" -> "YYYY-MM"`` and the schema's date fields are constrained to the
``YYYY-MM`` shape, so a year-only value cannot be represented otherwise.
Alternatives considered and rejected for this layer: (a) return the bare
``"2024"`` (violates the ``YYYY-MM`` contract), or (b) return ``None`` (discards
a genuinely known year). Choosing month ``01`` is the conventional, deterministic
"start of year" convention; consumers that care can treat day/sub-year precision
as unknown.
"""

from __future__ import annotations

import datetime
import re

from dateutil import parser as _dateutil_parser

# Fixed anchor so dateutil never reads the wall clock to fill missing parts.
_FIXED_DEFAULT = datetime.datetime(2000, 1, 1)

# Fast, unambiguous explicit forms handled before falling back to dateutil.
_YEAR_ONLY = re.compile(r"^\s*(\d{4})\s*$")
_YEAR_MONTH = re.compile(r"^\s*(\d{4})[-/](\d{1,2})\s*$")  # 2024-03, 2024/3
_MONTH_YEAR = re.compile(r"^\s*(\d{1,2})[-/](\d{4})\s*$")  # 03/2024, 3-2024


def _format(year: int, month: int) -> str | None:
    """Return ``"YYYY-MM"`` if ``month`` is a valid 1..12 value, else ``None``."""
    if 1 <= month <= 12:
        return f"{year:04d}-{month:02d}"
    return None


def normalize_date(raw: str) -> str | None:
    """Normalize a raw date string to ``"YYYY-MM"``, or ``None`` if unparseable.

    Parameters
    ----------
    raw:
        Raw date text such as ``"Jan 2024"``, ``"January 2024"``, ``"2024"``,
        ``"2024-03"``, or ``"03/2024"``.

    Returns
    -------
    str | None
        Canonical ``"YYYY-MM"`` string on success; ``None`` for empty or
        unparseable input. Year-only input maps to month ``01`` (see module
        design note).

    Notes
    -----
    Deterministic and offline: no current-date defaults are ever used.

    Examples
    --------
    >>> normalize_date("Jan 2024")
    '2024-01'
    >>> normalize_date("2024")
    '2024-01'
    >>> normalize_date("03/2024")
    '2024-03'
    >>> normalize_date("not a date") is None
    True
    """
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None

    # 1. Year only -> month defaults to 01 (documented fabrication).
    m = _YEAR_ONLY.match(text)
    if m:
        return _format(int(m.group(1)), 1)

    # 2. YYYY-MM / YYYY/MM.
    m = _YEAR_MONTH.match(text)
    if m:
        return _format(int(m.group(1)), int(m.group(2)))

    # 3. MM/YYYY / MM-YYYY.
    m = _MONTH_YEAR.match(text)
    if m:
        return _format(int(m.group(2)), int(m.group(1)))

    # 4. Month-name forms ("Jan 2024", "January 2024") and other common shapes.
    #    A fixed default anchors any missing component deterministically.
    try:
        parsed = _dateutil_parser.parse(text, default=_FIXED_DEFAULT)
    except (ValueError, OverflowError, TypeError):
        return None

    return _format(parsed.year, parsed.month)
