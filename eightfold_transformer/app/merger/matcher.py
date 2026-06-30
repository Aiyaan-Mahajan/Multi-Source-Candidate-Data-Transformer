"""Deterministic identity matching / clustering for partial records.

Groups :class:`PartialRecord` objects that refer to the *same* real-world
candidate. Matching is rule-based and offline (no ML / no LLM); identical inputs
always produce identical clusters in a stable order.

Match-key priority (strongest first)
------------------------------------
1. **Exact email** - normalized (lowercased, trimmed) email equality. Two
   records that share any email are the same candidate. Strongest signal.
2. **GitHub / LinkedIn profile** - normalized profile identity (scheme dropped,
   ``host`` + ``path`` lowercased, trailing slash stripped). A shared profile is
   a near-unique identifier.
3. **Name similarity** - the *weakest* key. Two records match on name only when
   their normalized full names are highly similar
   (``difflib.SequenceMatcher`` ratio >= :data:`NAME_SIMILARITY_THRESHOLD`)
   **and** they carry no *conflicting* strong identifier (see
   :func:`_has_strong_conflict`). The conflict guard is what stops two different
   people who merely share a common name (but have distinct emails / profiles)
   from being over-merged on name alone.

Transitivity
------------
Matching is transitive within a single run: keys define edges of a graph over
records and clusters are its connected components, computed with union-find. So
``A~B`` (email) and ``B~C`` (github) yields one cluster ``{A, B, C}`` even if
``A`` and ``C`` share nothing directly.

Determinism
-----------
Clusters are returned ordered by the smallest input index they contain, and
records within a cluster keep their original input order.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional

from eightfold_transformer.app.models.partial import PartialRecord

__all__ = [
    "NAME_SIMILARITY_THRESHOLD",
    "NAME_BLOCK_PREFIX_LEN",
    "normalize_email_key",
    "normalize_profile_key",
    "normalize_name_key",
    "name_blocking_keys",
    "name_candidate_pairs",
    "name_similarity",
    "cluster",
]

#: Conservative similarity threshold for the *weakest* (name-only) match key.
#: Set high (0.90) on purpose: name similarity must not, by itself, merge
#: clearly different identities. Combined with the strong-conflict guard in
#: :func:`_has_strong_conflict`, this strongly biases toward *not* over-merging.
NAME_SIMILARITY_THRESHOLD: float = 0.90

#: Length of the leading-prefix blocking key derived from the
#: whitespace-stripped normalized name (see :func:`name_blocking_keys`).
NAME_BLOCK_PREFIX_LEN: int = 4

# Strip an optional URL scheme and a leading "www." so profile keys compare by
# host+path only. ``github.com/x`` and ``https://www.github.com/x/`` unify.
_SCHEME = re.compile(r"^[a-z][a-z0-9+.\-]*://")
_WWW = re.compile(r"^www\.")
_WS = re.compile(r"\s+")


def normalize_email_key(raw: Optional[str]) -> Optional[str]:
    """Return a comparable email key (lowercased, trimmed) or ``None``."""
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    return key or None


def normalize_profile_key(raw: Optional[str]) -> Optional[str]:
    """Return a comparable profile key from a raw URL/handle, or ``None``.

    Drops the scheme and a leading ``www.``, lowercases, and strips a trailing
    slash so that ``"github.com/priyasharma"``,
    ``"https://github.com/priyasharma"`` and
    ``"https://www.github.com/priyasharma/"`` all collapse to the same key.
    """
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    if not key:
        return None
    key = _SCHEME.sub("", key)
    key = _WWW.sub("", key)
    key = key.rstrip("/")
    return key or None


def normalize_name_key(raw: Optional[str]) -> Optional[str]:
    """Return a comparable name key (lowercased, whitespace-collapsed)."""
    if not isinstance(raw, str):
        return None
    key = _WS.sub(" ", raw.strip().lower())
    return key or None


def name_similarity(a: Optional[str], b: Optional[str]) -> float:
    """Deterministic name similarity in ``[0, 1]`` via ``SequenceMatcher``.

    Returns ``0.0`` if either name is missing. Exact normalized equality yields
    ``1.0``.
    """
    ka, kb = normalize_name_key(a), normalize_name_key(b)
    if not ka or not kb:
        return 0.0
    if ka == kb:
        return 1.0
    return SequenceMatcher(None, ka, kb).ratio()


def name_blocking_keys(name_key: Optional[str]) -> set[str]:
    """Return the set of blocking keys for an already-normalized name key.

    Blocking is the technique that turns the otherwise O(n^2) name-similarity
    pass into a near-linear one: instead of comparing every pair of records, we
    only compare pairs that land in a common *block*. To keep recall high (i.e.
    to avoid dropping a pair that *should* merge), each record is assigned
    *multiple* keys and joins a block for each. Two records are compared if they
    agree on **any** key, so a single coincidental key collision is enough to
    preserve a true match.

    Three complementary keys are derived from the normalized name; each is robust
    to a different kind of minor variation that still leaves similarity >= 0.90:

    * ``"last:<token>"`` - the final whitespace-delimited token (surname).
      Survives edits to the given name / middle initials.
    * ``"init:<sorted initials>"`` - the sorted multiset of every token's first
      character. Survives token reordering and whitespace changes.
    * ``"pre:<prefix>"`` - the first :data:`NAME_BLOCK_PREFIX_LEN` characters of
      the whitespace-stripped name. Survives trailing edits and the addition of
      extra inter-token whitespace (which normalization already collapses).

    Returns an empty set for a missing/blank name (such records can never match
    on the name key anyway, since :func:`name_similarity` returns ``0.0``).
    """
    if not name_key:
        return set()
    keys: set[str] = set()
    tokens = name_key.split()
    if tokens:
        keys.add("last:" + tokens[-1])
        initials = "".join(sorted(t[0] for t in tokens))
        keys.add("init:" + initials)
    compact = name_key.replace(" ", "")
    if compact:
        keys.add("pre:" + compact[:NAME_BLOCK_PREFIX_LEN])
    return keys


def name_candidate_pairs(partials: List[PartialRecord]) -> List[tuple[int, int]]:
    """Return the deduplicated, sorted ``(i, j)`` pairs (``i < j``) to compare.

    This is the heart of the blocking optimization. Records are bucketed into
    blocks by :func:`name_blocking_keys`; only *intra-block* pairs become
    candidates for the expensive :func:`name_similarity` check. A pair that
    shares several keys is emitted once (set dedupe), and the result is sorted so
    the downstream union order matches the original nested-loop order exactly.

    The returned size is the number of name comparisons that will be performed,
    which is what makes the blocking win observable/testable: for well-spread
    names it is far below ``n*(n-1)/2``.
    """
    blocks: dict[str, List[int]] = {}
    for i, record in enumerate(partials):
        name_key = normalize_name_key(_name_of(record))
        for block_key in name_blocking_keys(name_key):
            blocks.setdefault(block_key, []).append(i)

    pairs: set[tuple[int, int]] = set()
    for indices in blocks.values():
        # ``indices`` is ascending (appended in enumeration order); pair them up.
        m = len(indices)
        for a in range(m):
            ia = indices[a]
            for b in range(a + 1, m):
                pairs.add((ia, indices[b]))
    return sorted(pairs)


def _emails_of(record: PartialRecord) -> set[str]:
    keys = set()
    for tv in record.emails:
        key = normalize_email_key(tv.value)
        if key:
            keys.add(key)
    return keys


def _profiles_of(record: PartialRecord) -> set[str]:
    keys = set()
    for tv in (record.links.github, record.links.linkedin):
        if tv is not None:
            key = normalize_profile_key(tv.value)
            if key:
                keys.add(key)
    return keys


def _name_of(record: PartialRecord) -> Optional[str]:
    return record.full_name.value if record.full_name is not None else None


def _has_strong_conflict(a: PartialRecord, b: PartialRecord) -> bool:
    """True if ``a`` and ``b`` carry contradictory strong identifiers.

    Two records *conflict* when both expose a strong identifier of the same kind
    (email or github/linkedin profile) and those identifier sets are completely
    disjoint. Such a pair must never be merged on the weak name key alone: it is
    far more likely to be two different people who happen to share a name than a
    single person with two entirely different emails *and* no other link.
    """
    ea, eb = _emails_of(a), _emails_of(b)
    if ea and eb and ea.isdisjoint(eb):
        return True
    pa, pb = _profiles_of(a), _profiles_of(b)
    if pa and pb and pa.isdisjoint(pb):
        return True
    return False


class _UnionFind:
    """Minimal union-find (disjoint-set) over record indices."""

    def __init__(self, n: int) -> None:
        self._parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self._parent[root] != root:
            root = self._parent[root]
        # Path compression keeps repeated lookups cheap and stays deterministic.
        while self._parent[x] != root:
            self._parent[x], x = root, self._parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            # Always attach the larger root index under the smaller one so the
            # representative is stable regardless of union order.
            lo, hi = (ra, rb) if ra < rb else (rb, ra)
            self._parent[hi] = lo


def cluster(partials: List[PartialRecord]) -> List[List[PartialRecord]]:
    """Group partial records that refer to the same candidate.

    See the module docstring for the match-key priority, the conservative
    name-similarity threshold, transitivity, and the deterministic ordering
    contract.

    Parameters
    ----------
    partials:
        Per-source partial records (in any order).

    Returns
    -------
    list[list[PartialRecord]]
        Clusters of records. Clusters are ordered by the smallest input index
        they contain; records within a cluster preserve input order.
    """
    n = len(partials)
    if n == 0:
        return []

    uf = _UnionFind(n)

    # --- Strong keys (email, profile): bucket index lists by shared key. ---
    by_email: dict[str, List[int]] = {}
    by_profile: dict[str, List[int]] = {}
    for i, record in enumerate(partials):
        for key in _emails_of(record):
            by_email.setdefault(key, []).append(i)
        for key in _profiles_of(record):
            by_profile.setdefault(key, []).append(i)

    for buckets in (by_email, by_profile):
        for indices in buckets.values():
            first = indices[0]
            for other in indices[1:]:
                uf.union(first, other)

    # --- Weak key (name): only union pairs without a strong conflict. ---
    # Blocking turns the naive O(n^2) all-pairs scan into ~O(n + within-block
    # comparisons): records are bucketed by multiple name-derived keys
    # (:func:`name_blocking_keys`) and only pairs sharing a block are ever fed to
    # the expensive ``SequenceMatcher`` ratio. The multi-key scheme keeps recall
    # high (a true near-duplicate collides on at least one key), so the set of
    # pairs that actually union is unchanged versus the all-pairs scan. The same
    # guards still apply: the similarity threshold, the strong-conflict check,
    # and the same-component skip.
    for i, j in name_candidate_pairs(partials):
        if uf.find(i) == uf.find(j):
            continue
        if name_similarity(_name_of(partials[i]), _name_of(partials[j])) >= (
            NAME_SIMILARITY_THRESHOLD
        ):
            if not _has_strong_conflict(partials[i], partials[j]):
                uf.union(i, j)

    # --- Composite scoring: names, phones, companies, email variants, resume. ---
    from eightfold_transformer.app.merger.entity_resolution import (
        MATCH_THRESHOLD,
        STRONG_OVERRIDE_THRESHOLD,
        composite_candidate_pairs,
        composite_match_score,
        has_corroboration,
    )

    for i, j in composite_candidate_pairs(partials):
        if uf.find(i) == uf.find(j):
            continue
        score = composite_match_score(partials[i], partials[j])
        if score < MATCH_THRESHOLD:
            continue
        conflict = _has_strong_conflict(partials[i], partials[j])
        if conflict:
            if score < STRONG_OVERRIDE_THRESHOLD or not has_corroboration(
                partials[i], partials[j]
            ):
                continue
        uf.union(i, j)

    # --- Materialize components, preserving deterministic ordering. ---
    groups: dict[int, List[int]] = {}
    for i in range(n):
        groups.setdefault(uf.find(i), []).append(i)

    ordered_roots = sorted(groups, key=lambda root: min(groups[root]))
    return [[partials[i] for i in sorted(groups[root])] for root in ordered_roots]
