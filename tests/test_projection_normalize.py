"""Gap-fill: projection-time ``normalize`` directive behavior.

The bundled sample configs only ever use boolean ``normalize: true`` (a
documented no-op), so the *string* normalize directives in
``projection/projector.py`` (``E164``/``date``/``canonical``/``country`` and the
list / confidence-wrapper / skill-dict application paths) were previously
unexercised. These deterministic, offline tests drive those directive paths
directly via the public ``project`` API.
"""

from __future__ import annotations

from eightfold_transformer.app.models.schema import (
    CanonicalCandidate,
    Location,
    Skill,
    TrackedValue,
)
from eightfold_transformer.app.projection import ProjectionConfig, project


def _tv(value, source="recruiter_csv", confidence=0.9, method="structured"):
    return TrackedValue(
        value=value, source=source, confidence=confidence, extraction_method=method
    )


def _candidate() -> CanonicalCandidate:
    # Note: canonical schema invariants still hold (phone is E.164, country is
    # ISO alpha-2); skills carry raw alias names so the 'canonical' directive has
    # something to transform.
    return CanonicalCandidate(
        candidate_id=_tv("cand-001", source="derived", method="derived"),
        full_name=_tv("Ada Lovelace"),
        emails=[_tv("ADA@example.com")],
        phones=[_tv("+918492948175", method="regex")],
        location=Location(country=_tv("US", confidence=0.8)),
        skills=[
            Skill(name="py", confidence=0.9, sources=["resume"]),
            Skill(name="k8s", confidence=0.5, sources=["github"]),
        ],
    )


def test_canonical_directive_on_mapped_skill_names() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "skills", "from": "skills[].name", "normalize": "canonical"}]
    )
    result = project(_candidate(), cfg)
    # "py" -> "Python", "k8s" -> "Kubernetes" via the skills gazetteer.
    assert result == {"skills": ["Python", "Kubernetes"]}


def test_e164_directive_on_phone_value() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "phone", "from": "phones[0]", "normalize": "E164"}]
    )
    result = project(_candidate(), cfg)
    assert result == {"phone": "+918492948175"}


def test_date_directive_routes_through_normalizer() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "y", "from": "headline", "normalize": "date"}]
    )
    cand = _candidate()
    cand.headline = _tv("2019")  # a bare year normalizes to YYYY-MM
    result = project(cand, cfg)
    assert result == {"y": "2019-01"}


def test_country_directive_runs() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "c", "from": "location.country", "normalize": "country"}]
    )
    result = project(_candidate(), cfg)
    assert result == {"c": "US"}


def test_canonical_directive_on_confidence_wrapper_value() -> None:
    # include_confidence wraps the value as {"value", "confidence"}; the directive
    # must normalize the *inner* value and leave confidence intact.
    cfg = ProjectionConfig(
        fields=[{"path": "s", "from": "skills[0].name", "normalize": "canonical"}],
        include_confidence=False,
    )
    result = project(_candidate(), cfg)
    assert result == {"s": "Python"}


def test_directive_on_skill_dict_normalizes_name() -> None:
    # Projecting a whole Skill (not skills[].name) yields a dict with "name";
    # the directive should rewrite the name in place.
    cfg = ProjectionConfig(
        fields=[{"path": "skills", "from": "skills", "normalize": "canonical"}],
        include_confidence=True,
    )
    result = project(_candidate(), cfg)
    names = {s["name"] for s in result["skills"]}
    assert names == {"Python", "Kubernetes"}
    # Confidence survived the normalization pass.
    assert all("confidence" in s for s in result["skills"])


def test_boolean_true_directive_is_noop() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "name", "from": "full_name", "normalize": True}]
    )
    assert project(_candidate(), cfg) == {"name": "Ada Lovelace"}


def test_unknown_directive_is_noop() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "name", "from": "full_name", "normalize": "bogus-directive"}]
    )
    assert project(_candidate(), cfg) == {"name": "Ada Lovelace"}


def test_directive_on_missing_value_passes_through_none() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "x", "from": "headline", "normalize": "canonical"}],
        on_missing="null",
    )
    # No headline set -> resolves to None; the directive must return None safely.
    result = project(_candidate(), cfg)
    assert result == {"x": None}


def test_directive_on_email_list_normalizes_each_item() -> None:
    cfg = ProjectionConfig(
        fields=[{"path": "emails", "from": "emails", "normalize": "canonical"}]
    )
    cand = _candidate()
    cand.emails = [_tv("React"), _tv("py")]  # strings the gazetteer recognizes
    result = project(cand, cfg)
    assert result == {"emails": ["React", "Python"]}
