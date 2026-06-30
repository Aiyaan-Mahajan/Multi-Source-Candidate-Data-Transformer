"""Tests for company name normalization."""

from __future__ import annotations

import pytest

from eightfold_transformer.app.merger.resolver import build_experience
from eightfold_transformer.app.models.partial import PartialExperienceItem, PartialRecord
from eightfold_transformer.app.models.schema import TrackedValue
from eightfold_transformer.app.normalization import normalize_company
from eightfold_transformer.app.normalization.companies import companies_equivalent, company_match_key


@pytest.mark.parametrize(
    "left,right",
    [
        ("Acme Corp", "Acme Corporation"),
        ("Globex Inc", "globex inc"),
        ("Initech LLC", "initech llc"),
    ],
)
def test_company_variants_equivalent(left: str, right: str) -> None:
    assert companies_equivalent(left, right)
    assert company_match_key(left) == company_match_key(right)


def test_normalize_company_prefers_corporation_suffix() -> None:
    assert normalize_company("Acme Corp") == "Acme Corporation"
    assert normalize_company("Acme Corporation") == "Acme Corporation"


def test_build_experience_merges_same_company() -> None:
    rec = PartialRecord(source="recruiter_csv")
    rec.experience = [
        PartialExperienceItem(
            company=TrackedValue(
                value="Acme Corp",
                source="recruiter_csv",
                confidence=0.9,
                extraction_method="structured",
            ),
            title=TrackedValue(
                value="Backend Engineer",
                source="recruiter_csv",
                confidence=0.8,
                extraction_method="structured",
            ),
            date_range=TrackedValue(
                value="2018 - 2020",
                source="recruiter_csv",
                confidence=0.7,
                extraction_method="structured",
            ),
        ),
        PartialExperienceItem(
            company=TrackedValue(
                value="Acme Corporation",
                source="resume",
                confidence=0.95,
                extraction_method="regex",
            ),
            title=TrackedValue(
                value="Senior Backend Engineer",
                source="resume",
                confidence=0.95,
                extraction_method="regex",
            ),
            date_range=TrackedValue(
                value="2020 - 2023",
                source="resume",
                confidence=0.7,
                extraction_method="regex",
            ),
        ),
    ]
    items = build_experience([rec])
    assert len(items) == 1
    assert items[0].company == "Acme Corporation"
    assert items[0].title == "Senior Backend Engineer"
    assert items[0].start == "2018-01"
    assert items[0].end == "2023-01"
