"""Flattened provenance ledger for a merged candidate.

The canonical record carries two views of provenance (see
:mod:`eightfold_transformer.app.models.schema`): the per-field
:class:`TrackedValue` carriers on winning values, and this *output-facing*
flattened audit trail of :class:`ProvenanceEntry` (``{field, source, method}``)
rows.

This module builds the flattened trail from **every contributing**
:class:`TrackedValue` across the whole cluster - not just the winners. That is
deliberate: provenance must answer "which sources spoke to this field?", so a
losing value's source (and even a source whose value was later dropped during
normalization, e.g. an unparseable phone) is still recorded. The ledger is
de-duplicated on the exact ``(field, source, method)`` tuple and sorted for a
deterministic, reproducible ordering.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Tuple

from eightfold_transformer.app.models.partial import PartialRecord
from eightfold_transformer.app.models.schema import ProvenanceEntry, TrackedValue

__all__ = ["build_provenance"]


def _entry(
    field: str, tv: Optional[TrackedValue]
) -> Optional[Tuple[str, str, str]]:
    """Map a tracked value to a ``(field, source, method)`` tuple, if present."""
    if tv is None:
        return None
    return (field, tv.source, tv.extraction_method)


def _iter_record(record: PartialRecord) -> Iterable[Tuple[str, str, str]]:
    """Yield every ``(field, source, method)`` contributed by one partial."""
    singles = (
        ("full_name", record.full_name),
        ("headline", record.headline),
        ("years_experience", record.years_experience),
        ("location.city", record.location.city),
        ("location.region", record.location.region),
        ("location.country", record.location.country),
        ("links.linkedin", record.links.linkedin),
        ("links.github", record.links.github),
        ("links.portfolio", record.links.portfolio),
    )
    for field, tv in singles:
        entry = _entry(field, tv)
        if entry is not None:
            yield entry

    list_fields = (
        ("emails", record.emails),
        ("phones", record.phones),
        ("skills", record.skills),
        ("links.other", record.links.other),
    )
    for field, values in list_fields:
        for tv in values:
            entry = _entry(field, tv)
            if entry is not None:
                yield entry

    for pe in record.experience:
        for field, tv in (
            ("experience.company", pe.company),
            ("experience.title", pe.title),
            ("experience.date_range", pe.date_range),
            ("experience.summary", pe.summary),
        ):
            entry = _entry(field, tv)
            if entry is not None:
                yield entry

    for ed in record.education:
        for field, tv in (
            ("education.institution", ed.institution),
            ("education.degree", ed.degree),
            ("education.field", ed.field),
            ("education.year", ed.year),
        ):
            entry = _entry(field, tv)
            if entry is not None:
                yield entry


def build_provenance(cluster: List[PartialRecord]) -> List[ProvenanceEntry]:
    """Build the deterministic flattened provenance trail for a cluster.

    Parameters
    ----------
    cluster:
        The matched partial records that form one candidate.

    Returns
    -------
    list[ProvenanceEntry]
        De-duplicated ``{field, source, method}`` rows, sorted by
        ``(field, source, method)`` for byte-identical output across runs.
    """
    seen: set[Tuple[str, str, str]] = set()
    for record in cluster:
        for entry in _iter_record(record):
            seen.add(entry)

    return [
        ProvenanceEntry(field=field, source=source, method=method)
        for field, source, method in sorted(seen)
    ]
