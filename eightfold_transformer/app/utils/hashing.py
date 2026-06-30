"""Deterministic hashing helpers.

Responsibility (no logic yet):
- Compute stable hashes / fingerprints for records and field values.
- Support deterministic matching/dedupe keys (merger) and stable record ids
  using a fixed algorithm over canonicalized, sorted input (no salt/randomness).

TODO: Implement a stable content hash over canonicalized record data.
TODO: Provide helpers for building deterministic blocking/match keys.
"""
