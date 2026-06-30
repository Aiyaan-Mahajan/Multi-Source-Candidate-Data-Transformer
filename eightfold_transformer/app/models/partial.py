"""Per-source partial record model (pre-merge ingestion output).

A :class:`PartialRecord` is what a *single* ingestion adapter emits for a single
candidate, *before* normalization and merge. It is the raw, source-local view:

* Every scalar is wrapped in :class:`~eightfold_transformer.app.models.schema.TrackedValue`
  so the value travels with its provenance (``source`` / ``confidence`` /
  ``extraction_method``) exactly as the canonical model expects.
* Values are captured **raw**. Ingestion does *not* normalize: phone numbers are
  kept in whatever shape the source used, dates stay as free text (e.g.
  ``"Jan 2019 - Mar 2023"``), country names are not coerced to ISO codes, and
  skill tokens are kept verbatim (``"py"``, ``"JS"``, ``"k8s"``, and even junk).
  The downstream normalization stage is the *only* place that interprets,
  canonicalizes, or validates these values.
* Unknowns are honestly empty: unknown scalars are ``None`` and unknown
  collections are ``[]``. Adapters never invent values.

Because the partial shape is intentionally permissive (raw, un-validated), it
deliberately does **not** reuse the canonical leaf models that enforce
normalization invariants. In particular it avoids:

* the canonical :class:`Location` country ISO-3166 validator (raw country names
  like ``"USA"`` are legal here),
* the canonical :class:`ExperienceItem` / :class:`EducationItem` ``YYYY-MM`` /
  ``int`` date constraints (raw date strings are legal here),
* the canonical aggregated :class:`Skill` model (skills are still per-source raw
  ``TrackedValue[str]`` tokens, not yet de-duplicated/aggregated).

Instead it defines lightweight partial-specific leaf models below. The single
unifying type with the canonical model is :class:`TrackedValue`, which is
imported (never redefined) so provenance metadata flows through unchanged.

Public surface
--------------
``from eightfold_transformer.app.models.partial import PartialRecord``
"""

from __future__ import annotations

from typing import List, Optional

from pydantic import BaseModel, ConfigDict, Field

# Import (never redefine) the canonical provenance carrier so the metadata shape
# is identical to what the merge/canonical stages consume.
from eightfold_transformer.app.models.schema import TrackedValue

__all__ = [
    "PartialLocation",
    "PartialLinks",
    "PartialExperienceItem",
    "PartialEducationItem",
    "PartialRecord",
]


class PartialLocation(BaseModel):
    """Raw, source-local location components (pre-normalization).

    Mirrors the canonical :class:`Location` shape but intentionally drops the
    ISO-3166 alpha-2 validator on ``country`` because ingestion captures whatever
    the source wrote (``"USA"``, ``"United States"``, ``"Remote"``, ...). The
    normalizer is responsible for coercing these into canonical codes.
    """

    model_config = ConfigDict(extra="forbid")

    city: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw city string as written by the source; None when absent."
    )
    region: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw state/region string; None when absent."
    )
    country: Optional[TrackedValue[str]] = Field(
        default=None,
        description="Raw country string (NOT normalized to an ISO code); None when absent.",
    )


class PartialLinks(BaseModel):
    """Raw external profile links captured by an adapter (pre-normalization).

    URLs are stored exactly as found (e.g. ``"github.com/priyasharma"`` without a
    scheme). Canonicalization to full, validated URLs happens during normalization.
    """

    model_config = ConfigDict(extra="forbid")

    linkedin: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw LinkedIn URL/handle as found; None when absent."
    )
    github: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw GitHub URL/handle as found; None when absent."
    )
    portfolio: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw portfolio/personal-site URL as found; None when absent."
    )
    other: List[TrackedValue[str]] = Field(
        default_factory=list,
        description="Any additional raw links, each with its own provenance.",
    )


class PartialExperienceItem(BaseModel):
    """A single raw employment record captured at ingestion time.

    Unlike the canonical :class:`ExperienceItem`, dates are kept as a single raw
    ``date_range`` string (e.g. ``"Jan 2019 - Mar 2023"`` or ``"2016 - 2018"``)
    rather than being split/parsed into ``YYYY-MM`` ``start``/``end``. The
    normalizer parses ``date_range`` later.
    """

    model_config = ConfigDict(extra="forbid")

    company: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw employer name; None when unknown."
    )
    title: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw role title; None when unknown."
    )
    date_range: Optional[TrackedValue[str]] = Field(
        default=None,
        description="Raw, unparsed date range exactly as written; None when absent.",
    )
    summary: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw free-text summary of the role; None when absent."
    )


class PartialEducationItem(BaseModel):
    """A single raw education record captured at ingestion time.

    Unlike the canonical :class:`EducationItem`, the completion ``year`` is kept
    as a raw string (``year``) rather than a bounded ``int`` ``end_year`` so the
    normalizer can validate/convert it later.
    """

    model_config = ConfigDict(extra="forbid")

    institution: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw school/university name; None when unknown."
    )
    degree: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw degree string, e.g. 'B.S.'; None when unknown."
    )
    field: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw field of study; None when unknown."
    )
    year: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw completion year as written; None when unknown."
    )


class PartialRecord(BaseModel):
    """The raw, single-source output of one ingestion adapter for one candidate.

    Field semantics mirror the canonical :class:`CanonicalCandidate` so a merger
    can line them up, but every value here is **raw and un-normalized** and every
    scalar is wrapped in a :class:`TrackedValue` carrying its provenance. Unknown
    scalars are ``None`` and unknown collections are ``[]`` — adapters never
    fabricate data.

    A ``PartialRecord`` is always constructible with just ``source`` set; that
    "empty-but-valid" form is what fail-soft adapters return when a source yields
    nothing parseable.
    """

    model_config = ConfigDict(extra="forbid")

    source: str = Field(
        description="Identifier of the producing adapter, e.g. 'recruiter_csv' or 'resume'.",
    )
    full_name: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw candidate name as found; None when absent."
    )
    emails: List[TrackedValue[str]] = Field(
        default_factory=list,
        description="Raw email strings (un-normalized), each with provenance.",
    )
    phones: List[TrackedValue[str]] = Field(
        default_factory=list,
        description="Raw phone strings exactly as written (NOT E.164), each with provenance.",
    )
    location: PartialLocation = Field(
        default_factory=PartialLocation,
        description="Raw location components with per-component provenance.",
    )
    links: PartialLinks = Field(
        default_factory=PartialLinks,
        description="Raw external profile links with per-link provenance.",
    )
    headline: Optional[TrackedValue[str]] = Field(
        default=None, description="Raw professional headline/tagline; None when absent."
    )
    years_experience: Optional[TrackedValue[float]] = Field(
        default=None,
        description="Raw total years of experience if a source states it directly; None otherwise.",
    )
    skills: List[TrackedValue[str]] = Field(
        default_factory=list,
        description=(
            "RAW skill tokens exactly as found (e.g. 'py', 'JS', 'k8s'), each with "
            "provenance. NOT canonicalized and NOT the aggregated Skill model; "
            "canonicalization happens later in normalization."
        ),
    )
    experience: List[PartialExperienceItem] = Field(
        default_factory=list,
        description="Raw employment history entries (dates kept as raw strings).",
    )
    education: List[PartialEducationItem] = Field(
        default_factory=list,
        description="Raw education history entries (year kept as a raw string).",
    )
