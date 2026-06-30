"""Canonical candidate data model for the Multi-Source Candidate Data Transformer.

This module defines the single, authoritative output schema produced after the
pipeline ingests, normalizes, and merges data from heterogeneous sources
(recruiter CSV, ATS JSON, GitHub API, free-text recruiter notes, resume files).

Design principles
-----------------
1. **Full-shaped, never invented.** A :class:`CanonicalCandidate` is always
   emitted with the complete set of fields. Unknown scalars are ``None`` and
   unknown collections are empty lists. The pipeline never fabricates values:
   "wrong-but-confident" output is strictly worse than "honestly-empty", so the
   schema makes emptiness a first-class, valid state.

2. **Provenance is carried, not bolted on.** Every individually extracted field
   needs to answer four questions: *what* is the value, *which source* produced
   it, *how confident* are we, and *by what method* was it extracted. Rather than
   maintaining parallel side-tables, we wrap such fields in a reusable generic
   carrier, :class:`TrackedValue` (``TrackedValue[T]``). The wrapper travels with
   the value through the record, keeping the audit trail local to the data.

3. **Two views of provenance.**
   - *In-record carrier:* :class:`TrackedValue` attaches ``source`` /
     ``confidence`` / ``extraction_method`` to each tracked field (and to each
     element of tracked list fields such as ``emails`` and ``phones``).
   - *Output-facing audit trail:* the top-level ``provenance`` list is the
     flattened, denormalized ledger of ``{field, source, method}`` entries. It is
     derived from the per-field :class:`TrackedValue` metadata and exists so a
     consumer can scan a single list to see where everything came from without
     walking the nested structure. ``overall_confidence`` is the aggregated,
     record-level confidence computed from the per-field confidences.

4. **Determinism.** No randomness and no wall-clock defaults anywhere. Identical
   inputs must yield byte-identical output, so all defaults are static
   (``None`` / empty list) and validation is purely structural.

Public surface
--------------
``from eightfold_transformer.app.models.schema import CanonicalCandidate``
"""

from __future__ import annotations

import re
from typing import Generic, List, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Compiled patterns kept at module scope so validators stay cheap and the rules
# are documented in exactly one place.
_ISO_3166_ALPHA2 = re.compile(r"^[A-Z]{2}$")
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")

__all__ = [
    "ExtractionMethod",
    "TrackedValue",
    "Location",
    "Links",
    "Skill",
    "ExperienceItem",
    "EducationItem",
    "ProvenanceEntry",
    "CanonicalCandidate",
]

# Value carried by a TrackedValue. Generic so the same wrapper works for any
# normalized scalar type (str, int, float, ...).
T = TypeVar("T")

#: Allowed extraction strategies. Constrained to a closed set so downstream
#: tooling can branch on method without guarding against free-form strings.
#: - "structured": value read directly from a structured field (CSV column,
#:   JSON key) with no interpretation.
#: - "regex":      value isolated via a deterministic pattern match.
#: - "free-text":  value pulled from unstructured prose (e.g. recruiter notes).
#: - "derived":    value computed by the pipeline (e.g. a hashed candidate_id).
ExtractionMethod = Literal["structured", "regex", "free-text", "derived"]


class TrackedValue(BaseModel, Generic[T]):
    """Generic provenance-carrying wrapper for a single extracted value.

    ``TrackedValue[T]`` binds a normalized value of type ``T`` to the metadata
    that justifies it. This is the in-record carrier of provenance: instead of
    storing bare scalars and reconstructing their origin elsewhere, every tracked
    field keeps its own source, confidence, and extraction method inline.

    The ``value`` itself is ``Optional`` so a field can be honestly absent while
    still recording *why* it is absent (e.g. a source was consulted but yielded
    nothing). For list fields, each element is its own ``TrackedValue`` so the
    per-value audit trail survives aggregation.
    """

    model_config = ConfigDict(extra="forbid")

    value: Optional[T] = Field(
        default=None,
        description="Normalized value; None when unknown (never fabricated).",
    )
    source: str = Field(
        description="Identifier of the producing source, e.g. 'recruiter_csv'.",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence in this value, constrained to [0.0, 1.0].",
    )
    extraction_method: ExtractionMethod = Field(
        description="How the value was obtained (structured/regex/free-text/derived).",
    )


