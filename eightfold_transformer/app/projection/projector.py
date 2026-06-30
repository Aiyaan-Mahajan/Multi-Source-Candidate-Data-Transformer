"""Output projector.

Applies a runtime :class:`~eightfold_transformer.app.projection.config.ProjectionConfig`
to a :class:`~eightfold_transformer.app.models.schema.CanonicalCandidate` to
produce a plain, JSON-serializable ``dict`` whose shape is defined *entirely* by
the config. The projector is read-only: it never mutates the canonical record.

Path resolution syntax (the ``from`` of each field)
---------------------------------------------------
A source path is a deterministic, dotted expression evaluated against the
canonical record:

* ``headline``            - attribute access.
* ``location.country``    - nested attribute access.
* ``emails[0]``           - list indexing (negative indices allowed).
* ``skills[].name``       - the ``[]`` "map" operator: evaluate the remaining
                            path for every element and return a list.
* ``overall_confidence``  - any scalar attribute.

Resolution rules:

* Out-of-range index, missing attribute, or indexing a non-list yields the
  internal ``MISSING`` sentinel (handled per ``on_missing``).
* When the resolved node is a :class:`TrackedValue`, the projected value is its
  ``.value`` (the wrapper is unwrapped). When ``include_confidence`` is set, the
  field is surfaced as ``{"value": <value>, "confidence": <float>}`` instead.
* A :class:`Skill` projects to ``{"name", "sources"}`` (plus ``"confidence"``
  when ``include_confidence`` is set; skill confidence comes from
  ``Skill.confidence``).
* Other nested models (``Location``, ``Links``, ``ExperienceItem``, ...) project
  recursively, so any nested ``TrackedValue`` inside them is unwrapped too.

Confidence convention (documented choice)
-----------------------------------------
``include_confidence=True`` wraps every provenance-tracked value as
``{"value": ..., "confidence": ...}``. Plain scalars that carry no provenance
(e.g. ``overall_confidence`` itself) are emitted bare. ``include_confidence``
defaults to ``False``, in which case only the bare value is emitted.

Provenance convention
---------------------
``include_provenance=True`` attaches the canonical ``provenance[]`` ledger
(``[{"field", "source", "method"}, ...]``) under a top-level ``"provenance"``
key, appended after the configured fields. When ``False`` it is never present.

Determinism
-----------
Output key order follows ``config.fields`` order (then ``provenance``). No clock,
randomness, or network access is involved.
"""

from __future__ import annotations

import re
from typing import Any, Callable, List, Optional, Tuple

from pydantic import BaseModel

from eightfold_transformer.app.models.schema import (
    CanonicalCandidate,
    Skill,
    TrackedValue,
)
from eightfold_transformer.app.projection.config import (
    FieldSpec,
    ProjectionConfig,
    load_config,
)

__all__ = ["project", "project_dict", "ProjectionError"]


class ProjectionError(ValueError):
    """Raised when a required/missing field cannot be resolved under the config."""


# Sentinel distinguishing "path did not resolve" from a legitimately resolved
# ``None`` value. Using a unique object means equality checks are identity-safe.
class _Missing:
    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return "<MISSING>"


MISSING = _Missing()

# A single path token: ("attr", name) | ("index", int) | ("map",).
_Token = Tuple[str, Any]

# One bracket group: either an integer index or empty (the map operator).
_BRACKET = re.compile(r"\[([^\]]*)\]")
# Leading identifier of a dotted segment (the attribute name before any bracket).
_IDENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)?(.*)$")


