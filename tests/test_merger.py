"""Deterministic, offline tests for the candidate merging system.

Covers the required cases:

* duplicate candidates sharing an exact email (or github) collapse into ONE
  canonical candidate, their distinct emails union, and the ``candidate_id`` is
  deterministic;
* conflicting values are resolved by the confidence-first ladder (0.95 beats
  0.70), with the losing source still recorded in provenance and the field
  confidence reflecting the disagreement;
* records missing email/phone/company merge gracefully (missing -> None/[],
  never invented);
* determinism: identical inputs yield identical canonical output (including
  provenance ordering and candidate_id);
* "don't over-merge": two different people with distinct strong identifiers and
  only weak name overlap stay as TWO candidates.

No network and no randomness anywhere.
"""

from __future__ import annotations

from typing import List, Optional

from eightfold_transformer.app.models.partial import (
    PartialExperienceItem,
    PartialLinks,
    PartialRecord,
)
from eightfold_transformer.app.models.schema import CanonicalCandidate, TrackedValue
from eightfold_transformer.app.merger import merge, merge_cluster
from eightfold_transformer.app.merger.matcher import (
    NAME_SIMILARITY_THRESHOLD,
    cluster,
    name_blocking_keys,
    name_candidate_pairs,
    name_similarity,
    normalize_name_key,
)
from eightfold_transformer.app.merger.resolver import (
    Contribution,
    resolve_scalar,
    resolve_value_list,
)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------
def _tv(value, source: str, confidence: float, method: str = "structured") -> TrackedValue:
    return TrackedValue(
        value=value, source=source, confidence=confidence, extraction_method=method
    )


def _record(
    source: str,
    *,
    name: Optional[str] = None,
    name_conf: float = 0.9,
    emails: Optional[List[str]] = None,
    phones: Optional[List[str]] = None,
    github: Optional[str] = None,
    company: Optional[str] = None,
    title: Optional[str] = None,
    exp_conf: float = 0.9,
    skills: Optional[List[str]] = None,
    confidence: float = 0.9,
    method: str = "structured",
) -> PartialRecord:
    record = PartialRecord(source=source)
    if name is not None:
        record.full_name = _tv(name, source, name_conf, method)
    for email in emails or []:
        record.emails.append(_tv(email, source, confidence, method))
    for phone in phones or []:
        record.phones.append(_tv(phone, source, confidence, method))
    if github is not None:
        record.links = PartialLinks(github=_tv(github, source, confidence, method))
    if company is not None or title is not None:
        record.experience.append(
            PartialExperienceItem(
                company=_tv(company, source, exp_conf, method) if company else None,
                title=_tv(title, source, exp_conf, method) if title else None,
            )
        )
    for skill in skills or []:
        record.skills.append(_tv(skill, source, confidence, method))
    return record


# ---------------------------------------------------------------------------
# Required case 1: duplicate candidate collapses, emails union, stable id
# ---------------------------------------------------------------------------
def test_duplicate_email_collapses_into_one_candidate():
    a = _record(
        "recruiter_csv",
        name="Priya Sharma",
        emails=["priya.sharma@gmail.com"],
        phones=["+1-555-123-4567"],
    )
    b = _record(
        "resume",
        name="Priya Sharma",
        emails=["Priya.Sharma@gmail.com", "priya@work.com"],
        method="regex",
        confidence=0.75,
    )

    candidates = merge([a, b])

    assert len(candidates) == 1
    candidate = candidates[0]
    # Distinct emails union (case-insensitive dedupe of the shared address).
    email_values = {tv.value for tv in candidate.emails}
    assert email_values == {"priya.sharma@gmail.com", "priya@work.com"}
    assert candidate.candidate_id.extraction_method == "derived"
    assert candidate.candidate_id.value.startswith("cand_")


def test_duplicate_github_collapses_into_one_candidate():
    a = _record("resume", name="Priya Sharma", github="github.com/priyasharma")
    b = _record(
        "github_api",
        name="Priya Sharma",
        github="https://www.github.com/priyasharma/",
    )
    candidates = merge([a, b])
    assert len(candidates) == 1
    assert candidates[0].links.github is not None
    assert candidates[0].links.github.value == "github.com/priyasharma"


def test_candidate_id_is_deterministic_and_order_independent():
    a = _record("recruiter_csv", name="Priya Sharma", emails=["priya@x.com"])
    b = _record("resume", name="Priya Sharma", emails=["priya@x.com"])
    id_ab = merge([a, b])[0].candidate_id.value
    id_ba = merge([b, a])[0].candidate_id.value
    assert id_ab == id_ba


