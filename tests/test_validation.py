"""Unit tests for the validation layer.

Covers :func:`validate_projected` (structural, include_confidence-aware,
dependency-free) and :func:`validate_canonical` (light pydantic re-validation),
plus the strict :func:`assert_valid_projected` wrapper and its error type.

These are hand-built dict + config cases (no pipeline run), so they isolate the
validator's behavior from ingestion/merge/projection.
"""

from __future__ import annotations

import pytest

from eightfold_transformer.app.models.schema import (
    CanonicalCandidate,
    Skill,
    TrackedValue,
)
from eightfold_transformer.app.projection.config import ProjectionConfig
from eightfold_transformer.app.validation import (
    ProjectedValidationError,
    assert_valid_projected,
    expected_schema,
    validate_canonical,
    validate_projected,
)


def _config(fields, *, include_confidence=False, on_missing="null") -> ProjectionConfig:
    """Build a ProjectionConfig from raw field dicts (uses the `from` alias)."""
    return ProjectionConfig.model_validate(
        {
            "fields": fields,
            "include_confidence": include_confidence,
            "on_missing": on_missing,
        }
    )


# --------------------------------------------------------------------------- #
# validate_projected - happy path
# --------------------------------------------------------------------------- #
def test_valid_record_returns_empty():
    config = _config(
        [
            {"path": "candidate_id", "type": "string", "required": True},
            {"path": "emails", "type": "array", "required": False},
            {"path": "links", "type": "object", "required": False},
            {"path": "years_experience", "type": "number", "required": False},
        ]
    )
    record = {
        "candidate_id": "abc123",
        "emails": ["a@x.com"],
        "links": {"github": "https://github.com/x"},
        "years_experience": 4.5,
    }
    assert validate_projected(record, config) == []


def test_nested_dotted_path_validates():
    config = _config(
        [
            {"path": "contact.email", "from": "emails[0]", "type": "string"},
            {"path": "contact.phone", "from": "phones[0]", "type": "string",
             "required": True},
        ]
    )
    record = {"contact": {"email": "a@x.com", "phone": "+14155550000"}}
    assert validate_projected(record, config) == []


# --------------------------------------------------------------------------- #
# validate_projected - failures
# --------------------------------------------------------------------------- #
def test_missing_required_field_returns_error():
    config = _config([{"path": "full_name", "type": "string", "required": True}])
    errors = validate_projected({}, config)
    assert len(errors) == 1
    assert "full_name" in errors[0]
    assert "missing or null" in errors[0]


def test_required_field_present_but_null_returns_error():
    config = _config([{"path": "full_name", "type": "string", "required": True}])
    errors = validate_projected({"full_name": None}, config)
    assert errors and "full_name" in errors[0]


def test_type_mismatch_array_where_string_expected():
    config = _config([{"path": "full_name", "type": "string", "required": True}])
    errors = validate_projected({"full_name": ["not", "a", "string"]}, config)
    assert len(errors) == 1
    assert "expected string" in errors[0]
    assert "list" in errors[0]


def test_type_mismatch_string_where_array_expected():
    config = _config([{"path": "emails", "type": "array"}])
    errors = validate_projected({"emails": "a@x.com"}, config)
    assert errors and "expected array" in errors[0]


# --------------------------------------------------------------------------- #
# validate_projected - include_confidence-aware
# --------------------------------------------------------------------------- #
def test_confidence_wrapped_scalar_validates():
    config = _config(
        [{"path": "full_name", "type": "string", "required": True}],
        include_confidence=True,
    )
    record = {"full_name": {"value": "Priya Sharma", "confidence": 0.92}}
    assert validate_projected(record, config) == []


def test_confidence_wrapped_scalar_inner_type_mismatch():
    config = _config(
        [{"path": "full_name", "type": "string"}],
        include_confidence=True,
    )
    # Inner value is a list -> still a type error after unwrapping.
    record = {"full_name": {"value": [1, 2], "confidence": 0.5}}
    errors = validate_projected(record, config)
    assert errors and "expected string" in errors[0]