def _parse_path(path: str) -> List[_Token]:
    """Parse a source path string into a flat list of resolution tokens.

    Examples
    --------
    >>> _parse_path("skills[].name")
    [('attr', 'skills'), ('map',), ('attr', 'name')]
    >>> _parse_path("emails[0]")
    [('attr', 'emails'), ('index', 0)]
    """
    tokens: List[_Token] = []
    for segment in path.split("."):
        if segment == "":
            continue
        match = _IDENT.match(segment)
        # _IDENT always matches (both groups optional), so `match` is not None.
        assert match is not None
        name, brackets = match.group(1), match.group(2)
        if name:
            tokens.append(("attr", name))
        for raw in _BRACKET.findall(brackets):
            if raw == "":
                tokens.append(("map",))
            else:
                try:
                    tokens.append(("index", int(raw)))
                except ValueError as exc:
                    raise ProjectionError(
                        f"Invalid list index {raw!r} in path {path!r}"
                    ) from exc
    return tokens


def _get_attr(node: Any, name: str) -> Any:
    """Read attribute/key ``name`` from ``node`` or return ``MISSING``."""
    if isinstance(node, BaseModel):
        if name in type(node).model_fields:
            return getattr(node, name)
        return MISSING
    if isinstance(node, dict):
        return node.get(name, MISSING)
    return MISSING


def _get_index(node: Any, index: int) -> Any:
    """Index into ``node`` (a list) or return ``MISSING`` when out of range."""
    if isinstance(node, list) and -len(node) <= index < len(node):
        return node[index]
    return MISSING


def _resolve(node: Any, tokens: List[_Token], idx: int) -> Any:
    """Resolve ``tokens[idx:]`` against ``node``; returns a raw node or ``MISSING``.

    The ``map`` token returns a list built by resolving the remaining tokens for
    each element, with unresolved elements dropped so the list stays clean.
    """
    if node is MISSING:
        return MISSING
    if idx >= len(tokens):
        return node

    kind, payload = tokens[idx][0], tokens[idx][1] if len(tokens[idx]) > 1 else None
    if kind == "attr":
        return _resolve(_get_attr(node, payload), tokens, idx + 1)
    if kind == "index":
        return _resolve(_get_index(node, payload), tokens, idx + 1)
    if kind == "map":
        if not isinstance(node, list):
            return MISSING
        out: List[Any] = []
        for element in node:
            resolved = _resolve(element, tokens, idx + 1)
            if resolved is not MISSING:
                out.append(resolved)
        return out
    raise ProjectionError(f"Unknown path token: {tokens[idx]!r}")


def _project_node(node: Any, include_confidence: bool) -> Any:
    """Convert a resolved canonical node into a plain, JSON-serializable value.

    Unwraps :class:`TrackedValue` (optionally wrapping with confidence), expands
    :class:`Skill`, recurses into nested models/lists/dicts, and passes scalars
    through unchanged.
    """
    if node is None or node is MISSING:
        return None
    if isinstance(node, TrackedValue):
        if include_confidence:
            return {"value": node.value, "confidence": node.confidence}
        return node.value
    if isinstance(node, Skill):
        skill: dict[str, Any] = {"name": node.name, "sources": list(node.sources)}
        if include_confidence:
            skill["confidence"] = node.confidence
        return skill
    if isinstance(node, BaseModel):
        return {
            name: _project_node(getattr(node, name), include_confidence)
            for name in type(node).model_fields
        }
    if isinstance(node, list):
        return [
            _project_node(item, include_confidence)
            for item in node
            if item is not MISSING
        ]
    if isinstance(node, dict):
        return {
            key: _project_node(value, include_confidence)
            for key, value in node.items()
        }
    return node


def _get_normalizer(directive: str) -> Optional[Callable[[Any], Any]]:
    """Resolve a normalize directive to a normalization function (lazy import).

    Normalizers are imported lazily, and from their *specific submodules* rather
    than the ``normalization`` package root, so that base projection depends only
    on ``pydantic`` and a directive pulls in just the dependency it needs (e.g.
    ``'canonical'`` does not require ``phonenumbers`` / ``python-dateutil``).
    """
    key = directive.strip().lower()
    if key in ("e164", "phone"):
        from eightfold_transformer.app.normalization.phones import normalize_phone

        return normalize_phone
    if key in ("yyyy-mm", "date"):
        from eightfold_transformer.app.normalization.dates import normalize_date

        return normalize_date
    if key in ("canonical", "skill"):
        from eightfold_transformer.app.normalization.skills import normalize_skill

        return normalize_skill
    if key in ("country", "iso3166", "iso-3166"):
        from eightfold_transformer.app.normalization.location import normalize_country

        return normalize_country
    return None


