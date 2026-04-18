"""Unit tests for pure shaping helpers in :mod:`sonarqube_mcp.tools`.

These functions take raw SonarQube API dicts and shape them into the
TypedDict output schemas or perform simple validation. They have no I/O,
so we exercise them directly without mocking any HTTP client.
"""

from __future__ import annotations

import pytest

from sonarqube_mcp.tools import (
    _direction,
    _parse_float,
    _shape_project,
    _short,
    _validate_list_against,
)


class TestShort:
    def test_trims_to_19(self) -> None:
        assert _short("2026-04-18T12:34:56.789+00:00") == "2026-04-18T12:34:56"

    def test_shorter_than_limit_is_unchanged(self) -> None:
        assert _short("abc") == "abc"

    def test_custom_length(self) -> None:
        assert _short("abcdefg", n=3) == "abc"

    def test_none_returns_none(self) -> None:
        assert _short(None) is None

    def test_empty_returns_none(self) -> None:
        assert _short("") is None


class TestParseFloat:
    def test_integer_string(self) -> None:
        assert _parse_float("42") == 42.0

    def test_float_string(self) -> None:
        assert _parse_float("85.7") == 85.7

    def test_rating_string(self) -> None:
        assert _parse_float("3") == 3.0

    def test_none_returns_none(self) -> None:
        assert _parse_float(None) is None

    def test_empty_returns_none(self) -> None:
        assert _parse_float("") is None

    def test_non_numeric_returns_none(self) -> None:
        assert _parse_float("OK") is None
        assert _parse_float("abc") is None


class TestShapeProject:
    def test_full_component(self) -> None:
        raw = {
            "key": "einvy:aut_einvy",
            "name": "EINVY autotests",
            "qualifier": "TRK",
            "visibility": "private",
            "lastAnalysisDate": "2026-04-18T09:10:11+0300",
        }
        shaped = _shape_project(raw)
        assert shaped["key"] == "einvy:aut_einvy"
        assert shaped["name"] == "EINVY autotests"
        assert shaped["qualifier"] == "TRK"
        assert shaped["visibility"] == "private"
        assert shaped["last_analysis"] == "2026-04-18T09:10:11"

    def test_falls_back_to_analysis_date(self) -> None:
        raw = {
            "key": "x",
            "name": "X",
            "analysisDate": "2026-04-18T00:00:00Z",
        }
        shaped = _shape_project(raw)
        assert shaped["last_analysis"] == "2026-04-18T00:00:00"

    def test_missing_fields_default(self) -> None:
        shaped = _shape_project({"key": "k"})
        assert shaped["key"] == "k"
        assert shaped["name"] == ""
        assert shaped["qualifier"] == "TRK"
        assert shaped["visibility"] is None
        assert shaped["last_analysis"] is None


class TestValidateList:
    def test_none_returns_empty(self) -> None:
        assert _validate_list_against(None, ("A", "B"), "kind") == []

    def test_empty_returns_empty(self) -> None:
        assert _validate_list_against([], ("A", "B"), "kind") == []

    def test_uppercases_and_strips(self) -> None:
        assert _validate_list_against(["  blocker ", "critical"], ("BLOCKER", "CRITICAL"), "severity") == [
            "BLOCKER",
            "CRITICAL",
        ]

    def test_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown severity"):
            _validate_list_against(["XYZ"], ("BLOCKER", "CRITICAL"), "severity")

    def test_partial_unknown_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown issue type"):
            _validate_list_against(["BUG", "NOT_A_TYPE"], ("BUG", "VULNERABILITY"), "issue type")

    def test_ignores_blank_strings(self) -> None:
        out = _validate_list_against(["BUG", "", "   ", "VULNERABILITY"], ("BUG", "VULNERABILITY"), "issue type")
        assert out == ["BUG", "VULNERABILITY"]


class TestDirection:
    def test_higher_is_worse_metric(self) -> None:
        assert _direction("bugs") == "higher is worse"
        assert _direction("vulnerabilities") == "higher is worse"
        assert _direction("sqale_rating") == "higher is worse"

    def test_lower_is_worse_metric(self) -> None:
        assert _direction("coverage") == "lower is worse"
        assert _direction("tests") == "lower is worse"
