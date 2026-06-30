"""merger package: entity matching, dedupe, conflict resolution, confidence + provenance.

This package turns a flat list of per-source :class:`PartialRecord` objects into
deduplicated :class:`CanonicalCandidate` records. The pipeline is three
deterministic stages, each in its own module:

1. :mod:`matcher`    - cluster partials that refer to the same candidate
   (email > profile > conservative name similarity; transitive via union-find).
2. :mod:`resolver`   - resolve each canonical field from a cluster (confidence-
   first conflict ladder for scalars; union+dedupe for lists), routing raw
   values through the shared normalizers.
3. :mod:`confidence` / :mod:`provenance` - score per-field/record confidence and
   build the flattened ``{field, source, method}`` audit trail.

Public surface
--------------
``from eightfold_transformer.app.merger import merge, merge_cluster``
"""

from __future__ import annotations

import hashlib
from typing import List

from eightfold_transformer.app.models.partial import PartialRecord
from eightfold_transformer.app.models.schema import CanonicalCandidate, TrackedValue
from eightfold_transformer.app.normalization import normalize_phone
from eightfold_transformer.app.merger.confidence import overall_confidence
from eightfold_transformer.app.merger.matcher import (
    cluster,
    normalize_email_key,
    normalize_name_key,
)
from eightfold_transformer.app.merger.provenance import build_provenance
from eightfold_transformer.app.merger.resolver import resolve_cluster

__all__ = ["merge", "merge_cluster"]

#: Source identifier stamped on the derived ``candidate_id`` TrackedValue.
_DERIVED_SOURCE = "merger"
#: Length of the hex digest kept for the id (64 bits is ample for collisions
#: at this scale while staying compact and readable).
_ID_HEX_LEN = 16


def _candidate_id_seed(records: List[PartialRecord]) -> str:
    """Pick the strongest available match key as the id seed (deterministic).

    Priority mirrors the matcher: a normalized email is the strongest stable
    identifier, then an E.164 phone, then a normalized name. Within a tier the
    lexicographically smallest value is chosen so the seed is independent of
    input order. Falls back to the sorted source set if nothing identifying
    exists (an "empty-but-valid" cluster).
    """
    emails: set[str] = set()
    for record in records:
        for tv in record.emails:
            key = normalize_email_key(tv.value)
            if key:
                emails.add(key)
    if emails:
        return "email:" + min(emails)

    phones: set[str] = set()
    for record in records:
        for tv in record.phones:
            e164 = normalize_phone(tv.value)
            if e164:
                phones.add(e164)
    if phones:
        return "phone:" + min(phones)

    names: set[str] = set()
    for record in records:
        if record.full_name is not None:
            key = normalize_name_key(record.full_name.value)
            if key:
                names.add(key)
    if names:
        return "name:" + min(names)

    return "source:" + "|".join(sorted({record.source for record in records}))


def _build_candidate_id(records: List[PartialRecord]) -> TrackedValue:
    """Build the deterministic derived ``candidate_id`` carrier.

    Same inputs -> same id: it is a stable SHA-256 over the strongest match-key
    seed, so two runs (or two equivalent clusters) always yield byte-identical
    ids. Modeled as a ``TrackedValue[str]`` with ``extraction_method='derived'``.
    """
    seed = _candidate_id_seed(records)
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:_ID_HEX_LEN]
    return TrackedValue(
        value=f"cand_{digest}",
        source=_DERIVED_SOURCE,
        confidence=1.0,
        extraction_method="derived",
    )


def merge_cluster(cluster: List[PartialRecord]) -> CanonicalCandidate:
    """Merge one already-matched cluster into a single canonical candidate.

    Builds every canonical field via :func:`resolver.resolve_cluster`, derives a
    deterministic ``candidate_id``, attaches the flattened provenance trail, and
    computes the aggregate ``overall_confidence``.
    """
    if not cluster:
        raise ValueError("merge_cluster requires a non-empty cluster")

    fields = resolve_cluster(cluster)
    candidate = CanonicalCandidate(
        candidate_id=_build_candidate_id(cluster),
        provenance=build_provenance(cluster),
        **fields,
    )
    candidate.overall_confidence = overall_confidence(candidate)
    return candidate


def merge(partials: List[PartialRecord]) -> List[CanonicalCandidate]:
    """Cluster partial records and merge each cluster into a canonical candidate.

    Parameters
    ----------
    partials:
        Per-source partial records (any order).

    Returns
    -------
    list[CanonicalCandidate]
        One canonical candidate per detected identity, in the deterministic
        cluster order produced by :func:`matcher.cluster`.
    """
    return [merge_cluster(group) for group in cluster(partials)]
