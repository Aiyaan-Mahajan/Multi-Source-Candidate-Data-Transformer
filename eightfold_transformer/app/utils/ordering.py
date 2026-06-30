"""Deterministic ordering helpers.

Responsibility (no logic yet):
- Provide stable, deterministic ordering used across merge and projection so
  list outputs (skills, emails, phones, sources) are reproducible.
- Centralize sort keys and tie-breaking rules (e.g. case-insensitive, then
  lexicographic) to avoid nondeterministic set/dict iteration order.

TODO: Implement stable sort helpers with explicit tie-breaking.
TODO: Define canonical ordering for multi-valued canonical fields.
"""
