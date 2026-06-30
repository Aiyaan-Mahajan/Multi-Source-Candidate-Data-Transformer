"""Deterministic tests for the ingestion layer (CSV + resume adapters).

These tests are pure and network-free. They assert the ingestion *contract*:

* adapters emit raw, un-normalized values wrapped in ``TrackedValue`` carriers,
* provenance (``source`` / ``extraction_method``) is correct per source,
* blank/garbage/missing inputs degrade fail-soft (omitted fields, empty results,
  empty-but-valid records) and never raise.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eightfold_transformer.app.ingestion.csv_reader import read_csv
from eightfold_transformer.app.ingestion.resume_parser import parse_resume
from eightfold_transformer.app.models.partial import PartialRecord

_DATA_DIR = Path(__file__).resolve().parents[1] / "data"

_INLINE_CSV = (
    "name,email,phone,current_company,title\n"
    "Priya Sharma,priya.sharma@gmail.com,(555) 123-4567,Acme Corp,Senior Backend Engineer\n"
    "Priya Sharma,priya.sharma@gmail.com,+1-555-123-4567,Acme Corporation,Backend Engineer\n"
    "P. Sharma,,5551234567,Acme Corporation,Engineer\n"
    "Jordan Kim,jordan.kim@globex.com,call me,Globex Inc,Product Manager\n"
)

_INLINE_RESUME = """Priya Sharma
Engineering Lead, Distributed Systems
Email: priya.sharma@gmail.com | Phone: +1 (555) 123.4567 | github.com/priyasharma

SUMMARY
Backend-focused engineer who likes building reliable services.

SKILLS
py, JS, Golang, k8s, Docker, PostgreSQL, Ninjutsu

EXPERIENCE
Acme Corporation - Engineering Lead
Jan 2019 - Mar 2023
  Built event-driven services and owned the on-call rotation for billing.

Globex Inc - Backend Engineer
2016 - 2018
  Shipped the first version of the internal payments API.

