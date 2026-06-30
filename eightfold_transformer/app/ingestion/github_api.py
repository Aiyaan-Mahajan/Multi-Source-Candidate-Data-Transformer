"""GitHub public API source adapter (structured, network).

Responsibility (no logic yet):
- Query the GitHub public REST API for a user's profile / repositories.
- Derive raw per-source signals (e.g. name, location, languages -> skills).
- Emit PartialRecord objects with provenance (source = "github_api").

TODO: Implement the SourceAdapter contract from app.ingestion.base.
TODO: Use `requests` to call the public API (deterministic; no auth required).
TODO: Map profile/repo fields to raw partial fields (no normalization here).
"""
