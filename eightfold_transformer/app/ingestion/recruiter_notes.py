"""Recruiter free-text notes source adapter (unstructured).

Responsibility (no logic yet):
- Parse free-form recruiter notes (plain text) for raw candidate signals.
- Use deterministic heuristics / regex to extract fields (phones, emails,
  mentioned skills, location hints).
- Emit PartialRecord objects with provenance (source = "recruiter_notes").

TODO: Implement the SourceAdapter contract from app.ingestion.base.
TODO: Extract raw fields via deterministic patterns (no normalization here).
"""