class Location(BaseModel):
    """Geographic location of the candidate.

    Each component is a :class:`TrackedValue` so the city, region, and country can
    legitimately originate from different sources with different confidences
    (e.g. country parsed from an ATS field, city inferred from free text).
    ``country`` is validated as an ISO-3166 alpha-2 code when present.
    """

    model_config = ConfigDict(extra="forbid")

    city: Optional[TrackedValue[str]] = Field(
        default=None, description="City name; None when unknown."
    )
    region: Optional[TrackedValue[str]] = Field(
        default=None, description="State/province/region; None when unknown."
    )
    country: Optional[TrackedValue[str]] = Field(
        default=None,
        description="ISO-3166 alpha-2 country code (e.g. 'US'); None when unknown.",
    )

    @field_validator("country")
    @classmethod
    def _validate_country_alpha2(
        cls, country: Optional[TrackedValue[str]]
    ) -> Optional[TrackedValue[str]]:
        """Enforce ISO-3166 alpha-2 on the country code when a value is present.

        A ``None`` carrier value is allowed (country honestly unknown); a present
        value must match ``^[A-Z]{2}$``.
        """
        if country is not None and country.value is not None:
            if not _ISO_3166_ALPHA2.fullmatch(country.value):
                raise ValueError(
                    f"country must be an ISO-3166 alpha-2 code (^[A-Z]{{2}}$), "
                    f"got {country.value!r}"
                )
        return country


class Links(BaseModel):
    """Canonical external profile links for the candidate.

    The well-known platforms get dedicated tracked fields; anything else is
    collected in ``other`` so the schema stays full-shaped without inventing
    named slots for arbitrary URLs.
    """

    model_config = ConfigDict(extra="forbid")

    linkedin: Optional[TrackedValue[str]] = Field(
        default=None, description="LinkedIn profile URL; None when unknown."
    )
    github: Optional[TrackedValue[str]] = Field(
        default=None, description="GitHub profile URL; None when unknown."
    )
    portfolio: Optional[TrackedValue[str]] = Field(
        default=None, description="Portfolio/personal site URL; None when unknown."
    )
    other: List[TrackedValue[str]] = Field(
        default_factory=list,
        description="Any additional links, each with its own provenance.",
    )


class Skill(BaseModel):
    """A canonical skill with aggregated confidence and contributing sources.

    Skills intentionally use the assignment's fixed ``{name, confidence,
    sources[]}`` shape rather than :class:`TrackedValue`. A skill is frequently
    corroborated by multiple sources (resume + GitHub + notes), so it carries a
    *list* of contributing sources and a single aggregated confidence instead of
    one source/method pair.
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(description="Canonicalized skill name, e.g. 'Python'.")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Aggregated confidence for this skill, in [0.0, 1.0].",
    )
    sources: List[str] = Field(
        default_factory=list,
        description="Identifiers of all sources that attested this skill.",
    )


class ExperienceItem(BaseModel):
    """A single employment record.

    Dates use the ``YYYY-MM`` granularity mandated by the assignment. ``end`` is
    ``None`` for an ongoing role. Free-form details live in ``summary``.
    """

    model_config = ConfigDict(extra="forbid")

    company: Optional[str] = Field(default=None, description="Employer name.")
    title: Optional[str] = Field(default=None, description="Role title.")
    start: Optional[str] = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}$",
        description="Start month as 'YYYY-MM'; None when unknown.",
    )
    end: Optional[str] = Field(
        default=None,
        pattern=r"^\d{4}-\d{2}$",
        description="End month as 'YYYY-MM'; None when ongoing or unknown.",
    )
    summary: Optional[str] = Field(
        default=None, description="Short free-text summary of the role."
    )


class EducationItem(BaseModel):
    """A single education record (institution, degree, field, completion year)."""

    model_config = ConfigDict(extra="forbid")

    institution: Optional[str] = Field(
        default=None, description="School/university name."
    )
    degree: Optional[str] = Field(
        default=None, description="Degree, e.g. 'BSc', 'MEng'."
    )
    field: Optional[str] = Field(
        default=None, description="Field of study, e.g. 'Computer Science'."
    )
    end_year: Optional[int] = Field(
        default=None,
        ge=1900,
        le=2100,
        description="Graduation/completion year; None when unknown.",
    )


class ProvenanceEntry(BaseModel):
    """One row of the flattened, output-facing provenance audit trail.

    This is the denormalized counterpart to the per-field :class:`TrackedValue`
    metadata: a flat ``{field, source, method}`` ledger that lets a consumer scan
    a single list to see where every value originated, without traversing the
    nested record. The top-level ``provenance`` list is built by flattening the
    in-record :class:`TrackedValue` carriers.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(description="Dotted path of the field, e.g. 'location.city'.")
    source: str = Field(description="Source that produced the value.")
    method: ExtractionMethod = Field(
        description="Extraction method used for the value."
    )


