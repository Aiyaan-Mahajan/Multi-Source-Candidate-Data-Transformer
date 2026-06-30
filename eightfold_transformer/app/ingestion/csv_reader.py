"""Recruiter CSV ingestion adapter (structured source).

Reads a recruiter-exported CSV (one candidate per row) and emits one
:class:`PartialRecord` per row. This is a *structured* source: every cell is a
named column, so values are read directly with ``extraction_method="structured"``
and a fixed high base confidence.

Boundary
--------
This adapter **extracts only** — it does not normalize, merge, or validate:

* Phone numbers are captured **exactly as written** (``"(555) 123-4567"``,
  ``"+1-555-123-4567"``, ``"555.123.4567"``, and even non-numeric junk like
  ``"call me"`` / ``"N/A"``). E.164 normalization is a later stage.
* Empty/blank cells produce an *omitted* field (``None`` / not appended), never
  an invented value.

Determinism & fail-soft
-----------------------
* Output preserves input row order, with no randomness or wall-clock use.
* A missing file, empty file, or individually malformed row never raises out of
  :func:`read_csv`; the reader degrades gracefully and returns whatever rows it
  could parse.

Expected header: ``name,email,phone,current_company,title``.

Public surface
--------------
``from eightfold_transformer.app.ingestion.csv_reader import read_csv``
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import List, Optional, Union

from eightfold_transformer.app.models.partial import (
    PartialExperienceItem,
    PartialRecord,
)
from eightfold_transformer.app.models.schema import TrackedValue

#: Stable identifier attached to every value produced by this adapter.
SOURCE_NAME = "recruiter_csv"

#: Fixed confidence for directly-read structured columns. Constant (no
#: randomness) so identical input yields byte-identical output.
STRUCTURED_CONFIDENCE = 0.9


def _tracked(value: str) -> TrackedValue[str]:
    """Wrap a raw structured cell value in a provenance-carrying TrackedValue."""
    return TrackedValue[str](
        value=value,
        source=SOURCE_NAME,
        confidence=STRUCTURED_CONFIDENCE,
        extraction_method="structured",
    )


def _clean(cell: Optional[str]) -> Optional[str]:
    """Return a stripped non-empty cell, or ``None`` for blank/missing cells.

    A blank cell means "this source said nothing here"; the caller omits the
    field rather than storing an empty string.
    """
    if cell is None:
        return None
    stripped = cell.strip()
    return stripped or None


def _row_to_record(row: dict) -> PartialRecord:
    """Map a single CSV row dict to a raw :class:`PartialRecord`.

    Column mapping: ``name`` -> ``full_name``; ``email`` -> ``emails[0]``;
    ``phone`` -> ``phones[0]`` (raw); ``current_company`` + ``title`` -> one
    :class:`PartialExperienceItem`. Blank cells are omitted.
    """
    record = PartialRecord(source=SOURCE_NAME)

    name = _clean(row.get("name"))
    if name is not None:
        record.full_name = _tracked(name)

    email = _clean(row.get("email"))
    if email is not None:
        record.emails.append(_tracked(email))

    phone = _clean(row.get("phone"))
    if phone is not None:
        # Captured verbatim; normalization (E.164) happens downstream.
        record.phones.append(_tracked(phone))

    company = _clean(row.get("current_company"))
    title = _clean(row.get("title"))
    if company is not None or title is not None:
        record.experience.append(
            PartialExperienceItem(
                company=_tracked(company) if company is not None else None,
                title=_tracked(title) if title is not None else None,
            )
        )

    return record


def read_csv(path: Union[str, Path]) -> List[PartialRecord]:
    """Read a recruiter CSV and return one :class:`PartialRecord` per data row.

    Parameters
    ----------
    path:
        Filesystem path to the CSV. Header is expected to be
        ``name,email,phone,current_company,title``.

    Returns
    -------
    list[PartialRecord]
        Records in input row order. Returns ``[]`` for a missing or empty file.
        Individually malformed rows are skipped rather than aborting the read.

    Notes
    -----
    This function is fail-soft: it never raises for IO or per-row parse problems.
    """
    records: List[PartialRecord] = []

    try:
        # newline="" is the documented way to let the csv module handle line
        # endings; encoding errors are tolerated so a stray byte can't abort.
        # "utf-8-sig" strips a leading UTF-8 BOM if present (otherwise the first
        # header would become "\ufeffname" and the name column would never
        # match); it is harmless when no BOM is present.
        with open(path, "r", newline="", encoding="utf-8-sig", errors="replace") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames is None:
                # Empty file (no header) -> nothing parseable.
                return records
            for row in reader:
                try:
                    records.append(_row_to_record(row))
                except Exception:
                    # Degrade: skip this row, keep the rest. Determinism is
                    # preserved because we simply omit the offending row.
                    continue
    except (FileNotFoundError, IsADirectoryError, PermissionError, OSError):
        # Missing/unreadable file -> honestly-empty result, never an exception.
        return records

    return records
