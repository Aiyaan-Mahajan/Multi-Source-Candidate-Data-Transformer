"""Tests for configurable skill vocabulary and merge-time filtering."""

from __future__ import annotations

from eightfold_transformer.app.merger import merge
from eightfold_transformer.app.models.partial import PartialRecord
from eightfold_transformer.app.models.schema import TrackedValue
from eightfold_transformer.app.normalization import is_allowed_skill, normalize_skill
from eightfold_transformer.app.merger.resolver import build_skills


def test_ninjutsu_not_allowed_python_is():
    assert not is_allowed_skill("Ninjutsu")
    assert is_allowed_skill("py")
    assert normalize_skill("py") == "Python"


def test_build_skills_drops_unknown_tokens():
    rec = PartialRecord(source="resume")
    rec.skills = [
        TrackedValue(value="Python", source="resume", confidence=0.8, extraction_method="regex"),
        TrackedValue(value="Ninjutsu", source="resume", confidence=0.8, extraction_method="regex"),
    ]
    skills = build_skills([rec])
    names = {s.name for s in skills}
    assert "Python" in names
    assert "Ninjutsu" not in names


def test_merge_output_excludes_ninjutsu():
    rec = PartialRecord(source="resume")
    rec.full_name = TrackedValue(
        value="Sam", source="resume", confidence=0.8, extraction_method="regex"
    )
    rec.emails.append(
        TrackedValue(value="sam@x.com", source="resume", confidence=0.8, extraction_method="regex")
    )
    rec.skills = [
        TrackedValue(value="py", source="resume", confidence=0.8, extraction_method="regex"),
        TrackedValue(value="Ninjutsu", source="resume", confidence=0.8, extraction_method="regex"),
    ]
    candidate = merge([rec])[0]
    assert "Python" in {s.name for s in candidate.skills}
    assert "Ninjutsu" not in {s.name for s in candidate.skills}