# ---------------------------------------------------------------------------
# Required case 2: conflicting values -> higher confidence wins; loser tracked
# ---------------------------------------------------------------------------
def test_higher_confidence_wins_on_scalar_ladder():
    # Same person (shared email), conflicting full_name with different confidence.
    high = _record(
        "resume",
        name="Priyanka Sharma",
        name_conf=0.95,
        emails=["priya@x.com"],
        method="regex",
    )
    low = _record(
        "recruiter_notes",
        name="Priya S",
        name_conf=0.70,
        emails=["priya@x.com"],
        method="free-text",
    )
    candidate = merge([high, low])[0]

    # 0.95 value wins over 0.70.
    assert candidate.full_name.value == "Priyanka Sharma"
    # Disagreement lowers field confidence below the winner's raw 0.95.
    assert candidate.full_name.confidence < 0.95
    # Losing source is still present in provenance for the field.
    name_sources = {
        p.source for p in candidate.provenance if p.field == "full_name"
    }
    assert name_sources == {"resume", "recruiter_notes"}


def test_resolver_ladder_on_company_title_values():
    # Directly exercise the resolver on competing company/title values.
    contribs = [
        Contribution("Acme Corp", "resume", 0.95, "structured"),
        Contribution("Globex Inc", "recruiter_notes", 0.70, "free-text"),
    ]
    winner = resolve_scalar(contribs)
    assert winner.value == "Acme Corp"
    assert winner.source == "resume"

    titles = [
        Contribution("Senior Engineer", "recruiter_csv", 0.90, "structured"),
        Contribution("Engineer", "recruiter_notes", 0.50, "free-text"),
    ]
    assert resolve_scalar(titles).value == "Senior Engineer"


def test_resolver_tie_breaks_on_agreement_then_lexicographic():
    # Equal confidence -> more corroborating sources wins.
    contribs = [
        Contribution("Acme", "recruiter_csv", 0.8, "structured"),
        Contribution("Acme", "resume", 0.8, "structured"),
        Contribution("Globex", "recruiter_notes", 0.8, "structured"),
    ]
    assert resolve_scalar(contribs).value == "Acme"

    # Fully tied (single source each, same method) -> lexicographic smallest.
    tied = [
        Contribution("Zeta", "a", 0.8, "structured"),
        Contribution("Alpha", "b", 0.8, "structured"),
    ]
    assert resolve_scalar(tied).value == "Alpha"


def test_value_list_confidence_rewards_corroboration():
    both = resolve_value_list(
        [
            Contribution("python", "recruiter_csv", 0.8, "structured"),
            Contribution("python", "resume", 0.8, "structured"),
        ]
    )
    one = resolve_value_list(
        [
            Contribution("python", "recruiter_csv", 0.8, "structured"),
            Contribution("rust", "resume", 0.8, "structured"),
        ]
    )
    conf_both = both[0].confidence
    conf_python_solo = next(tv.confidence for tv in one if tv.value == "python")
    assert conf_both > conf_python_solo


# ---------------------------------------------------------------------------
# Required case 3: missing fields merge gracefully (nothing invented)
# ---------------------------------------------------------------------------
def test_missing_fields_merge_gracefully():
    sparse = PartialRecord(source="recruiter_notes")  # empty-but-valid
    named = _record("resume", name="Jordan Kim")
    # Sparse has no identifiers, so it forms its own cluster; that's fine.
    candidates = merge([sparse, named])
    assert len(candidates) == 2
    for c in candidates:
        assert isinstance(c, CanonicalCandidate)
        # Nothing fabricated: absent scalars are None, absent lists empty.
        assert c.headline is None
        assert isinstance(c.emails, list)
        assert isinstance(c.phones, list)


def test_unparseable_phone_is_dropped_not_invented():
    rec = _record("recruiter_csv", name="Jordan Kim", emails=["jk@x.com"], phones=["call me"])
    candidate = merge([rec])[0]
    assert candidate.phones == []  # invalid phone dropped, never invented
    # But the attempt is still auditable in provenance.
    assert any(p.field == "phones" for p in candidate.provenance)


def test_empty_input_yields_no_candidates():
    assert merge([]) == []


# ---------------------------------------------------------------------------
# Required case 4: determinism (byte-identical output)
# ---------------------------------------------------------------------------
def test_merge_is_fully_deterministic():
    def build():
        a = _record(
            "recruiter_csv",
            name="Priya Sharma",
            emails=["priya@x.com"],
            phones=["+1-555-123-4567"],
            company="Acme Corp",
            title="Engineer",
            skills=["py", "k8s"],
        )
        b = _record(
            "resume",
            name="Priya Sharma",
            emails=["priya@work.com"],
            github="github.com/priyasharma",
            skills=["Python", "JS"],
            method="regex",
            confidence=0.75,
        )
        return [a, b]

    first = merge(build())
    second = merge(build())
    assert [c.model_dump() for c in first] == [c.model_dump() for c in second]
    # Provenance ordering is stable too.
    assert [p.model_dump() for p in first[0].provenance] == [
        p.model_dump() for p in second[0].provenance
    ]


