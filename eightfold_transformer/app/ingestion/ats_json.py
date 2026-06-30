"""ATS JSON source adapter (structured).

Responsibility (no logic yet):
- Read candidate records exported from an ATS as JSON.
- Extract raw per-source fields and emit PartialRecord objects.
- Attach provenance (source = "ats_json") to each emitted record.

TODO: Implement the SourceAdapter contract from app.ingestion.base.
TODO: Handle nested JSON traversal and field extraction (no normalization here).
"""
