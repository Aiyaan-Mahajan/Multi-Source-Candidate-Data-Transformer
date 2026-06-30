"""Deterministic conflict resolution for a matched cluster of partial records.

Given a cluster of :class:`PartialRecord` objects (all judged to be the same
candidate by :mod:`matcher`), this module produces the canonical field values.
Two ideas drive every rule here:

* **Never blindly overwrite.** Competing values are compared on an explicit,
  documented ladder; losers are not discarded silently (their sources are
  preserved by :mod:`provenance`) and disagreement *lowers* the resulting field
  confidence (via :func:`confidence.field_confidence`).
* **Normalize raw -> canonical here.** Raw partial values are routed through the
  shared normalizers (:func:`normalize_phone`, :func:`normalize_date`,
  :func:`normalize_country`, :func:`normalize_skill`) to build canonical fields
  and the keys used for de-duplication. A value that fails to normalize (e.g. an
  unparseable phone) is dropped, never invented.

Scalar conflict ladder (highest priority first)
-----------------------------------------------
1. **Higher confidence wins** - the per-value ``TrackedValue.confidence`` from
   the contributing partial (e.g. a structured resume experience at 0.95 beats a
   free-text recruiter note at 0.70).
2. **More corroborating sources** - if confidence ties, the value attested by
   more distinct sources wins.
3. **More reliable method** - if still tied, prefer the more reliable extraction
   method (``structured`` > ``regex`` > ``free-text``; ``derived`` == top).
4. **Lexicographic tie-break** - finally, the smallest normalized value wins, so
   the outcome is always deterministic.

List fields (emails, phones, skills, experience, education)
-----------------------------------------------------------
List fields are **unioned and de-duplicated on their normalized form** (emails
case-insensitively, phones by E.164, skills by canonical name, experience /
education by their identifying tuple). Each surviving value keeps its own
provenance and its own confidence (more corroborating sources -> higher).
"""

from __future__ import annotations

import re
from collections import namedtuple
from typing import Callable, List, Optional

from eightfold_transformer.app.models.partial import PartialRecord
from eightfold_transformer.app.models.schema import (
    EducationItem,
    ExperienceItem,
    Links,
    Location,
    Skill,
    TrackedValue,
)
from eightfold_transformer.app.normalization import (
    is_allowed_skill,
    normalize_company,
    normalize_country,
    normalize_date,
    normalize_phone,
    normalize_skill,
)
from eightfold_transformer.app.merger.confidence import (
    field_confidence,
    method_reliability,
)
from eightfold_transformer.app.merger.matcher import normalize_profile_key
from eightfold_transformer.app.normalization.companies import company_match_key

__all__ = [
    "Contribution",
    "resolve_scalar",
    "resolve_value_list",
    "build_location",
    "build_links",
    "build_skills",
    "build_experience",
    "build_education",
    "resolve_cluster",
    "split_date_range",
]

#: A single competing contribution to one field: an already-normalized canonical
#: ``value`` plus the provenance of the partial it came from.
Contribution = namedtuple("Contribution", ["value", "source", "confidence", "method"])

_WS = re.compile(r"\s+")
# A spaced range separator (dash variants or the word "to"). Spaces are required
# so we don't accidentally split an inner date like "2024-03".
_RANGE_SEP = re.compile(r"\s+(?:-|–|—|to)\s+", re.IGNORECASE)
_OPEN_ENDED = re.compile(r"(?i)\b(present|current|now|ongoing|to date)\b")


def _clean_text(value: Optional[str]) -> Optional[str]:
    """Trim and collapse internal whitespace; ``None``/blank -> ``None``."""
    if not isinstance(value, str):
        return None
    cleaned = _WS.sub(" ", value.strip())
    return cleaned or None