EDUCATION
B.S. Computer Science, State University, 2016
"""


# --------------------------------------------------------------------------- #
# CSV adapter
# --------------------------------------------------------------------------- #
def _write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_csv_record_count_and_order(tmp_path: Path) -> None:
    path = _write(tmp_path, "candidates.csv", _INLINE_CSV)
    records = read_csv(path)
    assert len(records) == 4
    # Order preserved (determinism).
    assert [r.full_name.value for r in records] == [
        "Priya Sharma",
        "Priya Sharma",
        "P. Sharma",
        "Jordan Kim",
    ]


def test_csv_values_and_provenance(tmp_path: Path) -> None:
    path = _write(tmp_path, "candidates.csv", _INLINE_CSV)
    first = read_csv(path)[0]

    assert first.source == "recruiter_csv"
    assert first.full_name.value == "Priya Sharma"
    assert first.emails[0].value == "priya.sharma@gmail.com"
    assert first.phones[0].value == "(555) 123-4567"

    # current_company + title -> one experience entry.
    assert len(first.experience) == 1
    assert first.experience[0].company.value == "Acme Corp"
    assert first.experience[0].title.value == "Senior Backend Engineer"

    # Every tracked value carries structured provenance at high confidence.
    for tv in (first.full_name, first.emails[0], first.phones[0]):
        assert tv.source == "recruiter_csv"
        assert tv.extraction_method == "structured"
        assert tv.confidence == 0.9


def test_csv_phone_formats_captured_as_is(tmp_path: Path) -> None:
    path = _write(tmp_path, "candidates.csv", _INLINE_CSV)
    records = read_csv(path)
    phones = [r.phones[0].value for r in records]
    # Raw, varied formats are preserved verbatim (no normalization at ingestion).
    assert phones == ["(555) 123-4567", "+1-555-123-4567", "5551234567", "call me"]


def test_csv_blank_cell_yields_omitted_field(tmp_path: Path) -> None:
    path = _write(tmp_path, "candidates.csv", _INLINE_CSV)
    # Row 3 ("P. Sharma") has a blank email cell -> emails omitted, not "".
    third = read_csv(path)[2]
    assert third.emails == []
    assert third.full_name.value == "P. Sharma"


def test_csv_utf8_bom_header_still_populates_full_name(tmp_path: Path) -> None:
    # A UTF-8 BOM at the start of the file would otherwise corrupt the first
    # header into "\ufeffname", so the "name" column would no longer match and
    # every full_name would be None. utf-8-sig in read_csv must strip the BOM.
    path = tmp_path / "bom.csv"
    # Write the inline CSV with a UTF-8 BOM (encoding="utf-8-sig" prepends it).
    path.write_text(_INLINE_CSV, encoding="utf-8-sig")

    records = read_csv(path)
    assert len(records) == 4
    # full_name resolves despite the BOM (header is "name", not "\ufeffname").
    assert records[0].full_name is not None
    assert records[0].full_name.value == "Priya Sharma"
    assert [r.full_name.value for r in records] == [
        "Priya Sharma",
        "Priya Sharma",
        "P. Sharma",
        "Jordan Kim",
    ]


def test_csv_explicit_bom_prefix_header_still_populates_full_name(
    tmp_path: Path,
) -> None:
    # Same guarantee when the BOM is written as a literal "\ufeff" prefix.
    path = tmp_path / "bom_prefix.csv"
    path.write_text("\ufeff" + _INLINE_CSV, encoding="utf-8")

    first = read_csv(path)[0]
    assert first.full_name is not None
    assert first.full_name.value == "Priya Sharma"


def test_csv_missing_file_returns_empty_without_raising(tmp_path: Path) -> None:
    missing = tmp_path / "does_not_exist.csv"
    assert read_csv(missing) == []


def test_csv_empty_file_returns_empty_without_raising(tmp_path: Path) -> None:
    empty = _write(tmp_path, "empty.csv", "")
    assert read_csv(empty) == []


@pytest.mark.skipif(
    not (_DATA_DIR / "candidates.csv").is_file(), reason="sample data not present"
)
def test_csv_sample_file_parses() -> None:
    records = read_csv(_DATA_DIR / "candidates.csv")
    assert len(records) == 8
    assert all(r.source == "recruiter_csv" for r in records)
    # The "Dana Patel" row has a blank company but a title -> still one experience.
    dana = records[-1]
    assert dana.full_name.value == "Dana Patel"
    assert dana.experience[0].title.value == "QA Lead"
    assert dana.experience[0].company is None


# --------------------------------------------------------------------------- #
# Resume adapter
# --------------------------------------------------------------------------- #
def test_resume_name_and_email() -> None:
    rec = parse_resume(_INLINE_RESUME)
    assert rec.source == "resume"
    assert rec.full_name.value == "Priya Sharma"
    assert rec.full_name.extraction_method == "free-text"
    assert any(e.value == "priya.sharma@gmail.com" for e in rec.emails)
    assert all(e.extraction_method == "regex" for e in rec.emails)


def test_resume_phone_is_raw_and_regex() -> None:
    rec = parse_resume(_INLINE_RESUME)
    assert len(rec.phones) == 1
    # Raw phone captured from the "Phone:" label, not normalized.
    assert "555" in rec.phones[0].value
    assert rec.phones[0].extraction_method == "regex"


def test_resume_skills_captured_raw_including_junk() -> None:
    rec = parse_resume(_INLINE_RESUME)
    tokens = [s.value for s in rec.skills]
    # Raw tokens preserved verbatim, including aliases and junk ("Ninjutsu").
    assert tokens == ["py", "JS", "Golang", "k8s", "Docker", "PostgreSQL", "Ninjutsu"]
    assert all(s.source == "resume" and s.extraction_method == "regex" for s in rec.skills)


def test_resume_experience_with_raw_dates() -> None:
    rec = parse_resume(_INLINE_RESUME)
    assert len(rec.experience) == 2

    first = rec.experience[0]
    assert first.company.value == "Acme Corporation"
    assert first.title.value == "Engineering Lead"
    assert first.date_range.value == "Jan 2019 - Mar 2023"  # raw, unparsed
    assert first.company.extraction_method == "free-text"

    second = rec.experience[1]
    assert second.company.value == "Globex Inc"
    assert second.date_range.value == "2016 - 2018"


def test_resume_education_with_raw_year() -> None:
    rec = parse_resume(_INLINE_RESUME)
    assert len(rec.education) == 1
    edu = rec.education[0]
    assert edu.year.value == "2016"
    assert edu.institution.value == "State University"
    assert edu.degree.value.startswith("B.S")
    assert edu.field.value == "Computer Science"
    assert edu.year.extraction_method == "free-text"


def test_resume_github_link_captured() -> None:
    rec = parse_resume(_INLINE_RESUME)
    assert rec.links.github is not None
    assert rec.links.github.value == "github.com/priyasharma"


def test_resume_linkedin_link_when_present() -> None:
    text = _INLINE_RESUME.replace(
        "github.com/priyasharma",
        "github.com/priyasharma | linkedin.com/in/priyasharma",
    )
    rec = parse_resume(text)
    assert rec.links.linkedin is not None
    assert "linkedin.com/in/priyasharma" in rec.links.linkedin.value


def test_resume_location_from_labeled_line() -> None:
    text = _INLINE_RESUME.replace(
        "Engineering Lead, Distributed Systems",
        "Location: San Francisco, CA, USA",
    )
    rec = parse_resume(text)
    assert rec.location.city is not None
    assert rec.location.city.value == "San Francisco"
    assert rec.location.region is not None
    assert rec.location.region.value == "CA"
    assert rec.location.country is not None
    assert rec.location.country.value == "USA"


def test_resume_empty_text_returns_empty_but_valid() -> None:
    rec = parse_resume("")
    assert isinstance(rec, PartialRecord)
    assert rec.source == "resume"
    assert rec.full_name is None
    assert rec.emails == []
    assert rec.skills == []
    assert rec.experience == []
    assert rec.education == []


def test_resume_garbage_text_does_not_raise() -> None:
    rec = parse_resume("!!! \n @@@ \n ???")
    assert rec.source == "resume"
    # No structured sections -> nothing meaningful, but valid and no exception.
    assert rec.skills == []


@pytest.mark.skipif(
    not (_DATA_DIR / "resume.txt").is_file(), reason="sample data not present"
)
def test_resume_sample_file_parses() -> None:
    rec = parse_resume(_DATA_DIR / "resume.txt")
    assert rec.source == "resume"
    assert rec.full_name.value == "Priya Sharma"
    assert any("Ninjutsu" == s.value for s in rec.skills)
    assert len(rec.experience) == 2