# ---------------------------------------------------------------------------
# Required case 5: don't over-merge two different people
# ---------------------------------------------------------------------------
def test_does_not_over_merge_distinct_identities():
    # Same common name, but distinct emails AND distinct github -> two people.
    a = _record(
        "recruiter_csv",
        name="John Smith",
        emails=["john.smith@acme.com"],
        github="github.com/jsmith-acme",
    )
    b = _record(
        "resume",
        name="John Smith",
        emails=["john.smith@globex.com"],
        github="github.com/jsmith-globex",
    )
    candidates = merge([a, b])
    assert len(candidates) == 2


def test_name_only_merge_when_no_conflicting_identifiers():
    # Identical names, one side has no strong identifier -> safe to merge.
    a = _record("recruiter_csv", name="Aisha Khan", emails=["aisha@x.com"])
    b = _record("recruiter_notes", name="Aisha Khan", method="free-text", confidence=0.5)
    candidates = merge([a, b])
    assert len(candidates) == 1


def test_transitive_clustering_across_keys():
    # A~B by email, B~C by github -> single cluster {A, B, C}.
    a = _record("recruiter_csv", name="Lee", emails=["lee@x.com"])
    b = _record(
        "resume", name="Lee", emails=["lee@x.com"], github="github.com/lee", method="regex"
    )
    c = _record("github_api", name="Lee", github="github.com/lee")
    clusters = cluster([a, b, c])
    assert len(clusters) == 1
    assert len(clusters[0]) == 3
    assert len(merge([a, b, c])) == 1


# ---------------------------------------------------------------------------
# Skills aggregation
# ---------------------------------------------------------------------------
def test_skills_canonicalized_and_sources_aggregated():
    a = _record("recruiter_csv", name="Sam", emails=["sam@x.com"], skills=["py", "k8s"])
    b = _record(
        "resume", name="Sam", emails=["sam@x.com"], skills=["Python", "React"], method="regex"
    )
    candidate = merge([a, b])[0]
    by_name = {s.name: s for s in candidate.skills}
    assert "Python" in by_name and "Kubernetes" in by_name and "React" in by_name
    # Python attested by both sources -> both recorded.
    assert by_name["Python"].sources == ["recruiter_csv", "resume"]


def test_merge_cluster_requires_non_empty():
    import pytest

    with pytest.raises(ValueError):
        merge_cluster([])


# ---------------------------------------------------------------------------
# Blocking: recall preserved while the expensive name pass is reduced
# ---------------------------------------------------------------------------
def test_blocking_preserves_name_merge_with_no_conflict():
    # Two records, identical name, neither carries a conflicting strong id.
    # Under blocking they must still land in a shared block and merge.
    a = _record("recruiter_csv", name="Aisha Khan", emails=["aisha@x.com"])
    b = _record("recruiter_notes", name="Aisha Khan", method="free-text", confidence=0.5)
    clusters = cluster([a, b])
    assert len(clusters) == 1
    assert len(clusters[0]) == 2
    # And the pair was actually offered to the similarity check.
    assert (0, 1) in name_candidate_pairs([a, b])


def test_blocking_recall_on_near_duplicate_name_above_threshold():
    # "Priya  Sharma" normalizes (collapsed whitespace) to "priya sharma";
    # a slight given-name edit keeps similarity above 0.90. Both must share a
    # block (surname token "sharma") and merge with no conflicting identifiers.
    a = _record("recruiter_notes", name="Priya  Sharma")
    b = _record("resume", name="Priyaa Sharma", method="regex", confidence=0.6)
    assert name_similarity("Priya  Sharma", "Priyaa Sharma") >= NAME_SIMILARITY_THRESHOLD
    shared = name_blocking_keys(normalize_name_key("Priya  Sharma")) & name_blocking_keys(
        normalize_name_key("Priyaa Sharma")
    )
    assert shared  # at least one common block key
    clusters = cluster([a, b])
    assert len(clusters) == 1
    assert len(clusters[0]) == 2


def test_blocking_does_not_compare_unrelated_names():
    # Completely different names should not share any block key, so the pair is
    # never even fed to SequenceMatcher.
    a = _record("a", name="Aisha Khan", emails=["aisha@x.com"])
    b = _record("b", name="Wolfgang Mozart", emails=["wolfgang@y.com"])
    assert name_candidate_pairs([a, b]) == []
    assert len(cluster([a, b])) == 2