# ---------------------------------------------------------------------------
# Generic winner selection
# ---------------------------------------------------------------------------
def resolve_scalar(
    contributions: List[Contribution],
) -> Optional[TrackedValue]:
    """Pick a single winning value from competing contributions via the ladder.

    Returns ``None`` when no contribution carries a value. The returned
    :class:`TrackedValue` records the winning value, the *primary* source that
    backed it, the field confidence (which reflects agreement across all
    supplying sources), and the winning extraction method.
    """
    contribs = [c for c in contributions if c.value is not None]
    if not contribs:
        return None

    groups: dict[object, List[Contribution]] = {}
    for c in contribs:
        groups.setdefault(c.value, []).append(c)

    def group_rank(value: object) -> tuple:
        group = groups[value]
        max_conf = max(c.confidence for c in group)
        support = len({c.source for c in group})
        best_method = max(method_reliability(c.method) for c in group)
        # Lower tuple sorts first -> negate "more is better" signals.
        return (-max_conf, -support, -best_method, str(value))

    winner_value = min(groups, key=group_rank)
    winning_group = groups[winner_value]
    primary = min(
        winning_group,
        key=lambda c: (-c.confidence, -method_reliability(c.method), c.source),
    )
    winner_sources = {c.source for c in winning_group}
    supplying = {c.source for c in contribs}
    conf = field_confidence(winner_sources, supplying, primary.method)
    return TrackedValue(
        value=winner_value,
        source=primary.source,
        confidence=conf,
        extraction_method=primary.method,
    )


def resolve_value_list(
    contributions: List[Contribution],
) -> List[TrackedValue]:
    """Union + dedupe contributions by normalized value, one carrier each.

    Each distinct normalized value yields one :class:`TrackedValue`; its
    confidence rewards corroboration (more sources attesting the *same* value
    relative to all sources that supplied the field). Output is ordered by value
    for determinism.
    """
    contribs = [c for c in contributions if c.value is not None]
    if not contribs:
        return []

    supplying = {c.source for c in contribs}
    groups: dict[object, List[Contribution]] = {}
    for c in contribs:
        groups.setdefault(c.value, []).append(c)

    result: List[TrackedValue] = []
    for value in sorted(groups, key=str):
        group = groups[value]
        primary = min(
            group,
            key=lambda c: (-c.confidence, -method_reliability(c.method), c.source),
        )
        srcs = {c.source for c in group}
        conf = field_confidence(srcs, supplying, primary.method)
        result.append(
            TrackedValue(
                value=value,
                source=primary.source,
                confidence=conf,
                extraction_method=primary.method,
            )
        )
    return result


# ---------------------------------------------------------------------------
# Per-field contribution gatherers (raw partial value -> canonical value)
# ---------------------------------------------------------------------------
def _scalar_contribs(
    cluster: List[PartialRecord],
    getter: Callable[[PartialRecord], Optional[TrackedValue]],
    transform: Callable[[object], Optional[object]],
) -> List[Contribution]:
    contribs: List[Contribution] = []
    for record in cluster:
        tv = getter(record)
        if tv is None or tv.value is None:
            continue
        value = transform(tv.value)
        if value is None or (isinstance(value, str) and not value):
            continue
        contribs.append(
            Contribution(value, tv.source, tv.confidence, tv.extraction_method)
        )
    return contribs


def _list_contribs(
    cluster: List[PartialRecord],
    getter: Callable[[PartialRecord], List[TrackedValue]],
    transform: Callable[[object], Optional[object]],
) -> List[Contribution]:
    contribs: List[Contribution] = []
    for record in cluster:
        for tv in getter(record):
            if tv.value is None:
                continue
            value = transform(tv.value)
            if value is None or (isinstance(value, str) and not value):
                continue
            contribs.append(
                Contribution(value, tv.source, tv.confidence, tv.extraction_method)
            )
    return contribs


def _norm_email(value: object) -> Optional[str]:
    if not isinstance(value, str):
        return None
    return value.strip().lower() or None


def build_location(cluster: List[PartialRecord]) -> Location:
    """Resolve city/region (cleaned text) and country (ISO-3166 alpha-2)."""
    city = resolve_scalar(
        _scalar_contribs(cluster, lambda r: r.location.city, _clean_text)
    )
    region = resolve_scalar(
        _scalar_contribs(cluster, lambda r: r.location.region, _clean_text)
    )
    country = resolve_scalar(
        _scalar_contribs(cluster, lambda r: r.location.country, normalize_country)
    )
    return Location(city=city, region=region, country=country)


