"""Canonical candidate profile model.

Responsibility (no logic yet):
- Define the single canonical candidate profile produced after merge.
- Intended canonical fields (to be defined as a pydantic model):
  - full_name: str
  - emails: list[str]
  - phones: list[str]          # normalized to E.164
  - location_country: str      # ISO-3166 alpha-2
  - location_raw: str | None
  - skills: list[str]          # canonical skill names
  - education: list[...]       # institution + dates (YYYY-MM)
  - experience: list[...]      # employer/title + dates (YYYY-MM)
  - links: dict                # e.g. github, linkedin
  - per-field confidence + provenance (attached / parallel structures)

The concrete Pydantic v2 model now lives in ``schema.py``. This module re-exports
it so existing/expected import paths keep working and there is a single source of
truth for the canonical shape.
"""

from eightfold_transformer.app.models.schema import (
    CanonicalCandidate,
    EducationItem,
    ExperienceItem,
    Links,
    Location,
    ProvenanceEntry,
    Skill,
    TrackedValue,
)

# Backwards/forwards-compatible alias: the model was historically referred to as
# "CanonicalProfile" in the design notes.
CanonicalProfile = CanonicalCandidate

__all__ = [
    "CanonicalCandidate",
    "CanonicalProfile",
    "TrackedValue",
    "Location",
    "Links",
    "Skill",
    "ExperienceItem",
    "EducationItem",
    "ProvenanceEntry",
]
