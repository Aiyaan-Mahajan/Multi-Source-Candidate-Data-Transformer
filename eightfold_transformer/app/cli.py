"""CLI entry point for the Multi-Source Candidate Data Transformer.

This module is the thin input/output surface that wires the full, deterministic
pipeline together. It contains **no business logic** of its own; each stage lives
in its own package and is merely orchestrated here:

    input files                                          (CLI responsibility)
        |
        v
    Ingestion        read_csv(...) + parse_resume(...)  -> list[PartialRecord]
        |
        v
    Normalize+Merge  merge(partials)                    -> list[CanonicalCandidate]
        |                                                  (normalization runs
        v                                                   *inside* merge)
    Projection       project(candidate, config)         -> dict (per candidate)
        |
        v
    Validation       validate_projected(dict, config)   -> structural check
        |
        v
    JSON output      json.dumps([...])                  -> stdout / --out file

Design choices (documented):

* **Stdout is pure JSON.** All human-facing progress/diagnostic logging goes to
  stderr via the :mod:`logging` module at INFO level, so a caller can safely pipe
  stdout into ``jq`` or a file.
* **Output shape.** The result is *always* a JSON array of projected objects,
  one per canonical candidate (even for a single candidate). Zero candidates
  yields ``[]``. The array is ordered by ``candidate_id`` for determinism that is
  independent of input order.
* **Default config.** When ``--config`` is omitted we use
  ``configs/default.json`` (falling back to the legacy ``configs/default_config.json``
  if the former is absent), resolved relative to the repository root.
* **Exit codes.**
    - ``0`` success *or* empty-but-valid result (zero candidates -> ``[]``).
    - ``2`` usage error (e.g. neither ``--csv`` nor ``--resume`` given).
    - ``1`` fatal runtime error (missing/malformed config, none of the provided
      source paths readable, or a projection error).
* **Fail-soft vs fatal for sources.** A provided ``--csv`` / ``--resume`` path
  that does not exist is a *soft* failure: we warn on stderr and continue with
  the other source. Only when *none* of the provided source paths are readable
  do we treat it as a fatal error (exit ``1``). Empty-but-readable files are
  perfectly valid and simply contribute zero records.
* **Determinism.** No clock or randomness touches the output. Candidate ordering
  is stable (by ``candidate_id``); key order within each object follows the
  projection config order.
* **Output validation (on by default).** Every projected dict is structurally
  validated against the shape implied by its config (required fields present and
  non-null, declared types satisfied) *before* anything is emitted. A failure is
  fatal (exit ``1``) so malformed output never silently ships. Validation can be
  disabled with ``--no-validate`` (e.g. for debugging a config). On success an
  INFO line ``validated N record(s)`` is logged to stderr.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import List, Optional, Sequence

from eightfold_transformer.app.ingestion.csv_reader import read_csv
from eightfold_transformer.app.ingestion.resume_parser import parse_resume
from eightfold_transformer.app.merger import merge
from eightfold_transformer.app.models.partial import PartialRecord
from eightfold_transformer.app.models.schema import CanonicalCandidate
from eightfold_transformer.app.projection import (
    ProjectionConfig,
    ProjectionError,
    load_config,
    project,
)
from eightfold_transformer.app.validation import validate_projected

# --- Exit codes (documented contract) ---------------------------------------
EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2

# Repository root: cli.py lives at <root>/eightfold_transformer/app/cli.py, so
# parents[2] is the repo root. Used to locate the bundled default configs in a
# cwd-independent way.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_CONFIG_CANDIDATES = (
    _REPO_ROOT / "configs" / "default.json",
    _REPO_ROOT / "configs" / "default_config.json",
)

logger = logging.getLogger("eightfold_transformer.cli")


class _CliError(Exception):
    """Internal: a friendly, already-explained error mapped to an exit code.

    Carrying the intended exit code lets the orchestration raise a single
    exception type and have :func:`main` translate it into a concise stderr
    message plus the right process exit code (no traceback ever reaches a user).
    """

    def __init__(self, message: str, code: int = EXIT_RUNTIME) -> None:
        super().__init__(message)
        self.code = code


def build_parser() -> argparse.ArgumentParser:
    """Construct the stdlib :class:`argparse.ArgumentParser` for the CLI."""
    parser = argparse.ArgumentParser(
        prog="eightfold-transformer",
        description=(
            "Transform multi-source candidate data (recruiter CSV + resume text) "
            "into deterministic, config-shaped canonical JSON."
        ),
    )
    parser.add_argument(
        "--csv",
        metavar="PATH",
        default=None,
        help="Path to a recruiter CSV source (optional).",
    )
    parser.add_argument(
        "--resume",
        metavar="PATH",
        default=None,
        help="Path to a plain-text resume source (optional).",
    )
    parser.add_argument(
        "--config",
        metavar="PATH",
        default=None,
        help=(
            "Path to a projection config JSON. Defaults to configs/default.json "
            "(then configs/default_config.json) at the repo root."
        ),
    )
    parser.add_argument(
        "--out",
        metavar="PATH",
        default=None,
        help="Write JSON output to this file instead of stdout.",
    )
    parser.add_argument(
        "--no-validate",
        dest="validate",
        action="store_false",
        default=True,
        help=(
            "Skip structural validation of the projected output against the "
            "config (validation is ON by default; a failure is fatal, exit 1)."
        ),
    )
    return parser


def _resolve_default_config() -> Path:
    """Return the first bundled default config that exists, or raise fatally."""
    for candidate in _DEFAULT_CONFIG_CANDIDATES:
        if candidate.is_file():
            return candidate
    raise _CliError(
        "No projection config provided and no bundled default config found "
        f"(looked for {', '.join(str(p) for p in _DEFAULT_CONFIG_CANDIDATES)}).",
        EXIT_RUNTIME,
    )


def _load_projection_config(config_arg: Optional[str]) -> ProjectionConfig:
    """Load and validate the projection config, mapping failures to _CliError.

    A missing, empty, malformed, or schema-invalid config is a *fatal* error: we
    cannot meaningfully shape output without knowing what to emit.
    """
    config_path = Path(config_arg) if config_arg else _resolve_default_config()

    if config_arg is not None and not config_path.is_file():
        raise _CliError(
            f"Config file not found or not readable: {config_path}", EXIT_RUNTIME
        )

    try:
        config = load_config(config_path)
    except FileNotFoundError:
        raise _CliError(
            f"Config file not found: {config_path}", EXIT_RUNTIME
        ) from None
    except json.JSONDecodeError as exc:
        raise _CliError(
            f"Config file is not valid JSON ({config_path}): {exc}", EXIT_RUNTIME
        ) from None
    except OSError as exc:
        raise _CliError(
            f"Could not read config file {config_path}: {exc}", EXIT_RUNTIME
        ) from None
    except Exception as exc:  # pydantic ValidationError and friends
        raise _CliError(
            f"Config file is structurally invalid ({config_path}): {exc}",
            EXIT_RUNTIME,
        ) from None

    logger.info("Loaded projection config from %s", config_path)
    return config


def _ingest(csv_arg: Optional[str], resume_arg: Optional[str]) -> List[PartialRecord]:
    """Stage 1 - Ingestion. Read each provided source into partial records.

    Source paths are fail-soft: a missing path warns and is skipped. If *every*
    provided path is missing, that is a fatal error (nothing to work with).
    """
    if csv_arg is None and resume_arg is None:
        # Defensive: the caller validates this earlier and raises a usage error.
        raise _CliError(
            "No input sources provided; pass --csv and/or --resume.", EXIT_USAGE
        )

    partials: List[PartialRecord] = []
    provided = 0
    usable = 0

    if csv_arg is not None:
        provided += 1
        csv_path = Path(csv_arg)
        if not csv_path.is_file():
            logger.warning(
                "CSV source not found, skipping (continuing with other sources): %s",
                csv_path,
            )
        else:
            usable += 1
            rows = read_csv(csv_path)
            logger.info("Ingested %d record(s) from CSV %s", len(rows), csv_path)
            partials.extend(rows)

    if resume_arg is not None:
        provided += 1
        resume_path = Path(resume_arg)
        if not resume_path.is_file():
            logger.warning(
                "Resume source not found, skipping (continuing with other sources): %s",
                resume_path,
            )
        else:
            usable += 1
            record = parse_resume(resume_path)
            logger.info("Ingested resume %s", resume_path)
            partials.append(record)

    if provided > 0 and usable == 0:
        raise _CliError(
            "None of the provided input sources could be read; nothing to do.",
            EXIT_RUNTIME,
        )

    logger.info("Stage 1/4 ingestion: %d partial record(s) total", len(partials))
    return partials


def _merge(partials: List[PartialRecord]) -> List[CanonicalCandidate]:
    """Stage 2 - Normalization + Merge (normalization runs inside ``merge``)."""
    candidates = merge(partials)
    # Stable ordering by candidate_id makes output independent of input order.
    candidates.sort(key=lambda c: c.candidate_id.value or "")
    logger.info("Stage 2/4 merge: %d canonical candidate(s)", len(candidates))
    return candidates


def _project_all(
    candidates: List[CanonicalCandidate],
    config: ProjectionConfig,
    validate: bool = True,
) -> List[dict]:
    """Stage 3 - Projection (+ optional validation). Shape each candidate.

    A :class:`ProjectionError` (e.g. a required field that could not be resolved)
    is mapped to a friendly fatal error rather than a raw traceback.

    When ``validate`` is true (the default), each projected dict is structurally
    checked against the shape implied by ``config`` before being kept. Any
    validation failure is fatal (exit ``1``) so malformed output never ships.
    """
    results: List[dict] = []
    for candidate in candidates:
        cid = candidate.candidate_id.value
        try:
            record = project(candidate, config)
        except ProjectionError as exc:
            raise _CliError(
                f"Projection failed for candidate {cid!r}: {exc}", EXIT_RUNTIME
            ) from None

        if validate:
            errors = validate_projected(record, config)
            if errors:
                raise _CliError(
                    f"output failed schema validation for candidate {cid!r}: "
                    + "; ".join(errors),
                    EXIT_RUNTIME,
                )
        results.append(record)

    logger.info("Stage 3/4 projection: shaped %d object(s)", len(results))
    if validate:
        logger.info("validated %d record(s)", len(results))
    return results


def _emit(results: List[dict], out_arg: Optional[str]) -> None:
    """Stage 4 - Output. Write deterministic, pretty JSON to stdout or a file."""
    # ensure_ascii=False keeps unicode readable; insertion order (config order)
    # is preserved so key order is meaningful yet still deterministic.
    text = json.dumps(results, ensure_ascii=False, indent=2)

    if out_arg is None:
        sys.stdout.write(text + "\n")
        logger.info("Stage 4/4 output: wrote %d object(s) to stdout", len(results))
        return

    out_path = Path(out_arg)
    try:
        out_path.write_text(text + "\n", encoding="utf-8")
    except OSError as exc:
        raise _CliError(
            f"Could not write output file {out_path}: {exc}", EXIT_RUNTIME
        ) from None
    logger.info(
        "Stage 4/4 output: wrote %d object(s) to %s", len(results), out_path
    )


def _configure_logging() -> None:
    """Route INFO+ logs to the *current* stderr, keeping stdout pure JSON.

    The handler is rebuilt on every call so it always binds to the live
    ``sys.stderr`` object (important for in-process callers/tests that swap
    ``sys.stderr`` between runs); otherwise a stale, closed stream would trigger
    spurious "Logging error" tracebacks.
    """
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


def run(args: argparse.Namespace) -> int:
    """Execute the wired pipeline for already-parsed ``args``.

    Returns a process exit code. All expected failure modes are surfaced as a
    concise stderr message; no traceback escapes to the user.
    """
    if args.csv is None and args.resume is None:
        print(
            "error: at least one of --csv or --resume must be provided",
            file=sys.stderr,
        )
        return EXIT_USAGE

    # Load config first so a bad config fails fast before any ingestion work.
    config = _load_projection_config(args.config)

    partials = _ingest(args.csv, args.resume)
    candidates = _merge(partials)

    if not candidates:
        # Empty-but-valid result: emit an empty array and succeed.
        logger.info("No candidates after merge; emitting empty result.")
        _emit([], args.out)
        return EXIT_OK

    results = _project_all(candidates, config, validate=getattr(args, "validate", True))
    _emit(results, args.out)
    return EXIT_OK


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point. Parse ``argv`` (defaults to ``sys.argv``) and run.

    Wraps the orchestration in a top-level guard so that *any* unexpected error
    becomes a concise stderr message plus a non-zero exit code, never a bare
    traceback. Returns the process exit code (callable in-process by tests).
    """
    _configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        return run(args)
    except _CliError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.code
    except KeyboardInterrupt:  # pragma: no cover - interactive only
        print("error: interrupted", file=sys.stderr)
        return EXIT_RUNTIME
    except Exception as exc:  # last-resort guard: no traceback to the user
        logger.debug("Unexpected error", exc_info=True)
        print(f"error: unexpected failure: {exc}", file=sys.stderr)
        return EXIT_RUNTIME


if __name__ == "__main__":  # pragma: no cover - exercised via __main__.py / main.py
    raise SystemExit(main())