def build_links(cluster: List[PartialRecord]) -> Links:
    """Resolve linkedin/github/portfolio plus a deduped ``other`` union.

    Profile links are normalized to their comparable ``host+path`` key so that
    differently-formatted spellings of the same profile dedupe cleanly.
    """
    linkedin = resolve_scalar(
        _scalar_contribs(cluster, lambda r: r.links.linkedin, normalize_profile_key)
    )
    github = resolve_scalar(
        _scalar_contribs(cluster, lambda r: r.links.github, normalize_profile_key)
    )
    portfolio = resolve_scalar(
        _scalar_contribs(cluster, lambda r: r.links.portfolio, normalize_profile_key)
    )
    other = resolve_value_list(
        _list_contribs(cluster, lambda r: r.links.other, normalize_profile_key)
    )
    return Links(linkedin=linkedin, github=github, portfolio=portfolio, other=other)


def build_skills(cluster: List[PartialRecord]) -> List[Skill]:
    """Aggregate raw skill tokens into canonical :class:`Skill` records.

    Only skills present in the configured vocabulary survive; unknown tokens such
    as ``Ninjutsu`` are dropped at merge time.
    """
    supplying: set[str] = set()
    per_skill: dict[str, dict] = {}

    for record in cluster:
        for tv in record.skills:
            if tv.value is None:
                continue
            if not is_allowed_skill(tv.value):
                continue
            name = normalize_skill(tv.value)
            if not isinstance(name, str):
                continue
            name = name.strip()
            if not name:
                continue
            supplying.add(tv.source)
            rel = method_reliability(tv.extraction_method)
            entry = per_skill.setdefault(
                name,
                {"sources": set(), "best_method": tv.extraction_method, "best_rel": rel},
            )
            entry["sources"].add(tv.source)
            if rel > entry["best_rel"]:
                entry["best_rel"] = rel
                entry["best_method"] = tv.extraction_method

    skills: List[Skill] = []
    for name in sorted(per_skill):
        entry = per_skill[name]
        conf = field_confidence(entry["sources"], supplying, entry["best_method"])
        skills.append(
            Skill(name=name, confidence=conf, sources=sorted(entry["sources"]))
        )
    return skills


