"""Structural validation of canonical and projected records.

This is the project's validation layer. It is deliberately **dependency-free**
(no ``jsonschema``): projected-output validation is performed in pure Python
against the expected shape derived from the :class:`ProjectionConfig` (see
:mod:`eightfold_transformer.app.validation.schemas`), and canonical-record
validation simply re-runs pydantic, which already enforces the domain invariants
(E.164 phones, ISO-3166 alpha-2 country, ``YYYY-MM`` dates, confidence bounds).

Public surface
--------------
* :func:`validate_projected` - list human-readable errors for a projected dict.
* :func:`validate_canonical` - light pydantic re-validation of a candidate.
* :func:`assert_valid_projected` - strict variant raising
  :class:`ProjectedValidationError`.

Design notes
-----------
* **Lenient where projection legitimately emits null.** Under
  ``on_missing="null"`` a non-required field is emitted as ``None``; that is a
  valid, typed-as-absent state and is not flagged. A non-required field whose key
  is absent entirely (``on_missing="omit"``) is likewise fine. Only ``required``
  fields must be present and non-null.
* **``include_confidence`` aware.** When the config wraps tracked scalars as
  ``{"value": ..., "confidence": ...}``, type checks (and the required/non-null
  check) look through the wrapper at the inner ``value``. Container types
  (``array``/``object``) are checked on the outer value and never unwrapped.
"""

from __future__ import annotations

from typing import Any, List

from pydantic import ValidationError

from eightfold_transformer.app.models.schema import CanonicalCandidate
from eightfold_transformer.app.projection.config import ProjectionConfig
from eightfold_transformer.app.validation.schemas import ExpectedSchema, expected_schema

__all__ = [
    "ProjectedValidationError",
    "validate_projected",
    "validate_canonical",
    "assert_valid_projected",
]


class ProjectedValidationError(ValueError):
    """Raised by :func:`assert_valid_projected` when a record fails validation.

    Carries the full list of human-readable error strings on ``.errors`` so
    strict callers can report each problem, while the exception message is the
    same list joined with ``"; "``.
    """

    def __init__(self, errors: List[str]) -> None:
        self.errors: List[str] = list(errors)
        super().__init__("; ".join(self.errors) or "projected record is invalid")


# Sentinel that distinguishes "key absent" from "key present with value None".
class _Absent:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<ABSENT>"


_ABSENT = _Absent()


def _dig(record: Any, dotted_path: str) -> Any:
    """Read a dotted output path from ``record`` or return the ``_ABSENT`` sentinel.

    A returned ``None`` means the key was present and explicitly null (a legal
    ``on_missing="null"`` state); ``_ABSENT`` means the key did not exist at all.
    """
    cursor: Any = record
    for part in dotted_path.split("."):
        if part == "":
            continue
        if isinstance(cursor, dict) and part in cursor:
            cursor = cursor[part]
        else:
            return _ABSENT
    return cursor


def _is_confidence_wrapper(value: Any) -> bool:
    """True when ``value`` looks like a projector confidence wrapper dict."""
    return isinstance(value, dict) and "value" in value and "confidence" in value


def _unwrap(value: Any, include_confidence: bool) -> Any:
    """Return the inner scalar of a confidence wrapper, else ``value`` unchanged.

    Only unwraps when ``include_confidence`` is in effect *and* ``value`` is a
    ``{"value": ..., "confidence": ...}`` dict, so plain ``object`` fields and
    lists pass through untouched.
    """
    if include_confidence and _is_confidence_wrapper(value):
        return value["value"]
    return value


def validate_projected(record: dict, config: ProjectionConfig) -> List[str]:
    """Validate a projected ``record`` against the shape implied by ``config``.

    Parameters
    ----------
    record:
        A single projected dict (as produced by
        :func:`eightfold_transformer.app.projection.projector.project`).
    config:
        The projection config the record was produced with.

    Returns
    -------
    list[str]
        Human-readable validation errors. An empty list means the record is
        valid. Checks performed:

        * Each ``required`` field is present and non-null (confidence-wrapper
          aware).
        * Each present, non-null value matches its declared type (mapping
          ``string/array/object/number/boolean``; unknown/omitted -> skipped).
        * Non-required fields that are null (``on_missing="null"``) or absent
          (``on_missing="omit"``) are accepted.
    """
    if not isinstance(record, dict):
        return [f"projected record must be a dict, got {type(record).__name__}"]

    schema: ExpectedSchema = expected_schema(config)
    errors: List[str] = []

    for expected in schema.fields:
        raw = _dig(record, expected.path)
        present = raw is not _ABSENT
        effective = _unwrap(raw, schema.include_confidence) if present else _ABSENT

        if expected.required and (not present or effective is None):
            errors.append(
                f"required field {expected.path!r} is missing or null"
            )
            continue

        # Non-required absent (omit) or null (null policy) is a legal state.
        if not present or effective is None or raw is None:
            continue

        if expected.py_type is not None and not isinstance(effective, expected.py_type):
            errors.append(
                f"field {expected.path!r}: expected {expected.type_name}, got "
                f"{type(effective).__name__}"
            )

    return errors


def assert_valid_projected(record: dict, config: ProjectionConfig) -> None:
    """Strict variant of :func:`validate_projected`.

    Raises
    ------
    ProjectedValidationError
        If ``record`` has any validation errors under ``config``.
    """
    errors = validate_projected(record, config)
    if errors:
        raise ProjectedValidationError(errors)


def validate_canonical(candidate: CanonicalCandidate) -> List[str]:
    """Light validation of a canonical candidate.

    Confirms ``candidate`` is a :class:`CanonicalCandidate` and re-validates it
    through pydantic by reconstructing it from its own dump. The model already
    enforces the domain invariants (E.164, ISO-3166, ``YYYY-MM``, confidence
    bounds), so any failure here surfaces those as readable messages.

    Returns
    -------
    list[str]
        Human-readable validation errors; empty when valid.
    """
    if not isinstance(candidate, CanonicalCandidate):
        return [
            "expected a CanonicalCandidate, got "
            f"{type(candidate).__name__}"
        ]
    try:
        type(candidate)(**candidate.model_dump())
    except ValidationError as exc:
        messages: List[str] = []
        for err in exc.errors():
            loc = ".".join(str(part) for part in err.get("loc", ()))
            msg = err.get("msg", "invalid")
            messages.append(f"{loc}: {msg}" if loc else msg)
        return messages or [str(exc)]
    return []
