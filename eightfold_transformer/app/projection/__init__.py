"""projection package: apply runtime config to canonical record to shape output.

The projection layer turns a canonical :class:`CanonicalCandidate` into a plain,
JSON-serializable dict whose shape is defined entirely by a runtime config (no
hardcoded output schema). It supports selecting, renaming (incl. nested output
paths), TrackedValue unwrapping, optional confidence/provenance inclusion,
optional per-field normalization, and configurable missing-value behavior.
"""

from eightfold_transformer.app.projection.config import (
    FieldSpec,
    ProjectionConfig,
    load_config,
)
from eightfold_transformer.app.projection.projector import (
    ProjectionError,
    project,
    project_dict,
)

__all__ = [
    "FieldSpec",
    "ProjectionConfig",
    "load_config",
    "project",
    "project_dict",
    "ProjectionError",
]