def split_date_range(raw: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """Split a raw employment date range into canonical ``(start, end)``.

    Recognizes spaced separators (``"-"``, en/em dash, or ``"to"``) and
    open-ended markers (``"Present"``, ``"Current"``, ...). Each side is routed
    through :func:`normalize_date`; an open-ended end becomes ``None`` (ongoing).
    A bare single date is treated as ``start`` with no ``end``.
    """
    if not isinstance(raw, str) or not raw.strip():
        return (None, None)
    text = raw.strip()
    parts = _RANGE_SEP.split(text, maxsplit=1)
    if len(parts) == 2:
        left, right = parts[0].strip(), parts[1].strip()
        start = normalize_date(left)
        end = None if _OPEN_ENDED.search(right) else normalize_date(right)
        return (start, end)
    return (normalize_date(text), None)


def _date_sort_key(raw: Optional[str]) -> tuple[int, str]:
    norm = normalize_date(raw) if isinstance(raw, str) else None
    if norm:
        return (0, norm)
    return (1, raw or "")


def build_experience(cluster: List[PartialRecord]) -> List[ExperienceItem]:
    """Union employment history, merging duplicate employers into one timeline."""
    grouped: dict[str, list[dict]] = {}

    for record in cluster:
        for pe in record.experience:
            raw_company = _clean_text(pe.company.value if pe.company else None)
            company = normalize_company(raw_company) if raw_company else None
            title = _clean_text(pe.title.value if pe.title else None)
            start, end = split_date_range(pe.date_range.value if pe.date_range else None)
            summary = _clean_text(pe.summary.value if pe.summary else None)
            if not any((company, title, start, end, summary)):
                continue

            group_key = company_match_key(company) if company else f"__none__:{title or ''}"
            title_conf = pe.title.confidence if pe.title else 0.0
            title_method = pe.title.extraction_method if pe.title else "free-text"
            grouped.setdefault(group_key, []).append(
                {
                    "company": company,
                    "raw_company": raw_company,
                    "title": title,
                    "title_conf": title_conf,
                    "title_method": title_method,
                    "start": start,
                    "end": end,
                    "summary": summary,
                }
            )

    items: List[ExperienceItem] = []
    for group_key in sorted(grouped):
        entries = grouped[group_key]
        best = max(
            entries,
            key=lambda e: (
                e["title_conf"],
                method_reliability(e["title_method"]),
                len(e["title"] or ""),
            ),
        )
        starts = [e["start"] for e in entries if e["start"]]
        ends = [e["end"] for e in entries if e["end"]]
        merged_start = min(starts, key=_date_sort_key) if starts else best["start"]
        merged_end = None if any(e["end"] is None for e in entries) else (
            max(ends, key=_date_sort_key) if ends else best["end"]
        )
        summaries = sorted({e["summary"] for e in entries if e["summary"]})
        merged_summary = " | ".join(summaries) if summaries else None
        display_company = normalize_company(best["raw_company"] or best["company"] or "")

        items.append(
            ExperienceItem(
                company=display_company or best["company"],
                title=best["title"],
                start=merged_start,
                end=merged_end,
                summary=merged_summary,
            )
        )
    return items


def _year_to_int(raw: Optional[str]) -> Optional[int]:
    norm = normalize_date(raw) if isinstance(raw, str) else None
    if not norm:
        return None
    year = int(norm[:4])
    return year if 1900 <= year <= 2100 else None


def build_education(cluster: List[PartialRecord]) -> List[EducationItem]:
    """Union + dedupe education history across the cluster.

    Dedupe key is ``(institution, degree, field, end_year)``; first occurrence
    in deterministic cluster order wins. Raw years are routed through
    :func:`normalize_date` and bounded to ``[1900, 2100]``.
    """
    items: List[EducationItem] = []
    seen: set[tuple] = set()
    for record in cluster:
        for ed in record.education:
            institution = _clean_text(ed.institution.value if ed.institution else None)
            degree = _clean_text(ed.degree.value if ed.degree else None)
            field = _clean_text(ed.field.value if ed.field else None)
            end_year = _year_to_int(ed.year.value if ed.year else None)
            if not any((institution, degree, field, end_year)):
                continue
            key = (
                institution.lower() if institution else None,
                degree.lower() if degree else None,
                field.lower() if field else None,
                end_year,
            )
            if key in seen:
                continue
            seen.add(key)
            items.append(
                EducationItem(
                    institution=institution,
                    degree=degree,
                    field=field,
                    end_year=end_year,
                )
            )
    return items


def resolve_cluster(cluster: List[PartialRecord]) -> dict:
    """Resolve every canonical field for a cluster (sans id/provenance/overall).

    Returns a dict keyed by the :class:`CanonicalCandidate` field names that the
    orchestrator (:mod:`eightfold_transformer.app.merger`) assembles into the
    final record together with the derived ``candidate_id``, the flattened
    ``provenance`` ledger, and the aggregate ``overall_confidence``.
    """
    return {
        "full_name": resolve_scalar(
            _scalar_contribs(cluster, lambda r: r.full_name, _clean_text)
        ),
        "emails": resolve_value_list(
            _list_contribs(cluster, lambda r: r.emails, _norm_email)
        ),
        "phones": resolve_value_list(
            _list_contribs(cluster, lambda r: r.phones, normalize_phone)
        ),
        "location": build_location(cluster),
        "links": build_links(cluster),
        "headline": resolve_scalar(
            _scalar_contribs(cluster, lambda r: r.headline, _clean_text)
        ),
        "years_experience": resolve_scalar(
            _scalar_contribs(cluster, lambda r: r.years_experience, lambda v: v)
        ),
        "skills": build_skills(cluster),
        "experience": build_experience(cluster),
        "education": build_education(cluster),
    }
