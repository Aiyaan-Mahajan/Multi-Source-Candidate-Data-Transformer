"""Adapter interface description for ingestion sources.

Responsibility (no logic yet):
- Define the common contract every source adapter must implement.
- An adapter reads a single raw source (structured or unstructured) and emits
  zero or more PartialRecord objects (see app.models.partial).
- Adapters MUST NOT normalize, merge, or validate; they only extract raw
  per-source fields plus provenance metadata (source id, confidence hints).

Intended interface (to be defined later):
- A base class / protocol, e.g. `SourceAdapter`, with:
  - `source_name` identifier.
  - a method that accepts a raw input handle (path / bytes / config) and
    yields PartialRecord objects.

TODO: Define the SourceAdapter base class / Protocol.
TODO: Document the expected PartialRecord output shape and provenance fields.
"""
