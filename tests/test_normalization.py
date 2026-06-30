"""Deterministic, offline tests for the normalization layer.

Covers per-field value transformers:
* phones  -> E.164 (region-aware) or None,
* dates   -> "YYYY-MM" or None (year-only never depends on the current date),
* skills  -> gazetteer canonicalization (unknown stays unchanged),
* country -> ISO-3166 alpha-2 or None (optional helper).

All tests are pure: no network, no clock, no randomness.
"""

from __future__ import annotations

import pytest

from eightfold_transformer.app.normalization import (
    normalize_country,
    normalize_date,
    normalize_phone,
    normalize_skill,
    normalize_skills,
)


# --------------------------------------------------------------------------- #
# phones
# --------------------------------------------------------------------------- #
class TestNormalizePhone:
    def test_bare_national_number_default_region_in(self) -> None:
        assert normalize_phone("8492948175") == "+918492948175"

    def test_already_international_indian(self) -> None:
        assert normalize_phone("+91 8492948175") == "+918492948175"

    def test_embedded_country_code_overrides_default_region(self) -> None:
        # A "+1" US number stays US even though default_region is IN.
        assert normalize_phone("+1 415 555 2671") == "+14155552671"

    def test_national_number_with_explicit_us_region(self) -> None:
        assert normalize_phone("(415) 555-2671", default_region="US") == "+14155552671"

    @pytest.mark.parametrize("garbage", ["call me", "N/A", "", "   ", "abcdefg"])
    def test_garbage_returns_none(self, garbage: str) -> None:
        assert normalize_phone(garbage) is None

    def test_deterministic(self) -> None:
        assert normalize_phone("8492948175") == normalize_phone("8492948175")


# --------------------------------------------------------------------------- #
# dates
# --------------------------------------------------------------------------- #
class TestNormalizeDate:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("Jan 2024", "2024-01"),
            ("January 2024", "2024-01"),
            ("2024", "2024-01"),
            ("2024-03", "2024-03"),
            ("2024/03", "2024-03"),
            ("03/2024", "2024-03"),
            ("03-2024", "2024-03"),
            ("Dec 2020", "2020-12"),
        ],
    )
    def test_supported_forms(self, raw: str, expected: str) -> None:
        assert normalize_date(raw) == expected

    @pytest.mark.parametrize("garbage", ["", "   ", "not a date", "hello", "13/2024"])
    def test_garbage_returns_none(self, garbage: str) -> None:
        assert normalize_date(garbage) is None

    def test_year_only_is_independent_of_current_date(self) -> None:
        # Determinism guarantee: a year-only input always maps to month 01,
        # regardless of when the test runs (no datetime.now() involvement).
        assert normalize_date("2024") == "2024-01"
        assert normalize_date("1999") == "1999-01"

    def test_deterministic(self) -> None:
        assert normalize_date("Jan 2024") == normalize_date("Jan 2024")


# --------------------------------------------------------------------------- #
# skills
# --------------------------------------------------------------------------- #
class TestNormalizeSkill:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("ReactJS", "React"),
            ("React.js", "React"),
            ("reactjs", "React"),
            ("  react js  ", "React"),
            ("JS", "JavaScript"),
            ("k8s", "Kubernetes"),
            ("py", "Python"),
            ("Golang", "Go"),
        ],
    )
    def test_known_aliases(self, raw: str, expected: str) -> None:
        assert normalize_skill(raw) == expected

    def test_unknown_skill_stays_unchanged(self) -> None:
        assert normalize_skill("Rust") == "Rust"

    def test_unknown_skill_is_trimmed(self) -> None:
        assert normalize_skill("  Rust  ") == "Rust"

    def test_list_preserves_order_and_does_not_dedupe(self) -> None:
        out = normalize_skills(["ReactJS", "py", "Rust", "React.js"])
        assert out == ["React", "Python", "Rust", "React"]

    def test_deterministic(self) -> None:
        assert normalize_skill("ReactJS") == normalize_skill("ReactJS")


# --------------------------------------------------------------------------- #
# country (optional helper)
# --------------------------------------------------------------------------- #
class TestNormalizeCountry:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("United States", "US"),
            ("usa", "US"),
            ("US", "US"),
            ("india", "IN"),
            ("UK", "GB"),
        ],
    )
    def test_known_countries(self, raw: str, expected: str) -> None:
        assert normalize_country(raw) == expected

    @pytest.mark.parametrize("garbage", ["", "   ", "Atlantis", "ZZ"])
    def test_unknown_returns_none(self, garbage: str) -> None:
        assert normalize_country(garbage) is None
