"""validation package: structural validation of canonical and projected records.

Dependency-free validation layer. Projected output is validated in pure Python
against the expected shape derived from a :class:`ProjectionConfig`; canonical
records are re-validated via pydantic (which enforces the domain invariants).

Public surface
--------------
``validate_projected`` / ``assert_valid_projected`` - projected-dict checks.
``validate_canonical`` - canonical-record re-validation.
``ProjectedValidationError`` - strict-mode error type.
``expected_schema`` / ``ExpectedSchema`` / ``ExpectedField`` - shape derivation.
"""

from eightfold_transformer.app.validation.schemas import (
    ExpectedField,
    ExpectedSchema,
    expected_schema,
)
from eightfold_transformer.app.validation.validator import (
    ProjectedValidationError,
    assert_valid_projected,
    validate_canonical,
    validate_projected,
)

__all__ = [
    "ExpectedField",
    "ExpectedSchema",
    "expected_schema",
    "ProjectedValidationError",
    "assert_valid_projected",
    "validate_canonical",
    "validate_projected",
]
