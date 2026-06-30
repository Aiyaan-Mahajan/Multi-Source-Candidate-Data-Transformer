"""Tests for composite entity resolution and Priya-variant merging."""

from __future__ import annotations

from eightfold_transformer.app.ingestion.csv_reader import read_csv
from eightfold_transformer.app.ingestion.resume_parser import parse_resume
from eightfold_transformer.app.merger import merge
from eightfold_transformer.app.merger.entity_resolution import composite_match_score
from eightfold_transformer.app.merger.matcher import cluster
from eightfold_transformer.app.models.partial import PartialExperienceItem, PartialRecord
from eightfold_transformer.app.models.schema import TrackedValue


def _tv(value, source: str, confidence: float = 0.9, method: str = "structured"):
    return TrackedValue(
        value=value, source=source, confidence=confidence, extraction_method=method
    )


def _record(
    source: str,
    *,
    name: str | None = None,
    emails: list[str] | None = None,
    phones: list[str] | None = None,
    company: str | None = None,
    title: str | None = None,
) -> PartialRecord:
    rec = PartialRecord(source=source)
    if name:
        rec.full_name = _tv(name, source)
    for email in emails or []:
        rec.emails.append(_tv(email, source))
    for phone in phones or []:
        rec.phones.append(_tv(phone, source))
    if company or title:
        rec.experience.append(
            PartialExperienceItem(
                company=_tv(company, source) if company else None,
                title=_tv(title, source) if title else None,
            )
        )
    return rec


def test_priya_variants_merge_into_one():
    partials = [
        _record(
            "recruiter_csv",
            name="Priya Sharma",
            emails=["priya.sharma@gmail.com"],
            phones=["(555) 123-4567"],
            company="Acme Corp",
            title="Senior Backend Engineer",
        ),
        _record(
            "recruiter_csv",
            name="P. Sharma",
            phones=["5551234567"],
            company="Acme Corporation",
            title="Engineer",
        ),
        _record(
            "resume",
            name="Priya Sharma",
            emails=["priya.sharma@gmail.com"],
            phones=["+1 (555) 123.4567"],
            company="Acme Corporation",
            title="Engineering Lead",
        ),
    ]
    candidates = merge(partials)
    assert len(candidates) == 1
    assert "Priya" in candidates[0].full_name.value


def test_distinct_people_stay_separate():
    a = _record(
        "recruiter_csv",
        name="Marcus Lee",
        emails=["marcus.lee@example.com"],
        company="Initech",
        title="Data Scientist",
    )
    b = _record(
        "recruiter_csv",
        name="Marcus Lee",
        emails=["m.lee2@example.com"],
        company="Initech",
        title="Data Analyst",
    )
    assert len(merge([a, b])) == 2


def test_composite_score_prefers_phone_and_company():
    priya = _record(
        "recruiter_csv",
        name="Priya Sharma",
        phones=["5551234567"],
        company="Acme Corp",
    )
    initial = _record(
        "recruiter_csv",
        name="P. Sharma",
        phones=["5551234567"],
        company="Acme Corporation",
    )
    score = composite_match_score(priya, initial)
    assert score >= 0.58


def test_sample_data_fewer_than_seven_candidates(tmp_path):
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    csv_path = root / "data" / "candidates.csv"
    resume_path = root / "data" / "resume.txt"
    if not csv_path.is_file() or not resume_path.is_file():
        return
    partials = read_csv(csv_path) + [parse_resume(resume_path)]
    candidates = merge(partials)
    assert len(candidates) < 7
    priya_clusters = [c for c in candidates if c.full_name and "Sharma" in c.full_name.value]
    assert len(priya_clusters) == 1