def _apply_normalize(value: Any, fn: Callable[[Any], Any]) -> Any:
    """Apply ``fn`` to the scalar value(s) inside an already-projected value."""
    if value is None:
        return None
    if isinstance(value, list):
        return [_apply_normalize(item, fn) for item in value]
    if isinstance(value, dict):
        if "value" in value:  # confidence wrapper -> normalize the inner value
            return {**value, "value": _apply_normalize(value["value"], fn)}
        if "name" in value and isinstance(value["name"], str):  # skill dict
            return {**value, "name": fn(value["name"])}
        return value
    if isinstance(value, str):
        return fn(value)
    return value


def _normalize_directive(value: Any, directive: Any) -> Any:
    """Route ``value`` through the normalizer named by ``directive`` if any.

    A boolean ``True`` (used by the sample configs) is a documented no-op here
    because it carries no information about *which* normalizer to apply; only
    string directives select a function. Unknown directives are also no-ops.
    """
    if directive is None or directive is False or directive is True:
        return value
    if not isinstance(directive, str):
        return value
    fn = _get_normalizer(directive)
    if fn is None:
        return value
    return _apply_normalize(value, fn)


def _set_nested(out: dict[str, Any], path: str, value: Any) -> None:
    """Assign ``value`` into ``out`` at the dotted output ``path``."""
    parts = [p for p in path.split(".") if p != ""]
    cursor = out
    for part in parts[:-1]:
        existing = cursor.get(part)
        if not isinstance(existing, dict):
            existing = {}
            cursor[part] = existing
        cursor = existing
    cursor[parts[-1]] = value


def project(candidate: CanonicalCandidate, config: ProjectionConfig) -> dict[str, Any]:
    """Project ``candidate`` into a plain dict whose shape is defined by ``config``.

    Parameters
    ----------
    candidate:
        The canonical record to read from. It is never mutated.
    config:
        The projection configuration (fields to select/rename, toggles, and
        missing-value policy).

    Returns
    -------
    dict
        A JSON-serializable dict containing exactly the configured fields (in
        config order), plus ``"provenance"`` when ``include_provenance`` is set.

    Raises
    ------
    ProjectionError
        When a ``required`` field cannot be resolved, or when ``on_missing`` is
        ``"error"`` and any field cannot be resolved.
    """
    out: dict[str, Any] = {}

    for spec in config.fields:
        tokens = _parse_path(spec.source)
        node = _resolve(candidate, tokens, 0)

        if node is MISSING:
            if spec.required or config.on_missing == "error":
                raise ProjectionError(
                    f"Could not resolve field {spec.path!r} from source "
                    f"{spec.source!r}"
                    + (" (required)" if spec.required else "")
                )
            if config.on_missing == "omit":
                continue
            _set_nested(out, spec.path, None)
            continue

        value = _project_node(node, config.include_confidence)
        value = _normalize_directive(value, spec.normalize)
        _set_nested(out, spec.path, value)

    if config.include_provenance:
        out["provenance"] = [
            {"field": entry.field, "source": entry.source, "method": entry.method}
            for entry in candidate.provenance
        ]

    return out


def project_dict(
    candidate: CanonicalCandidate, config_dict: Any
) -> dict[str, Any]:
    """Convenience wrapper: build a :class:`ProjectionConfig` then project.

    Parameters
    ----------
    candidate:
        The canonical record to project.
    config_dict:
        A mapping or path accepted by
        :func:`eightfold_transformer.app.projection.config.load_config`.
    """
    config = load_config(config_dict)
    return project(candidate, config)
