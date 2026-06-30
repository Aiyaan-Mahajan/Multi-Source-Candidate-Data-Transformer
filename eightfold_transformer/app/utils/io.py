"""Deterministic IO helpers.

Responsibility (no logic yet):
- Provide small, deterministic read/write helpers shared across the pipeline.
- Read text/bytes from a path, load JSON, and dump JSON with stable formatting
  (sorted keys, fixed separators) so output is byte-for-byte reproducible.

TODO: Implement deterministic JSON load/dump helpers (sort_keys, fixed indent).
TODO: Implement safe text/bytes file readers used by ingestion adapters.
"""
