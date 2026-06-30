"""Phone number normalizer.

Normalize raw phone strings to canonical E.164 format (e.g. ``"+14155552671"``)
using the deterministic :mod:`phonenumbers` library. No ML / no LLM / no network.

Design
------
- A bare national number (no leading ``+``) is parsed against an explicit
  ``default_region``. This assumption is made a *parameter* (rather than hidden
  in the function body) so the caller declares the region context and the
  behaviour is configurable and testable.
- A value that already carries an international ``+<country code>`` prefix is
  parsed as-is and the embedded country code wins over ``default_region``.
- Unknown / unparseable / invalid input returns ``None``. We never invent a
  value: "honestly-empty" beats "wrong-but-confident".
"""

from __future__ import annotations

import phonenumbers


def normalize_phone(raw: str, default_region: str = "IN") -> str | None:
    """Normalize a raw phone string to E.164, or ``None`` if not a valid number.

    Parameters
    ----------
    raw:
        The raw phone string from a source (e.g. ``"8492948175"``,
        ``"+91 8492948175"``, ``"(415) 555-2671"``). Leading/trailing whitespace
        is tolerated by the underlying parser.
    default_region:
        ISO-3166 alpha-2 region used to interpret a *national* number that has no
        explicit ``+<country code>`` prefix. Defaults to ``"IN"`` because the
        assignment's worked example is an Indian number (``"8492948175"`` ->
        ``"+918492948175"``); a US-context caller would pass ``"US"``. When the
        input already begins with ``+...`` the embedded country code takes
        precedence and this argument is effectively ignored.

    Returns
    -------
    str | None
        The number in E.164 format (e.g. ``"+918492948175"``) when parsing
        succeeds and the number is valid; otherwise ``None``.

    Notes
    -----
    Deterministic: identical inputs always yield identical output. No clock,
    randomness, or network access is involved.

    Examples
    --------
    >>> normalize_phone("8492948175")
    '+918492948175'
    >>> normalize_phone("+91 8492948175")
    '+918492948175'
    >>> normalize_phone("call me") is None
    True
    """
    if not isinstance(raw, str):
        return None
    if not raw.strip():
        return None

    try:
        # When `raw` starts with "+", phonenumbers ignores `default_region` and
        # uses the embedded country code; otherwise it interprets the national
        # number against `default_region`.
        parsed = phonenumbers.parse(raw, default_region)
    except phonenumbers.NumberParseException:
        return None

    if not phonenumbers.is_valid_number(parsed):
        return None

    return phonenumbers.format_number(
        parsed, phonenumbers.PhoneNumberFormat.E164
    )
