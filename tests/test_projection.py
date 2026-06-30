"""Deterministic, offline tests for the configurable output projection layer.

These tests construct a :class:`CanonicalCandidate` entirely in memory (no I/O,
no network, no clock) and assert that projection honors selecting, renaming
(including nested output paths), TrackedValue unwrapping, the ``[]`` map
operator, the include_confidence / include_provenance toggles, the on_missing
policy, and determinism.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from eightfold_transformer.app.models.schema import (
    CanonicalCandidate,
    Links,
    Location,
    ProvenanceEntry,
    Skill,
    TrackedValue,
)
from eightfold_transformer.app.projection import (
    ProjectionConfig,
    ProjectionError,
    load_config,
    project,
    project_dict,
)

# Repo root = two levels up from this file (tests/ -> repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent


def _tv(value, source="recruiter_csv", confidence=0.9, method="structured"):
    """Build a TrackedValue with sensible provenance defaults."""
    return TrackedValue(
        value=value, source=source, confidence=confidence, extraction_method=method
    )


def _candidate() -> CanonicalCandidate:
    """A fully-populated canonical candidate used across the tests."""
    return CanonicalCandidate(
        candidate_id=_tv("cand-001", source="derived", method="derived"),
        full_name=_tv("Ada Lovelace"),
        emails=[_tv("ada@example.com"), _tv("ada2@example.com", confidence=0.7)],
        phones=[_tv("+918492948175", method="regex")],
        location=Location(country=_tv("US", confidence=0.8)),
        links=Links(github=_tv("https://github.com/ada", confidence=0.95)),
        headline=_tv("Pioneering programmer"),
        years_experience=_tv(12.0, confidence=0.6),
        skills=[
            Skill(name="Python", confidence=0.9, sources=["resume", "github"]),
            Skill(name="Go", confidence=0.5, sources=["github"]),
        ],
        provenance=[
            ProvenanceEntry(field="full_name", source="recruiter_csv", method="structured"),
            ProvenanceEntry(field="emails[0]", source="recruiter_csv", method="structured"),
        ],
        overall_confidence=0.82,
    )


def test_select_and_rename_unwraps_tracked_value() -> None:
    cfg = ProjectionConfig(fields=[{"path": "email", "from": "emails[0]"}])
    result = project(_candidate(), cfg)
    assert result == {"email": "ada@example.com"}


def test_unlisted_fields_are_removed() -> None:
    cfg = ProjectionConfig(fields=[{"path": "name", "from": "full_name"}])
    result = project(_candidate(), cfg)
    assert result == {"name": "Ada Lovelace"}
    assert "emails" not in result
    assert "phones" not in result
    assert "skills" not in result


def test_nested_output_path_creates_object() -> None:
    cfg = ProjectionConfig(
        fields=[
            {"path": "contact.email", "from": "emails[0]"},
            {"path": "contact.phone", "from": "phones[0]"},
        ]
    )
    result = project(_candidate(), cfg)
    assert result == {
        "contact": {"email": "ada@example.com", "phone": "+918492948175"}
    }


def test_default_from_equals_path() -> None:
    cfg = ProjectionConfig(fields=[{"path": "headline"}])
    result = project(_candidate(), cfg)
    assert result == {"headline": "Pioneering programmer"}


def test_dotted_source_and_links() -> None:
    cfg = ProjectionConfig(
        fields=[
            {"path": "country", "from": "location.country"},
            {"path": "github", "from": "links.github"},
        ]
    )
    result = project(_candidate(), cfg)
    assert result == {"country": "US", "github": "https://github.com/ada"}


def test_map_operator_returns_list_of_names() -> None:
    cfg = ProjectionConfig(fields=[{"path": "skill_names", "from": "skills[].name"}])
    result = project(_candidate(), cfg)
    assert result == {"skill_names": ["Python", "Go"]}


def test_emails_whole_list_unwrapped() -> None:
    cfg = ProjectionConfig(fields=[{"path": "emails", "from": "emails"}])
    result = project(_candidate(), cfg)
    assert result == {"emails": ["ada@example.com", "ada2@example.com"]}


def test_include_confidence_false_is_bare_value() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "email", "from": "emails[0]"}], include_confidence=False
    )
    result = project(_candidate(), cfg)
    assert result == {"email": "ada@example.com"}


def test_include_confidence_true_wraps_tracked_value() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "email", "from": "emails[0]"}], include_confidence=True
    )
    result = project(_candidate(), cfg)
    assert result == {"email": {"value": "ada@example.com", "confidence": 0.9}}


def test_include_confidence_true_for_skills() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "skills", "from": "skills"}], include_confidence=True
    )
    result = project(_candidate(), cfg)
    assert result == {
        "skills": [
            {"name": "Python", "sources": ["resume", "github"], "confidence": 0.9},
            {"name": "Go", "sources": ["github"], "confidence": 0.5},
        ]
    }


def test_include_confidence_false_skill_has_no_confidence() -> None:
    cfg = ProjectionConfig(fields=[{"path": "skills", "from": "skills"}])
    result = project(_candidate(), cfg)
    assert result == {
        "skills": [
            {"name": "Python", "sources": ["resume", "github"]},
            {"name": "Go", "sources": ["github"]},
        ]
    }


def test_include_provenance_true_attaches_ledger() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "name", "from": "full_name"}], include_provenance=True
    )
    result = project(_candidate(), cfg)
    assert result == {
        "name": "Ada Lovelace",
        "provenance": [
            {"field": "full_name", "source": "recruiter_csv", "method": "structured"},
            {"field": "emails[0]", "source": "recruiter_csv", "method": "structured"},
        ],
    }


def test_include_provenance_false_omits_ledger() -> None:
    cfg = ProjectionConfig(fields=[{"path": "name", "from": "full_name"}])
    result = project(_candidate(), cfg)
    assert "provenance" not in result


def test_on_missing_null_emits_null() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "second_phone", "from": "phones[1]"}], on_missing="null"
    )
    result = project(_candidate(), cfg)
    assert result == {"second_phone": None}


def test_on_missing_omit_drops_key() -> None:
    cfg = ProjectionConfig(
        fields=[
            {"path": "name", "from": "full_name"},
            {"path": "second_phone", "from": "phones[1]"},
        ],
        on_missing="omit",
    )
    result = project(_candidate(), cfg)
    assert result == {"name": "Ada Lovelace"}


def test_on_missing_error_raises() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "second_phone", "from": "phones[1]"}], on_missing="error"
    )
    with pytest.raises(ProjectionError):
        project(_candidate(), cfg)


def test_required_missing_raises_regardless_of_on_missing() -> None:
    # Empty phones list -> phones[0] is out of range -> MISSING.
    candidate = _candidate()
    candidate_no_phone = candidate.model_copy(update={"phones": []})
    cfg = ProjectionConfig(
        fields=[{"path": "phone", "from": "phones[0]", "required": True}],
        on_missing="null",
    )
    with pytest.raises(ProjectionError):
        project(candidate_no_phone, cfg)


def test_determinism_same_input_same_output_and_order() -> None:
    cfg = ProjectionConfig(
        fields=[
            {"path": "name", "from": "full_name"},
            {"path": "email", "from": "emails[0]"},
            {"path": "country", "from": "location.country"},
        ]
    )
    candidate = _candidate()
    first = project(candidate, cfg)
    second = project(candidate, cfg)
    assert first == second
    assert list(first.keys()) == ["name", "email", "country"]


def test_projector_does_not_mutate_candidate() -> None:
    candidate = _candidate()
    before = candidate.model_dump()
    cfg = ProjectionConfig(fields=[{"path": "email", "from": "emails[0]"}])
    project(candidate, cfg)
    assert candidate.model_dump() == before


def test_project_dict_convenience() -> None:
    result = project_dict(
        _candidate(), {"fields": [{"path": "name", "from": "full_name"}]}
    )
    assert result == {"name": "Ada Lovelace"}


def test_field_spec_from_alias_round_trips() -> None:
    cfg = load_config(
        {"fields": [{"path": "x", "from": "headline"}], "on_missing": "null"}
    )
    assert cfg.fields[0].source == "headline"


def test_example_custom_config_loads() -> None:
    path = _REPO_ROOT / "configs" / "example_custom_config.json"
    if not path.exists():
        pytest.skip("example config not present")
    cfg = load_config(path)
    assert cfg.include_confidence is True
    assert any(f.path == "contact.email" for f in cfg.fields)


def test_default_config_loads() -> None:
    path = _REPO_ROOT / "configs" / "default_config.json"
    if not path.exists():
        pytest.skip("default config not present")
    cfg = load_config(path)
    assert any(f.path == "full_name" for f in cfg.fields)
