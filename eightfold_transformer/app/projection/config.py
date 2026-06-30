"""Projection configuration model / loader.

The :class:`ProjectionConfig` defined here is the *only* thing that decides what
the projected output looks like. There is **no hardcoded output schema** in this
layer: every key that appears in the projected result is declared explicitly in
``config.fields``. Anything not listed is, by definition, excluded.

Config shape (matches ``configs/*.json``)
-----------------------------------------
A config is a JSON object with::

    {
      "fields": [
        { "path": "...", "from": "...", "type": "...",
          "required": false, "normalize": false },
        ...
      ],
      "include_confidence": false,
      "include_provenance": false,
      "on_missing": "null"
    }

* ``path``    - the OUTPUT key (dotted paths like ``"contact.email"`` create
                nested objects). This is the rename mechanism.
* ``from``    - the CANONICAL source path to read from (see
                :mod:`eightfold_transformer.app.projection.projector` for the
                path syntax). Defaults to ``path`` when omitted.
* ``type``    - optional declared type hint (``string``/``array``/``object``/
                ``number``) used only as documentation / light coercion intent.
* ``required``- when ``True`` a value that cannot be resolved is a hard error.
* ``normalize``- optional per-field normalization directive (see projector).

The model deliberately accepts the existing sample configs (which use boolean
``normalize`` and omit ``include_provenance``) unchanged.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal, Mapping, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["FieldSpec", "ProjectionConfig", "load_config"]


class FieldSpec(BaseModel):
    """Declarative spec for a single projected output field.

    One :class:`FieldSpec` maps a canonical *source* path (``from_``) onto an
    *output* path (``path``). The presence of a spec is what causes a field to
    appear in the output at all (select), and the difference between ``path`` and
    ``from_`` is what implements renaming.
    """

    # ``populate_by_name`` lets callers build a spec with either ``from_`` (the
    # Python attribute) or ``"from"`` (the JSON alias). ``extra="forbid"`` keeps
    # config typos from silently passing.
    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    path: str = Field(
        description="Output key/path. Dotted paths create nested objects.",
    )
    from_: Optional[str] = Field(
        default=None,
        alias="from",
        description=(
            "Canonical source path to read from. Defaults to `path` when omitted."
        ),
    )
    type: Optional[str] = Field(
        default=None,
        description="Optional declared type hint (string/array/object/number).",
    )
    required: bool = Field(
        default=False,
        description="If True, a value that cannot be resolved raises an error.",
    )
    normalize: Optional[Union[str, bool]] = Field(
        default=None,
        description=(
            "Optional per-field normalization directive, e.g. 'E164', "
            "'YYYY-MM', 'canonical', 'country'. Boolean accepted for config "
            "compatibility (True is a documented no-op without a directive)."
        ),
    )

    @model_validator(mode="after")
    def _default_source_to_path(self) -> "FieldSpec":
        """Default the source path to the output path when ``from`` is omitted."""
        if self.from_ is None:
            self.from_ = self.path
        return self

    @property
    def source(self) -> str:
        """The effective canonical source path (``from_`` or, as a fallback, ``path``)."""
        return self.from_ if self.from_ is not None else self.path


class ProjectionConfig(BaseModel):
    """Runtime configuration that fully defines the projected output shape."""

    model_config = ConfigDict(extra="forbid")

    fields: list[FieldSpec] = Field(
        default_factory=list,
        description="Ordered list of output field specs. Output order follows this.",
    )
    include_confidence: bool = Field(
        default=False,
        description="When True, surface per-field confidence (see projector).",
    )
    include_provenance: bool = Field(
        default=False,
        description="When True, attach the canonical provenance[] audit trail.",
    )
    on_missing: Literal["null", "omit", "error"] = Field(
        default="null",
        description=(
            "Behavior when a (non-required) source path cannot be resolved: "
            "'null' emits null, 'omit' drops the key, 'error' raises."
        ),
    )


def load_config(source: Union[str, Path, Mapping[str, Any]]) -> ProjectionConfig:
    """Load and validate a :class:`ProjectionConfig` from a path or mapping.

    Parameters
    ----------
    source:
        Either a filesystem path (``str`` / :class:`pathlib.Path`) to a JSON
        config file, or an already-parsed mapping (``dict``).

    Returns
    -------
    ProjectionConfig
        The validated configuration.

    Raises
    ------
    TypeError
        If ``source`` is neither a path-like nor a mapping.
    """
    if isinstance(source, Mapping):
        data: Any = dict(source)
    elif isinstance(source, (str, Path)):
        with open(source, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        raise TypeError(
            f"load_config expects a path or mapping, got {type(source).__name__}"
        )
    return ProjectionConfig.model_validate(data)
