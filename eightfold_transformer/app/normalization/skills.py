"""Skill name normalizer with configurable vocabulary.

Map raw skill mentions to canonical skill names via ``configs/skills.json``.
Unknown tokens are still returned trimmed from :func:`normalize_skill`, but
:func:`is_allowed_skill` gates which skills survive merge/projection.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

__all__ = [
    "SKILL_ALIASES",
    "normalize_skill",
    "normalize_skills",
    "is_allowed_skill",
    "allowed_skill_names",
]

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Inline fallback when the config file is missing.
_FALLBACK_ALIASES: dict[str, str] = {
    "reactjs": "React",
    "react.js": "React",
    "react js": "React",
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "py": "Python",
    "python": "Python",
    "golang": "Go",
    "go": "Go",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "docker": "Docker",
    "aws": "AWS",
    "rust": "Rust",
}


def _lookup_key(raw: str) -> str:
    return _NON_ALNUM.sub("", raw.strip().lower())


@lru_cache(maxsize=1)
def _load_aliases() -> dict[str, str]:
    config_path = Path(__file__).resolve().parents[3] / "configs" / "skills.json"
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
        aliases = payload.get("aliases", payload)
        if isinstance(aliases, dict):
            return {str(k).lower(): str(v) for k, v in aliases.items()}
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return dict(_FALLBACK_ALIASES)


@lru_cache(maxsize=1)
def _canonical_by_key() -> dict[str, str]:
    return {_lookup_key(alias): canonical for alias, canonical in _load_aliases().items()}


@lru_cache(maxsize=1)
def allowed_skill_names() -> frozenset[str]:
    return frozenset(_canonical_by_key().values())


# Backward-compatible export used by older tests/docs.
SKILL_ALIASES: dict[str, str] = _load_aliases()


def normalize_skill(raw: str) -> str:
    """Canonicalize a single skill name via the alias gazetteer."""
    if not isinstance(raw, str):
        return raw
    canonical = _canonical_by_key().get(_lookup_key(raw))
    if canonical is not None:
        return canonical
    return raw.strip()


def is_allowed_skill(raw: str) -> bool:
    """True when ``raw`` maps to a skill in the configured vocabulary."""
    if not isinstance(raw, str) or not raw.strip():
        return False
    canonical = normalize_skill(raw)
    return canonical in allowed_skill_names()


def normalize_skills(raw: list[str]) -> list[str]:
    """Apply :func:`normalize_skill` to each item, preserving order."""
    return [normalize_skill(item) for item in raw]
