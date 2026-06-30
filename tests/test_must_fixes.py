"""Deterministic regression tests for three must-fix correctness bugs.

These tests are pure and network-free. They lock in:

* Bug 2 (skills): known aliases canonicalize; unknown tokens normalize trimmed but
  are filtered out at merge time via the skills vocabulary.
* Bug 3 (legacy ``configs/default_config.json`` source path): assert that the
  ``location_country`` field now resolves from the nested canonical
  ``location.country`` path.
"""

from __future__ import annotations

from pathlib import Path

from eightfold_transformer.app.models.schema import (
    CanonicalCandidate,
    Location,
    TrackedValue,
)
from eightfold_transformer.app.merger.resolver import build_skills
from eightfold_transformer.app.models.partial import PartialRecord
from eightfold_transformer.app.normalization.skills import (
    is_allowed_skill,
    normalize_skill,
    normalize_skills,
)
from eightfold_transformer.app.projection import load_config, project

_CONFIG_PATH = Path(__file__).resolve().parents[1] / "configs" / "default_config.json"


def test_normalize_skill_canonicalizes_known_alias() -> None:
    assert normalize_skill("ReactJS") == "React"
    assert normalize_skill("react.js") == "React"
    assert normalize_skill("k8s") == "Kubernetes"


def test_normalize_skill_passes_unknown_through_trimmed() -> None:
    assert normalize_skill("Ninjutsu") == "Ninjutsu"
    assert normalize_skill("  Rust  ") == "Rust"
    assert not is_allowed_skill("Ninjutsu")
    assert is_allowed_skill("Rust")


def test_merge_stage_drops_unknown_skills() -> None:
    rec = PartialRecord(source="resume")
    rec.skills = [
        TrackedValue(value="py", source="resume", confidence=0.8, extraction_method="regex"),
        TrackedValue(value="Ninjutsu", source="resume", confidence=0.8, extraction_method="regex"),
    ]
    names = {s.name for s in build_skills([rec])}
    assert "Python" in names
    assert "Ninjutsu" not in names


def test_normalize_skills_preserves_order_for_known_aliases() -> None:
    assert normalize_skills(["ReactJS", "py", "Ninjutsu", "Rust"]) == [
        "React",
        "Python",
        "Ninjutsu",
        "Rust",
    ]


# --------------------------------------------------------------------------- #
# Bug 3 — default_config.json location_country resolves from location.country
# --------------------------------------------------------------------------- #
def _candidate_with_country(country_code: str) -> CanonicalCandidate:
    """Build a minimal canonical candidate with a populated nested country."""
    return CanonicalCandidate(
        candidate_id=TrackedValue[str](
            value="cand-1",
            source="test",
            confidence=1.0,
            extraction_method="derived",
        ),
        location=Location(
            country=TrackedValue[str](
                value=country_code,
                source="ats_json",
                confidence=0.9,
                extraction_method="structured",
            )
        ),
    )


def test_default_config_resolves_nested_country() -> None:
    candidate = _candidate_with_country("US")
    config = load_config(_CONFIG_PATH)

    out = project(candidate, config)

    # The flat output key is preserved, but it now resolves from the nested
    # canonical path location.country (previously the broken "location_country"
    # source path never resolved and always emitted null).
    assert out["location_country"] == "US"


def test_default_config_country_absent_emits_null() -> None:
    # Sanity: when country is absent, the (non-required) field is null, not an
    # error — proving the fix only changes resolution, not on_missing behavior.
    candidate = CanonicalCandidate(
        candidate_id=TrackedValue[str](
            value="cand-2",
            source="test",
            confidence=1.0,
            extraction_method="derived",
        )
    )
    config = load_config(_CONFIG_PATH)

    out = project(candidate, config)

    assert out["location_country"] is None
