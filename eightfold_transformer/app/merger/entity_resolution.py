"""Composite entity-resolution scoring for partial record pairs.

Supplements exact email/profile matching in :mod:`matcher` with a weighted,
deterministic score built from normalized names, nickname/initial agreement,
email/phone/company/title overlap, and cross-source corroboration.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import List, Optional, Set

from eightfold_transformer.app.models.partial import PartialRecord
from eightfold_transformer.app.merger.matcher import (
    normalize_email_key,
    normalize_name_key,
    normalize_profile_key,
    name_similarity,
)
from eightfold_transformer.app.normalization.companies import companies_equivalent, company_match_key
from eightfold_transformer.app.normalization.phones import normalize_phone

__all__ = [
    "MATCH_THRESHOLD",
    "STRONG_OVERRIDE_THRESHOLD",
    "composite_match_score",
    "has_corroboration",
    "composite_candidate_pairs",
]

#: Union pairs at or above this score (when guards pass).
MATCH_THRESHOLD: float = 0.58

#: When strong identifiers conflict, require at least this score to merge.
STRONG_OVERRIDE_THRESHOLD: float = 0.72

# Weight budget (sums to 1.0).
_W_NAME = 0.22
_W_INITIAL = 0.18
_W_EMAIL = 0.12
_W_PHONE = 0.22
_W_COMPANY = 0.14
_W_TITLE = 0.07
_W_RESUME = 0.05

_RESUME_SOURCES = frozenset({"resume"})


def _name_of(record: PartialRecord) -> Optional[str]:
    return record.full_name.value if record.full_name is not None else None


def _emails_of(record: PartialRecord) -> Set[str]:
    keys: Set[str] = set()
    for tv in record.emails:
        key = normalize_email_key(tv.value)
        if key:
            keys.add(key)
    return keys


_NON_DIGIT = re.compile(r"\D+")


def _phone_match_key(raw: str) -> Optional[str]:
    """Return a comparable phone key (E.164 when valid, else trailing digits)."""
    e164 = normalize_phone(raw)
    if e164:
        return e164
    digits = _NON_DIGIT.sub("", raw)
    if len(digits) < 7:
        return None
    return digits[-10:] if len(digits) >= 10 else digits


def _phones_of(record: PartialRecord) -> Set[str]:
    keys: Set[str] = set()
    for tv in record.phones:
        key = _phone_match_key(tv.value)
        if key:
            keys.add(key)
    return keys


def _companies_of(record: PartialRecord) -> Set[str]:
    keys: Set[str] = set()
    for pe in record.experience:
        if pe.company is not None and pe.company.value:
            key = company_match_key(pe.company.value)
            if key:
                keys.add(key)
    return keys


def _titles_of(record: PartialRecord) -> Set[str]:
    keys: Set[str] = set()
    for pe in record.experience:
        if pe.title is not None and pe.title.value:
            text = pe.title.value.strip().lower()
            if text:
                keys.add(text)
    return keys


def _sources_of(record: PartialRecord) -> Set[str]:
    return {record.source}


def _initial_nickname_score(name_a: Optional[str], name_b: Optional[str]) -> float:
    """Score first-name initial / nickname agreement with same surname."""
    ka, kb = normalize_name_key(name_a), normalize_name_key(name_b)
    if not ka or not kb:
        return 0.0
    ta, tb = ka.split(), kb.split()
    if len(ta) < 1 or len(tb) < 1:
        return 0.0
    if ta[-1] != tb[-1]:
        return 0.0
    fa, fb = ta[0].rstrip("."), tb[0].rstrip(".")
    if fa == fb:
        return 1.0
    # Single-letter (or initial) vs full given name.
    if len(fa) == 1 and fb.startswith(fa):
        return 1.0
    if len(fb) == 1 and fa.startswith(fb):
        return 1.0
    # Shared prefix on given name (Priyaa / Priya).
    if len(fa) >= 2 and len(fb) >= 2 and fa[:3] == fb[:3]:
        return 0.85
    return 0.0


def _email_similarity(ea: str, eb: str) -> float:
    if ea == eb:
        return 1.0
    la, _, da = ea.partition("@")
    lb, _, db = eb.partition("@")
    if not la or not lb or not da or not db:
        return 0.0
    if da != db:
        # Same person may use work vs personal domains; partial credit when locals align.
        local_sim = SequenceMatcher(None, la, lb).ratio()
        if local_sim >= 0.85:
            return 0.35
        root_a = la.split(".")[0].split("+")[0]
        root_b = lb.split(".")[0].split("+")[0]
        if root_a == root_b or root_a.startswith(root_b) or root_b.startswith(root_a):
            return 0.45
        return 0.0
    if la == lb:
        return 1.0
    root_a = la.split(".")[0].split("+")[0]
    root_b = lb.split(".")[0].split("+")[0]
    if root_a == root_b:
        return 0.9
    if root_a.startswith(root_b) or root_b.startswith(root_a):
        return 0.75
    return SequenceMatcher(None, la, lb).ratio() * 0.6


def _best_email_similarity(emails_a: Set[str], emails_b: Set[str]) -> float:
    if not emails_a or not emails_b:
        return 0.0
    return max(_email_similarity(a, b) for a in emails_a for b in emails_b)


def _set_overlap_score(a: Set[str], b: Set[str]) -> float:
    if not a or not b:
        return 0.0
    if a & b:
        return 1.0
    return 0.0


def _company_overlap_score(a: Set[str], b: Set[str], raw_a: PartialRecord, raw_b: PartialRecord) -> float:
    if a & b:
        return 1.0
    # Fuzzy match on raw company strings when keys differ slightly.
    companies_a = [
        pe.company.value
        for pe in raw_a.experience
        if pe.company is not None and pe.company.value
    ]
    companies_b = [
        pe.company.value
        for pe in raw_b.experience
        if pe.company is not None and pe.company.value
    ]
    for ca in companies_a:
        for cb in companies_b:
            if companies_equivalent(ca, cb):
                return 1.0
    return 0.0


def _title_overlap_score(titles_a: Set[str], titles_b: Set[str]) -> float:
    if not titles_a or not titles_b:
        return 0.0
    if titles_a & titles_b:
        return 1.0
    best = 0.0
    for ta in titles_a:
        for tb in titles_b:
            best = max(best, SequenceMatcher(None, ta, tb).ratio())
    return best if best >= 0.85 else 0.0


def _resume_corroboration(a: PartialRecord, b: PartialRecord) -> float:
    sa, sb = _sources_of(a), _sources_of(b)
    if sa & _RESUME_SOURCES and sb - _RESUME_SOURCES:
        return 1.0
    if sb & _RESUME_SOURCES and sa - _RESUME_SOURCES:
        return 1.0
    return 0.0


def composite_match_score(a: PartialRecord, b: PartialRecord) -> float:
    """Return a deterministic match score in ``[0, 1]`` for one record pair."""
    name_a, name_b = _name_of(a), _name_of(b)
    score = 0.0
    score += _W_NAME * name_similarity(name_a, name_b)
    score += _W_INITIAL * _initial_nickname_score(name_a, name_b)
    score += _W_EMAIL * _best_email_similarity(_emails_of(a), _emails_of(b))
    score += _W_PHONE * _set_overlap_score(_phones_of(a), _phones_of(b))
    score += _W_COMPANY * _company_overlap_score(
        _companies_of(a), _companies_of(b), a, b
    )
    score += _W_TITLE * _title_overlap_score(_titles_of(a), _titles_of(b))
    score += _W_RESUME * _resume_corroboration(a, b)
    return min(score, 1.0)


def has_corroboration(a: PartialRecord, b: PartialRecord) -> bool:
    """True when independent signals (beyond name alone) support same identity."""
    if _phones_of(a) & _phones_of(b):
        return True
    if _best_email_similarity(_emails_of(a), _emails_of(b)) >= 0.75:
        return True
    profiles_a = {
        normalize_profile_key(tv.value)
        for tv in (a.links.github, a.links.linkedin)
        if tv is not None and normalize_profile_key(tv.value)
    }
    profiles_b = {
        normalize_profile_key(tv.value)
        for tv in (b.links.github, b.links.linkedin)
        if tv is not None and normalize_profile_key(tv.value)
    }
    if profiles_a & profiles_b:
        return True
    if (
        _company_overlap_score(_companies_of(a), _companies_of(b), a, b) >= 1.0
        and _initial_nickname_score(_name_of(a), _name_of(b)) >= 0.85
    ):
        return True
    if _resume_corroboration(a, b) >= 1.0 and (
        _phones_of(a) & _phones_of(b)
        or _company_overlap_score(_companies_of(a), _companies_of(b), a, b) >= 1.0
        or _best_email_similarity(_emails_of(a), _emails_of(b)) >= 0.45
    ):
        return True
    return False


def composite_candidate_pairs(partials: List[PartialRecord]) -> List[tuple[int, int]]:
    """Return sorted candidate pairs for composite scoring (blocking)."""
    n = len(partials)
    blocks: dict[str, List[int]] = {}

    def add_block(key: str, idx: int) -> None:
        if key:
            blocks.setdefault(key, []).append(idx)

    for i, record in enumerate(partials):
        name_key = normalize_name_key(_name_of(record))
        if name_key:
            tokens = name_key.split()
            if tokens:
                add_block("last:" + tokens[-1], i)
                add_block("init:" + tokens[0][0] + tokens[-1], i)
        for phone in _phones_of(record):
            add_block("phone:" + phone, i)
        for company in _companies_of(record):
            add_block("co:" + company, i)
        for email in _emails_of(record):
            local, _, domain = email.partition("@")
            if local and domain:
                add_block("email:" + local.split(".")[0] + "@" + domain, i)

    pairs: set[tuple[int, int]] = set()
    for indices in blocks.values():
        m = len(indices)
        for a in range(m):
            ia = indices[a]
            for b in range(a + 1, m):
                ib = indices[b]
                pairs.add((ia, ib) if ia < ib else (ib, ia))
    return sorted(pairs)