class CanonicalCandidate(BaseModel):
    """The single canonical candidate profile emitted by the pipeline.

    The record is always full-shaped: every field is present, with unknown
    scalars set to ``None`` and unknown collections to ``[]``. Tracked scalar and
    list-element fields carry their own provenance via :class:`TrackedValue`,
    while ``provenance`` provides the flattened audit trail and
    ``overall_confidence`` the aggregate record-level confidence.

    ``candidate_id`` is a deterministic, pipeline-derived identifier (typically a
    stable hash of identifying fields), modeled as a ``TrackedValue[str]`` with
    ``extraction_method='derived'``.
    """

    # Top-level extra fields are forbidden to keep the output schema closed and
    # to catch typos/regressions in producers early.
    model_config = ConfigDict(extra="forbid")

    candidate_id: TrackedValue[str] = Field(
        description="Deterministic candidate identifier (derived; never random)."
    )
    full_name: Optional[TrackedValue[str]] = Field(
        default=None, description="Candidate full name; None when unknown."
    )
    emails: List[TrackedValue[str]] = Field(
        default_factory=list,
        description="Email addresses, each with its own provenance.",
    )
    phones: List[TrackedValue[str]] = Field(
        default_factory=list,
        description="Phone numbers in E.164, each with its own provenance.",
    )

    @field_validator("phones")
    @classmethod
    def _validate_phones_e164(
        cls, phones: List[TrackedValue[str]]
    ) -> List[TrackedValue[str]]:
        """Enforce E.164 formatting on each phone value that is present.

        Carriers whose ``value`` is ``None`` are allowed (the number was tracked
        but not resolved); present values must match ``^\\+[1-9]\\d{6,14}$``.
        """
        for phone in phones:
            if phone.value is not None and not _E164.fullmatch(phone.value):
                raise ValueError(
                    f"phone must be E.164 (^\\+[1-9]\\d{{6,14}}$), "
                    f"got {phone.value!r}"
                )
        return phones
    location: Location = Field(
        default_factory=Location,
        description="Geographic location with per-component provenance.",
    )
    links: Links = Field(
        default_factory=Links,
        description="External profile links with per-link provenance.",
    )
    headline: Optional[TrackedValue[str]] = Field(
        default=None, description="Professional headline/tagline; None when unknown."
    )
    years_experience: Optional[TrackedValue[float]] = Field(
        default=None,
        description="Total years of experience; None when unknown.",
    )
    skills: List[Skill] = Field(
        default_factory=list,
        description="Canonical skills with aggregated confidence and sources.",
    )
    experience: List[ExperienceItem] = Field(
        default_factory=list, description="Employment history."
    )
    education: List[EducationItem] = Field(
        default_factory=list, description="Education history."
    )
    provenance: List[ProvenanceEntry] = Field(
        default_factory=list,
        description="Flattened {field, source, method} audit trail.",
    )
    overall_confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Aggregated record-level confidence in [0.0, 1.0].",
    )
