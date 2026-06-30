"""Deterministic confidence scoring for the merge stage.

This module is the single place that turns "where did a value come from and how
many sources agreed" into a number in ``[0, 1]``. It is intentionally a pure,
table-driven heuristic so identical inputs always yield byte-identical scores
(no ML / no LLM / no clock / no randomness).

Scoring model
-------------
A field's confidence blends three deterministic signals::

    field_confidence = clamp01(
        0.5 * source_trust       # how much we trust the contributing source(s)
      + 0.3 * agreement_ratio    # how many supplying sources back the winner
      + 0.2 * method_reliability # how the value was extracted
    )

* **source_trust** - mean trust of the sources that supported the *winning*
  value, from :data:`SOURCE_TRUST` (a documented per-source table). Structured
  recruiter exports are trusted more than free-text recruiter notes.
* **agreement_ratio** - ``#sources supporting the winner / #sources that
  supplied any value for the field``. A value corroborated by every source that
  spoke gets ``1.0``; a contested value (e.g. 1 of 2 sources) is penalised, so
  disagreement *lowers* the field's confidence exactly as required.
* **method_reliability** - from :data:`METHOD_RELIABILITY`: a value read from a
  structured column is more reliable than one scraped from prose.

The weights ``(0.5, 0.3, 0.2)`` sum to 1.0 so the raw blend already lives in
``[0, 1]``; :func:`clamp01` is applied defensively regardless.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids runtime import cycle
    from eightfold_transformer.app.models.schema import CanonicalCandidate

__all__ = [
    "SOURCE_TRUST",
    "DEFAULT_SOURCE_TRUST",
    "METHOD_RELIABILITY",
    "DEFAULT_METHOD_RELIABILITY",
    "clamp01",
    "source_trust",
    "method_reliability",
    "field_confidence",
    "overall_confidence",
]

#: Per-source trust table. Higher = more authoritative. Values are deliberately
#: hand-picked and documented rather than learned:
#:   - ``ats_json``        0.85  applicant-tracking export: structured + curated.
#:   - ``recruiter_csv``   0.80  structured spreadsheet columns.
#:   - ``github_api``      0.70  authoritative for handles/links, but partial.
#:   - ``resume``          0.60  self-reported, parsed from a document.
#:   - ``recruiter_notes`` 0.50  free-text human notes, easiest to get wrong.
#: Unknown sources fall back to :data:`DEFAULT_SOURCE_TRUST`.
SOURCE_TRUST: dict[str, float] = {
    "ats_json": 0.85,
    "recruiter_csv": 0.80,
    "github_api": 0.70,
    "github": 0.70,
    "resume": 0.60,
    "recruiter_notes": 0.50,
}

#: Trust assumed for any source not listed in :data:`SOURCE_TRUST`.
DEFAULT_SOURCE_TRUST: float = 0.50

#: Per-extraction-method reliability. Mirrors the schema's ``ExtractionMethod``
#: literal set. ``derived`` is pipeline-computed and fully deterministic, so it
#: is treated as maximally reliable alongside ``structured``.
METHOD_RELIABILITY: dict[str, float] = {
    "structured": 1.0,
    "derived": 1.0,
    "regex": 0.8,
    "free-text": 0.5,
}

#: Reliability assumed for an unrecognised method string.
DEFAULT_METHOD_RELIABILITY: float = 0.5


def clamp01(value: float) -> float:
    """Clamp ``value`` into the closed interval ``[0.0, 1.0]``."""
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return float(value)


def source_trust(source: str) -> float:
    """Return the documented trust for ``source`` (or the default)."""
    return SOURCE_TRUST.get(source, DEFAULT_SOURCE_TRUST)


def method_reliability(method: str) -> float:
    """Return the documented reliability for an extraction ``method``."""
    return METHOD_RELIABILITY.get(method, DEFAULT_METHOD_RELIABILITY)


def field_confidence(
    winner_sources: Iterable[str],
    supplying_sources: Iterable[str],
    method: str,
) -> float:
    """Score a single resolved field in ``[0, 1]``.

    Parameters
    ----------
    winner_sources:
        Distinct sources that supported the *winning* value.
    supplying_sources:
        Distinct sources that supplied *any* value for the field (the
        denominator of the agreement ratio). Must be a superset of
        ``winner_sources``.
    method:
        The extraction method of the winning value (``structured`` / ``regex`` /
        ``free-text`` / ``derived``).

    Returns
    -------
    float
        ``clamp01(0.5*trust + 0.3*agreement + 0.2*method_reliability)``.
        Returns ``0.0`` when no source supplied the field.
    """
    winners = sorted(set(winner_sources))
    supplying = sorted(set(supplying_sources) | set(winners))
    if not supplying:
        return 0.0

    trust = sum(source_trust(s) for s in winners) / len(winners) if winners else 0.0
    agreement = len(winners) / len(supplying)
    blended = 0.5 * trust + 0.3 * agreement + 0.2 * method_reliability(method)
    return clamp01(blended)


def overall_confidence(candidate: "CanonicalCandidate") -> float:
    """Aggregate the core identity fields' confidences into one record score.

    The aggregate is the deterministic mean of the per-field confidences of the
    *present* core identity fields: ``full_name``, the first email, the first
    phone, and ``location.country``. Absent fields are skipped (they do not drag
    the mean toward zero); if none are present the record scores ``0.0``.

    Pure function: depends only on the candidate's already-computed per-field
    confidences, so it is fully reproducible.
    """
    confidences: list[float] = []

    if candidate.full_name is not None:
        confidences.append(candidate.full_name.confidence)
    if candidate.emails:
        confidences.append(candidate.emails[0].confidence)
    if candidate.phones:
        confidences.append(candidate.phones[0].confidence)
    if candidate.location.country is not None:
        confidences.append(candidate.location.country.confidence)

    if not confidences:
        return 0.0
    return clamp01(sum(confidences) / len(confidences))