def test_confidence_wrapped_required_with_null_inner_is_error():
    config = _config(
        [{"path": "full_name", "type": "string", "required": True}],
        include_confidence=True,
    )
    record = {"full_name": {"value": None, "confidence": 0.5}}
    errors = validate_projected(record, config)
    assert errors and "full_name" in errors[0]


def test_confidence_wrapped_array_elements_pass_as_array():
    config = _config(
        [{"path": "emails", "type": "array"}],
        include_confidence=True,
    )
    # Lists of wrappers are still lists; the array check looks at the container.
    record = {"emails": [{"value": "a@x.com", "confidence": 0.9}]}
    assert validate_projected(record, config) == []


# --------------------------------------------------------------------------- #
# validate_projected - lenient on legal absence/null
# --------------------------------------------------------------------------- #
def test_non_required_null_under_on_missing_null_is_ok():
    config = _config(
        [{"path": "headline", "type": "string", "required": False}],
        on_missing="null",
    )
    assert validate_projected({"headline": None}, config) == []


def test_non_required_absent_key_is_ok():
    config = _config(
        [{"path": "headline", "type": "string", "required": False}],
        on_missing="omit",
    )
    assert validate_projected({}, config) == []


def test_unknown_declared_type_skips_type_check():
    config = _config([{"path": "weird", "type": "geo"}])
    # Any present value is accepted because the type is not in the type map.
    assert validate_projected({"weird": 123}, config) == []
    assert validate_projected({"weird": "x"}, config) == []


def test_non_dict_record_is_rejected():
    config = _config([{"path": "full_name", "type": "string"}])
    errors = validate_projected(["not", "a", "dict"], config)  # type: ignore[arg-type]
    assert errors and "must be a dict" in errors[0]


# --------------------------------------------------------------------------- #
# expected_schema
# --------------------------------------------------------------------------- #
def test_expected_schema_maps_types_and_flags():
    config = _config(
        [
            {"path": "candidate_id", "type": "string", "required": True},
            {"path": "emails", "type": "array"},
            {"path": "mystery", "type": None},
        ],
        include_confidence=True,
    )
    schema = expected_schema(config)
    assert schema.include_confidence is True
    by_path = {f.path: f for f in schema.fields}
    assert by_path["candidate_id"].py_type == (str,)
    assert by_path["candidate_id"].required is True
    assert by_path["emails"].py_type == (list,)
    assert by_path["mystery"].py_type is None


# --------------------------------------------------------------------------- #
# assert_valid_projected (strict)
# --------------------------------------------------------------------------- #
def test_assert_valid_projected_raises_on_invalid():
    config = _config([{"path": "full_name", "type": "string", "required": True}])
    with pytest.raises(ProjectedValidationError) as exc_info:
        assert_valid_projected({}, config)
    assert exc_info.value.errors
    assert "full_name" in str(exc_info.value)


def test_assert_valid_projected_passes_on_valid():
    config = _config([{"path": "full_name", "type": "string", "required": True}])
    assert_valid_projected({"full_name": "Sam"}, config)  # no raise


# --------------------------------------------------------------------------- #
# validate_canonical
# --------------------------------------------------------------------------- #
def _candidate() -> CanonicalCandidate:
    return CanonicalCandidate(
        candidate_id=TrackedValue(
            value="cand-1", source="derived", confidence=1.0,
            extraction_method="derived",
        ),
        full_name=TrackedValue(
            value="Priya Sharma", source="recruiter_csv", confidence=0.9,
            extraction_method="structured",
        ),
        emails=[
            TrackedValue(
                value="priya@x.com", source="recruiter_csv", confidence=0.9,
                extraction_method="structured",
            )
        ],
        skills=[Skill(name="Python", confidence=0.8, sources=["resume"])],
        overall_confidence=0.9,
    )


def test_validate_canonical_roundtrips_valid_candidate():
    assert validate_canonical(_candidate()) == []


def test_validate_canonical_rejects_non_candidate():
    errors = validate_canonical({"candidate_id": "x"})  # type: ignore[arg-type]
    assert errors and "CanonicalCandidate" in errors[0]
