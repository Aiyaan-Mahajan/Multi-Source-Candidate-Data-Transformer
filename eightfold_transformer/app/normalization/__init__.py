"""normalization package: per-field normalizers producing canonical field values.

Deterministic, offline value transformers that turn raw source strings into the
canonical field representations expected by the schema (E.164 phones, ``YYYY-MM``
dates, gazetteer-canonicalized skills, ISO-3166 alpha-2 countries). No ML / no
LLM / no network / no wall-clock or random defaults.

These functions operate on raw string values and return normalized values, so
they are independent of the partial/canonical Pydantic models; downstream
ingestion/merge stages consume their output.
"""

from eightfold_transformer.app.normalization.companies import normalize_company
from eightfold_transformer.app.normalization.dates import normalize_date
from eightfold_transformer.app.normalization.location import normalize_country
from eightfold_transformer.app.normalization.phones import normalize_phone
from eightfold_transformer.app.normalization.skills import (
    is_allowed_skill,
    normalize_skill,
    normalize_skills,
)

__all__ = [
    "normalize_phone",
    "normalize_date",
    "normalize_skill",
    "normalize_skills",
    "is_allowed_skill",
    "normalize_country",
    "normalize_company",
]