def test_blocking_recall_matches_bruteforce_on_random_like_set():
    # The blocking candidate set must be a superset of every pair the brute-force
    # O(n^2) scan would have merged on name. We verify that for a deterministic
    # mixed set, blocking and a full scan produce identical clusters.
    names = [
        "Priya Sharma",
        "Priya Sharma",
        "Priyaa Sharma",
        "John Smith",
        "Jon Smith",
        "Aisha Khan",
        "Aisha Khan",
        "Wolfgang Mozart",
        "Lee",
        "Lee",
    ]
    partials = [_record(f"s{i}", name=nm) for i, nm in enumerate(names)]

    blocked = cluster(partials)

    # Brute-force reference: every i<j pair above threshold, same guards.
    pairs = set(name_candidate_pairs(partials))
    n = len(partials)
    for i in range(n):
        for j in range(i + 1, n):
            if name_similarity(names[i], names[j]) >= NAME_SIMILARITY_THRESHOLD:
                # No strong conflicts here (no shared/disjoint ids), so brute
                # force would compare-and-(maybe-)merge this pair; blocking must
                # offer it too.
                assert (i, j) in pairs

    # Reconstruct expected clusters from the brute-force name graph.
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if name_similarity(names[i], names[j]) >= NAME_SIMILARITY_THRESHOLD:
                ri, rj = find(i), find(j)
                if ri != rj:
                    lo, hi = (ri, rj) if ri < rj else (rj, ri)
                    parent[hi] = lo
    expected_components = len({find(i) for i in range(n)})
    assert len(blocked) == expected_components


# ---------------------------------------------------------------------------
# Blocking: scale / efficiency sanity (correctness + far fewer comparisons)
# ---------------------------------------------------------------------------
def test_blocking_scales_far_below_quadratic_and_stays_correct():
    import time

    groups = [
        ("Priya Sharma", "priya"),
        ("John Smith", "john"),
        ("Aisha Khan", "aisha"),
        ("Wolfgang Mozart", "wolfgang"),
        ("Lee Anderson", "lee"),
    ]
    per_group = 60  # 5 groups * 60 = 300 records
    partials = []
    for gname, gslug in groups:
        for k in range(per_group):
            # Distinct emails -> NO name merge (strong-conflict guard) so each
            # record is its own cluster; this stresses the comparison count
            # because every same-name pair is a block candidate but conflicts.
            partials.append(
                _record(
                    f"{gslug}-{k}",
                    name=gname,
                    emails=[f"{gslug}.{k}@example.com"],
                )
            )

    n = len(partials)
    pairs = name_candidate_pairs(partials)
    brute = n * (n - 1) // 2

    # Comparisons are confined to within-group blocks: roughly
    # groups * C(per_group, 2), far below the full quadratic.
    assert len(pairs) < brute // 2
    assert len(pairs) <= len(groups) * (per_group * (per_group - 1) // 2)

    start = time.perf_counter()
    clusters = cluster(partials)
    elapsed = time.perf_counter() - start

    # Distinct emails per record -> no merges at all: n clusters.
    assert len(clusters) == n
    assert elapsed < 5.0  # generous; deterministic & offline


def test_blocking_scale_with_real_name_merges_is_correct():
    # Same groups but records within a group share an email -> they SHOULD all
    # collapse into one cluster per group via email + name. Verifies blocking
    # doesn't lose true within-group recall at scale.
    groups = ["Priya Sharma", "John Smith", "Aisha Khan", "Lee Anderson"]
    per_group = 50
    partials = []
    for gi, gname in enumerate(groups):
        for k in range(per_group):
            partials.append(
                _record(f"g{gi}-{k}", name=gname, emails=[f"group{gi}@example.com"])
            )
    clusters = cluster(partials)
    assert len(clusters) == len(groups)
    for c in clusters:
        assert len(c) == per_group


def test_name_candidate_pairs_is_deterministic_and_order_sorted():
    partials = [
        _record("a", name="Priya Sharma"),
        _record("b", name="Priya Sharma"),
        _record("c", name="John Smith"),
        _record("d", name="Priya Sharma"),
    ]
    pairs = name_candidate_pairs(partials)
    assert pairs == sorted(pairs)
    # Priya records are indices 0,1,3 -> all three pairs present; John (2) alone.
    assert (0, 1) in pairs and (0, 3) in pairs and (1, 3) in pairs
    assert all(2 not in pair for pair in pairs)


def test_records_without_name_get_no_blocking_keys():
    assert name_blocking_keys(None) == set()
    assert name_blocking_keys("") == set()
    a = _record("a", emails=["x@x.com"])  # no name
    b = _record("b", emails=["y@y.com"])  # no name
    assert name_candidate_pairs([a, b]) == []
