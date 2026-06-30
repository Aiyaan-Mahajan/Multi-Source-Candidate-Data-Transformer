"""Deterministic, offline tests for the CLI entry point.

These exercise the wired pipeline in-process via ``cli.main(argv) -> int`` so the
tests are fast and deterministic (no subprocess, no network, no clock/random).
They assert the documented contract: pure-JSON stdout, stderr logging, exit-code
scheme, fail-soft vs fatal behavior, empty-data handling, and determinism.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eightfold_transformer.app import cli

_REPO_ROOT = Path(__file__).resolve().parents[1]
_DATA_DIR = _REPO_ROOT / "data"
_CSV = _DATA_DIR / "candidates.csv"
_RESUME = _DATA_DIR / "resume.txt"
_CONFIG = _REPO_ROOT / "configs" / "default.json"


def _run(argv, capsys):
    """Invoke the CLI in-process and return (exit_code, stdout, stderr)."""
    code = cli.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_happy_path_end_to_end(capsys):
    """Real CSV + resume + default config -> exit 0, JSON array, merged Priya."""
    code, out, err = _run(
        [
            "--csv",
            str(_CSV),
            "--resume",
            str(_RESUME),
            "--config",
            str(_CONFIG),
        ],
        capsys,
    )

    assert code == cli.EXIT_OK
    data = json.loads(out)  # stdout must be pure, parseable JSON
    assert isinstance(data, list)
    assert len(data) >= 1

    # Every projected object carries the required full_name field.
    assert all("full_name" in obj for obj in data)

    # The CSV gmail rows and the resume (matched on the shared
    # priya.sharma@gmail.com email) merge into a single canonical Priya whose
    # record fuses both sources: CSV emails + resume-only skills/links. This is
    # the cross-source merge the pipeline must demonstrate.
    merged_priya = [
        obj
        for obj in data
        if isinstance(obj.get("full_name"), str)
        and "Priya" in obj["full_name"]
        and obj.get("skills")
    ]
    assert len(merged_priya) == 1
    priya = merged_priya[0]
    assert priya["links"]["github"] == "github.com/priyasharma"  # from the resume
    skill_names = {s["name"] for s in priya["skills"]}
    assert "Python" in skill_names  # resume skill, normalized from "py"
    assert "priya.sharma@gmail.com" in priya["emails"]  # from the CSV

    # Stage logging goes to stderr, never stdout.
    assert "ingestion" in err.lower()


def test_no_sources_is_usage_error(capsys):
    """Neither --csv nor --resume -> usage error (exit 2), clear message."""
    code, out, err = _run(["--config", str(_CONFIG)], capsys)
    assert code == cli.EXIT_USAGE
    assert out == ""
    assert "--csv" in err and "--resume" in err


def test_missing_csv_is_failsoft(capsys, tmp_path):
    """A missing --csv path warns and continues with the resume (exit 0)."""
    missing = tmp_path / "does_not_exist.csv"
    code, out, err = _run(
        [
            "--csv",
            str(missing),
            "--resume",
            str(_RESUME),
            "--config",
            str(_CONFIG),
        ],
        capsys,
    )

    assert code == cli.EXIT_OK
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) >= 1
    # Clear, human-readable warning; no traceback.
    assert "not found" in err.lower()
    assert "Traceback" not in err


def test_missing_config_is_fatal(capsys, tmp_path):
    """A --config path that does not exist -> fatal (exit 1), no traceback."""
    missing_cfg = tmp_path / "nope.json"
    code, out, err = _run(
        ["--csv", str(_CSV), "--config", str(missing_cfg)], capsys
    )
    assert code == cli.EXIT_RUNTIME
    assert out == ""
    assert "config" in err.lower()
    assert "Traceback" not in err


def test_empty_input_yields_empty_array(capsys, tmp_path):
    """An empty (header-only / blank) CSV and no resume -> [] and exit 0."""
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("", encoding="utf-8")

    code, out, err = _run(
        ["--csv", str(empty_csv), "--config", str(_CONFIG)], capsys
    )
    assert code == cli.EXIT_OK
    assert json.loads(out) == []


def test_malformed_config_is_friendly_error(capsys, tmp_path):
    """Invalid JSON in the config -> friendly fatal error, no traceback."""
    bad_cfg = tmp_path / "bad.json"
    bad_cfg.write_text("{ this is : not valid json ", encoding="utf-8")

    code, out, err = _run(
        ["--csv", str(_CSV), "--config", str(bad_cfg)], capsys
    )
    assert code == cli.EXIT_RUNTIME
    assert out == ""
    assert "json" in err.lower()
    assert "Traceback" not in err


def test_determinism_same_args_identical_stdout(capsys):
    """Running the same args twice produces byte-identical stdout."""
    argv = [
        "--csv",
        str(_CSV),
        "--resume",
        str(_RESUME),
        "--config",
        str(_CONFIG),
    ]
    _, out1, _ = _run(argv, capsys)
    _, out2, _ = _run(argv, capsys)
    assert out1 == out2


def test_out_file_written(capsys, tmp_path):
    """--out writes JSON to a file and keeps stdout empty."""
    out_file = tmp_path / "result.json"
    code, out, _ = _run(
        [
            "--csv",
            str(_CSV),
            "--resume",
            str(_RESUME),
            "--config",
            str(_CONFIG),
            "--out",
            str(out_file),
        ],
        capsys,
    )
    assert code == cli.EXIT_OK
    assert out == ""
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)
