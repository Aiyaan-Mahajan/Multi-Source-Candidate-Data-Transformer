"""Resume (plain-text) ingestion adapter (unstructured source).

Deterministically parses a plain-text resume into a single :class:`PartialRecord`
using rules and regular expressions only — **no LLM, no network, no ML**. One
resume corresponds to one candidate.

Scope
-----
This file handles ``.txt`` prose (and raw text passed directly). PDF/DOCX text
extraction is intentionally out of scope here and remains a placeholder.

Boundary
--------
Extract-only, raw capture. The parser never normalizes:

* emails/phones are isolated by regex and stored verbatim,
* skill tokens are captured **raw** (``"py"``, ``"JS"``, ``"k8s"``, and junk like
  ``"Ninjutsu"``) — unknown tokens are *kept*, not dropped; the normalizer
  decides what to canonicalize or discard,
* experience/education dates are kept as raw strings
  (e.g. ``"Jan 2019 - Mar 2023"``, ``"2016 - 2018"``, ``"2016"``).

Determinism
-----------
Confidence is a fixed per-method constant (``structured`` > ``regex`` >
``free-text``); there is no randomness and no wall-clock use, so identical input
always yields identical output.

Fail-soft
---------
Empty or garbage text (and missing files) yield an empty-but-valid
``PartialRecord(source="resume")`` rather than raising.

Public surface
--------------
``from eightfold_transformer.app.ingestion.resume_parser import parse_resume``
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List, Optional, Union

from eightfold_transformer.app.models.partial import (
    PartialEducationItem,
    PartialExperienceItem,
    PartialRecord,
)
from eightfold_transformer.app.models.schema import ExtractionMethod, TrackedValue

#: Stable identifier attached to every value produced by this adapter.
SOURCE_NAME = "resume"

# Fixed per-method confidence constants. Ordered structured > regex > free-text
# so downstream merge logic can trust the relative reliability, and so output is
# fully reproducible.
REGEX_CONFIDENCE = 0.75
FREE_TEXT_CONFIDENCE = 0.5

# --- Compiled patterns (module scope: cheap, and rules documented in one place).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# A phone-ish run of digits/separators with at least ~7 digits worth of body.
_PHONE_RE = re.compile(
    r"(?:\+?\d{1,3}[\s.\-]?)?(?:\(?\d{2,4}\)?[\s.\-]?)?\d{3}[\s.\-]?\d{4}\b"
)
# Explicit contact labels (Phone, Mobile, Tel, Cell).
_PHONE_LABEL_RE = re.compile(
    r"(?:Phone|Mobile|Tel|Cell(?:phone)?)\s*:?\s*([^|\n]+)", re.IGNORECASE
)
# Standalone international / US formats in header lines.
_PHONE_INLINE_RE = re.compile(
    r"(?<!\w)(?:\+?\d{1,3}[\s.\-]?)?\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}(?!\w)"
)
_NAME_LABEL_RE = re.compile(r"Name\s*:?\s*(.+)", re.IGNORECASE)
_LOCATION_LABEL_RE = re.compile(
    r"(?:Location|Address|Based in)\s*:?\s*([^|\n]+)", re.IGNORECASE
)
_LOCATION_INLINE_RE = re.compile(
    r"\b([A-Za-z .\-]+,\s*[A-Za-z .\-]+(?:,\s*[A-Za-z .\-]+)?)\b"
)
_GITHUB_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/[A-Za-z0-9_\-]+/?", re.IGNORECASE
)
_LINKEDIN_RE = re.compile(
    r"(?:https?://)?(?:www\.)?linkedin\.com/in/[A-Za-z0-9_\-]+/?", re.IGNORECASE
)
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
# Leading degree token, e.g. "B.S.", "BSc", "M.S", "Ph.D", "B.Tech".
_DEGREE_RE = re.compile(
    r"^(B\.?S\.?c?|M\.?S\.?c?|B\.?A\.?|M\.?A\.?|Ph\.?\s*D\.?|B\.?Eng|M\.?Eng|"
    r"B\.?Tech|M\.?Tech|MBA)\b\.?",
    re.IGNORECASE,
)

# Section headers we recognize. Matching is done on an upper-cased, stripped line
# so "SKILLS", "Skills", and "skills" all match.
_SECTION_ALIASES = {
    "SUMMARY": "summary",
    "SKILLS": "skills",
    "TECHNOLOGIES": "skills",
    "TECHNICAL SKILLS": "skills",
    "TOOLS": "skills",
    "FRAMEWORKS": "skills",
    "EXPERIENCE": "experience",
    "WORK EXPERIENCE": "experience",
    "EMPLOYMENT": "experience",
    "EDUCATION": "education",
    "ACADEMIC BACKGROUND": "education",
}


def _tv(value: str, method: ExtractionMethod, confidence: float) -> TrackedValue[str]:
    """Build a provenance-carrying TrackedValue for the resume source."""
    return TrackedValue[str](
        value=value,
        source=SOURCE_NAME,
        confidence=confidence,
        extraction_method=method,
    )


def _load_text(path_or_text: Union[str, Path]) -> str:
    """Resolve the input to text.

    Accepts a :class:`~pathlib.Path` (read the file), a string that names an
    existing file (read it), or a raw resume string (used as-is). Missing/
    unreadable files degrade to ``""`` rather than raising.
    """
    if isinstance(path_or_text, Path):
        try:
            return path_or_text.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    text = path_or_text
    # Only treat short, single-line strings as candidate file paths; a real
    # resume body has newlines and won't be mistaken for a path.
    if "\n" not in text and len(text) <= 260:
        try:
            candidate = Path(text)
            if candidate.is_file():
                return candidate.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return text
    return text


def _section_key(line: str) -> Optional[str]:
    """Return the normalized section key if ``line`` is a known header, else None."""
    return _SECTION_ALIASES.get(line.strip().upper())


def _split_sections(lines: List[str]) -> dict:
    """Group lines into a {section_key: [lines]} map plus a 'header' preamble.

    Everything before the first recognized section header is collected under the
    synthetic key ``"header"`` (name / headline / contact line live there).
    """
    sections: dict = {"header": []}
    current = "header"
    for line in lines:
        key = _section_key(line)
        if key is not None:
            current = key
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _extract_name_and_headline(header_lines: List[str], full_text: str, record: PartialRecord) -> None:
    """Populate ``full_name`` (and best-effort ``headline``) from the preamble.

    Prefers an explicit ``Name:`` label (regex, higher confidence); otherwise
    falls back to the first non-empty preamble line as a heuristic (free-text,
    lower confidence).
    """
    label_match = _NAME_LABEL_RE.search(full_text)
    if label_match:
        name = label_match.group(1).strip()
        if name:
            record.full_name = _tv(name, "regex", REGEX_CONFIDENCE)
            return

    non_empty = [ln.strip() for ln in header_lines if ln.strip()]
    if not non_empty:
        return
    # First preamble line is the name heuristic; skip lines that are obviously a
    # contact line (contain '@' or 'Phone').
    name_idx = None
    for idx, ln in enumerate(non_empty):
        if "@" in ln or _PHONE_LABEL_RE.search(ln):
            continue
        record.full_name = _tv(ln, "free-text", FREE_TEXT_CONFIDENCE)
        name_idx = idx
        break

    # The next plausible line (not contact, not the name) is treated as headline.
    if name_idx is not None:
        for ln in non_empty[name_idx + 1 :]:
            if "@" in ln or _PHONE_LABEL_RE.search(ln) or _GITHUB_RE.search(ln):
                continue
            record.headline = _tv(ln, "free-text", FREE_TEXT_CONFIDENCE)
            break


def _extract_contacts(full_text: str, header_lines: List[str], record: PartialRecord) -> None:
    """Extract emails, phone, links, and location via regex (raw, un-normalized)."""
    for email in _EMAIL_RE.findall(full_text):
        record.emails.append(_tv(email, "regex", REGEX_CONFIDENCE))

    phone_raw: Optional[str] = None
    label = _PHONE_LABEL_RE.search(full_text)
    if label:
        segment = label.group(1).strip()
        match = _PHONE_RE.search(segment) or _PHONE_INLINE_RE.search(segment)
        phone_raw = match.group(0).strip() if match else None
    if phone_raw is None:
        for line in header_lines:
            if "@" in line:
                line = _EMAIL_RE.sub(" ", line)
            match = _PHONE_INLINE_RE.search(line) or _PHONE_RE.search(line)
            if match:
                phone_raw = match.group(0).strip()
                break
    if phone_raw:
        record.phones.append(_tv(phone_raw, "regex", REGEX_CONFIDENCE))

    github = _GITHUB_RE.search(full_text)
    if github:
        record.links.github = _tv(github.group(0).rstrip("/"), "regex", REGEX_CONFIDENCE)
    linkedin = _LINKEDIN_RE.search(full_text)
    if linkedin:
        record.links.linkedin = _tv(linkedin.group(0).rstrip("/"), "regex", REGEX_CONFIDENCE)

    _extract_location(full_text, header_lines, record)


def _extract_location(full_text: str, header_lines: List[str], record: PartialRecord) -> None:
    """Best-effort city/region/country from labeled or header contact lines."""
    loc_text: Optional[str] = None
    label = _LOCATION_LABEL_RE.search(full_text)
    if label:
        loc_text = label.group(1).strip()
    if not loc_text:
        for line in header_lines[:6]:
            if "@" in line or _PHONE_LABEL_RE.search(line):
                continue
            inline = _LOCATION_INLINE_RE.search(line)
            if inline:
                loc_text = inline.group(1).strip()
                break
    if not loc_text:
        return

    parts = [p.strip() for p in loc_text.split(",") if p.strip()]
    if len(parts) >= 3:
        record.location.city = _tv(parts[0], "regex", REGEX_CONFIDENCE)
        record.location.region = _tv(parts[1], "regex", REGEX_CONFIDENCE)
        record.location.country = _tv(parts[2], "regex", REGEX_CONFIDENCE)
    elif len(parts) == 2:
        record.location.city = _tv(parts[0], "regex", REGEX_CONFIDENCE)
        record.location.region = _tv(parts[1], "regex", REGEX_CONFIDENCE)
    elif len(parts) == 1:
        record.location.city = _tv(parts[0], "regex", REGEX_CONFIDENCE)


def _extract_skills(skill_lines: List[str], record: PartialRecord) -> None:
    """Capture raw skill tokens only from dedicated skills/technology sections."""
    for line in skill_lines:
        stripped = line.strip()
        if not stripped:
            continue
        # Skip subsection labels that are not skills themselves.
        if stripped.endswith(":") and len(stripped.split()) <= 3:
            continue
        for token in re.split(r"[,;|/]", stripped):
            token = token.strip(" •-\t")
            if token and not token.endswith(":"):
                record.skills.append(_tv(token, "regex", REGEX_CONFIDENCE))


def _looks_like_date(line: str) -> bool:
    """Heuristic: a line is a date range if it contains a 4-digit year."""
    return bool(_YEAR_RE.search(line))


def _extract_experience(exp_lines: List[str], record: PartialRecord) -> None:
    """Parse the EXPERIENCE section into raw :class:`PartialExperienceItem` entries.

    Heuristic state machine over the section lines:

    * a non-indented ``"Company - Title"`` line (has ``" - "`` and *no* year)
      starts a new entry,
    * a line containing a 4-digit year becomes that entry's raw ``date_range``,
    * any remaining indented/continuation text is appended to ``summary``.
    """
    current: Optional[PartialExperienceItem] = None
    summary_parts: List[str] = []

    def _flush() -> None:
        nonlocal current, summary_parts
        if current is not None:
            if summary_parts:
                current.summary = _tv(
                    " ".join(summary_parts).strip(), "free-text", FREE_TEXT_CONFIDENCE
                )
            record.experience.append(current)
        current = None
        summary_parts = []

    for raw_line in exp_lines:
        line = raw_line.strip()
        if not line:
            continue

        if _looks_like_date(line):
            # A line carrying a 4-digit year is the current entry's date range
            # (covers "Jan 2019 - Mar 2023" and "2016 - 2018" alike).
            if current is None:
                current = PartialExperienceItem()
            current.date_range = _tv(line, "free-text", FREE_TEXT_CONFIDENCE)
            continue

        if " - " in line:
            # New "Company - Title" header (no year present).
            _flush()
            company, _, title = line.partition(" - ")
            current = PartialExperienceItem(
                company=_tv(company.strip(), "free-text", FREE_TEXT_CONFIDENCE)
                if company.strip()
                else None,
                title=_tv(title.strip(), "free-text", FREE_TEXT_CONFIDENCE)
                if title.strip()
                else None,
            )
            continue

        # Otherwise: continuation / summary text.
        if current is None:
            current = PartialExperienceItem()
        summary_parts.append(line)

    _flush()


def _extract_education(edu_lines: List[str], record: PartialRecord) -> None:
    """Parse the EDUCATION section into raw :class:`PartialEducationItem` entries."""
    current_lines: List[str] = []

    def _flush_block() -> None:
        if not current_lines:
            return
        block = " ".join(ln.strip() for ln in current_lines if ln.strip())
        if block:
            record.education.append(_parse_education_line(block))
        current_lines.clear()

    for raw_line in edu_lines:
        line = raw_line.strip()
        if not line:
            _flush_block()
            continue
        if line.endswith(":") and len(line.split()) <= 3:
            _flush_block()
            continue
        if _DEGREE_RE.match(line) and current_lines:
            _flush_block()
        current_lines.append(line)
    _flush_block()


def _parse_education_line(line: str) -> PartialEducationItem:
    """Parse one education block line into degree/field/institution/year."""
    item = PartialEducationItem()

    year_match = _YEAR_RE.search(line)
    if year_match:
        item.year = _tv(year_match.group(0), "free-text", FREE_TEXT_CONFIDENCE)

    parts = [p.strip() for p in line.split(",") if p.strip()]
    parts = [p for p in parts if not _YEAR_RE.fullmatch(p)]

    degree_field: Optional[str] = None
    institution: Optional[str] = None
    if len(parts) >= 2:
        degree_field = parts[0]
        institution = ", ".join(parts[1:])
    elif len(parts) == 1:
        degree_field = parts[0]

    if degree_field:
        degree_match = _DEGREE_RE.match(degree_field)
        if degree_match:
            degree = degree_match.group(0).strip()
            field = degree_field[degree_match.end() :].strip(" ,in")
            item.degree = _tv(degree, "free-text", FREE_TEXT_CONFIDENCE)
            if field:
                item.field = _tv(field, "free-text", FREE_TEXT_CONFIDENCE)
        else:
            item.field = _tv(degree_field, "free-text", FREE_TEXT_CONFIDENCE)

    if institution:
        item.institution = _tv(institution, "free-text", FREE_TEXT_CONFIDENCE)

    return item


def parse_resume(path_or_text: Union[str, Path]) -> PartialRecord:
    """Parse a plain-text resume into a single raw :class:`PartialRecord`.

    Parameters
    ----------
    path_or_text:
        A :class:`~pathlib.Path` to a ``.txt`` resume, a string path to one, or
        the raw resume text itself.

    Returns
    -------
    PartialRecord
        Always ``source="resume"``. Empty/garbage input (or a missing file)
        yields an empty-but-valid record rather than raising.
    """
    record = PartialRecord(source=SOURCE_NAME)

    try:
        text = _load_text(path_or_text)
        if not text or not text.strip():
            return record

        lines = text.splitlines()
        sections = _split_sections(lines)
        header_lines = sections.get("header", [])

        _extract_name_and_headline(header_lines, text, record)
        _extract_contacts(text, header_lines, record)
        _extract_skills(sections.get("skills", []), record)
        _extract_experience(sections.get("experience", []), record)
        _extract_education(sections.get("education", []), record)
    except Exception:
        # Fail-soft: never raise out of the adapter. Return whatever we have,
        # falling back to the empty-but-valid record.
        return record

    return record
