"""Expected-shape derivation for projected output validation.

This module turns a :class:`~eightfold_transformer.app.projection.config.ProjectionConfig`
into a lightweight, pure-Python description of the *expected shape* of a projected
record. It is the structural counterpart to a JSON Schema, but is built by hand so
the validation layer needs **no extra dependency** (the project deliberately avoids
``jsonschema``).

What "expected shape" means
---------------------------
The projection layer has a simple, total contract: every key that appears in the
output is declared by a :class:`FieldSpec` in ``config.fields``. So the expected
shape is exactly the set of declared output paths, each annotated with:

* ``path``      - the dotted OUTPUT key (e.g. ``"contact.email"`` -> nested dict).
* ``py_type``   - the Python type(s) the value must satisfy, derived from the
                  spec's declared ``type`` (``None`` -> no type check).
* ``required``  - whether the value must be present and non-null.
* ``type_name`` - the original declared type string (for friendly messages).

Declared-type mapping
---------------------
``string -> str``, ``array -> list``, ``object -> dict``,
``number -> (int, float)``, ``boolean -> bool``. An unknown or omitted type maps
to ``None`` (the field is selected/required-checked but not type-checked).

``include_confidence`` convention (documented choice)
-----------------------------------------------------
When ``config.include_confidence`` is true the projector emits each *tracked*
scalar as a confidence wrapper ``{"value": ..., "confidence": ...}`` instead of a
bare scalar. We carry the ``include_confidence`` flag on the returned schema so
the validator can unwrap such wrappers and type-check the inner ``value`` rather
than rejecting the wrapper dict. Container types (``array``/``object``) are never
unwrapped: a list of wrappers is still a list, and an ``object`` field is a plain
nested dict.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dataclass_field
from typing import Optional, Tuple, Type

from eightfold_transformer.app.projection.config import ProjectionConfig

__all__ = ["ExpectedField", "ExpectedSchema", "TYPE_MAP", "expected_schema"]

# Declared-type string -> Python type(s) used for isinstance checks. A declared
# type absent from this map (or ``None``) means "do not type-check this field".
TYPE_MAP: dict[str, Tuple[Type, ...]] = {
    "string": (str,),
    "array": (list,),
    "object": (dict,),
    "number": (int, float),
    "boolean": (bool,),
}


@dataclass(frozen=True)
class ExpectedField:
    """The expected shape of one projected output key.

    Attributes
    ----------
    path:
        Dotted output key (e.g. ``"contact.email"``).
    py_type:
        Tuple of acceptable Python types for an ``isinstance`` check, or ``None``
        when the declared type is unknown/omitted (no type check performed).
    required:
        When ``True`` the value must be present and non-null.
    type_name:
        The original declared type string (used only for friendly messages).
    """

    path: str
    py_type: Optional[Tuple[Type, ...]]
    required: bool
    type_name: Optional[str]


@dataclass(frozen=True)
class ExpectedSchema:
    """The expected shape of a whole projected record.

    ``include_confidence`` mirrors the source config so the validator can apply
    the confidence-wrapper-aware type checking documented in the module header.
    """

    fields: Tuple[ExpectedField, ...] = dataclass_field(default_factory=tuple)
    include_confidence: bool = False


def expected_schema(config: ProjectionConfig) -> ExpectedSchema:
    """Derive the :class:`ExpectedSchema` for records projected with ``config``.

    Parameters
    ----------
    config:
        The projection configuration whose ``fields`` define the output shape.

    Returns
    -------
    ExpectedSchema
        One :class:`ExpectedField` per declared spec, plus the
        ``include_confidence`` flag.
    """
    fields = tuple(
        ExpectedField(
            path=spec.path,
            py_type=TYPE_MAP.get(spec.type) if spec.type is not None else None,
            required=spec.required,
            type_name=spec.type,
        )
        for spec in config.fields
    )
    return ExpectedSchema(fields=fields, include_confidence=config.include_confidence)
