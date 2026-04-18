"""Integration tests for the five MCP tools.

We exercise each tool end-to-end via its public function, mocking the
SonarQube HTTP layer with :mod:`responses`. The goal is to cover the
happy path and key edge cases (pagination, empty result, filter
validation, metric direction).

These tests don't spin up a full MCP server — they call the decorated
tool functions directly, which is sufficient because our tools contain
the business logic; ``@mcp.tool`` only registers them with FastMCP.
"""

from __future__ import annotations

import pytest
import responses
from mcp.server.fastmcp.exceptions import ToolError

from sonarqube_mcp import _mcp
from sonarqube_mcp.tools import (
    sonarqube_get_issues,
    sonarqube_list_projects,
    sonarqube_project_metrics,
    sonarqube_quality_gate_status,
    sonarqube_worst_metrics,
)

BASE = "https://sonar.example.com"


@pytest.fixture(autouse=True)
def configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set env vars + reset the module-global client cache per-test."""
    monkeypatch.setenv("SONARQUBE_URL", BASE)
    monkeypatch.setenv("SONARQUBE_TOKEN", "t")  # pragma: allowlist secret
    monkeypatch.setenv("SONARQUBE_SSL_VERIFY", "true")
    with _mcp._client_lock:
        if _mcp._client is not None:
            try:
                _mcp._client.close()
            except Exception:
                pass
        _mcp._client = None
    yield
    with _mcp._client_lock:
        if _mcp._client is not None:
            try:
                _mcp._client.close()
            except Exception:
                pass
        _mcp._client = None


# ── sonarqube_list_projects ────────────────────────────────────────────────


@responses.activate
def test_list_projects_happy_path() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/components/search",
        json={
            "components": [
                {"key": "einvy:aut_einvy", "name": "EINVY AutotEsts", "qualifier": "TRK"},
                {"key": "einvy:qa_assistant", "name": "QA Assistant", "qualifier": "TRK"},
            ],
            "paging": {"pageIndex": 1, "pageSize": 50, "total": 2},
        },
        status=200,
    )
    result = sonarqube_list_projects(query="einvy", page=1, page_size=50)
    data = result.structuredContent
    assert data["projects_count"] == 2
    assert data["total"] == 2
    assert data["has_more"] is False
    assert data["projects"][0]["key"] == "einvy:aut_einvy"
    assert data["query"] == "einvy"


@responses.activate
def test_list_projects_has_more_pagination() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/components/search",
        json={
            "components": [{"key": f"k{i}", "name": f"N{i}", "qualifier": "TRK"} for i in range(10)],
            "paging": {"pageIndex": 1, "pageSize": 10, "total": 42},
        },
        status=200,
    )
    result = sonarqube_list_projects(page=1, page_size=10)
    data = result.structuredContent
    assert data["projects_count"] == 10
    assert data["total"] == 42
    assert data["has_more"] is True
    assert data["next_page"] == 2


@responses.activate
def test_list_projects_empty() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/components/search",
        json={"components": [], "paging": {"total": 0}},
        status=200,
    )
    result = sonarqube_list_projects(query="nonexistent")
    data = result.structuredContent
    assert data["projects_count"] == 0
    assert data["has_more"] is False
    assert data["next_page"] is None


@responses.activate
def test_list_projects_401_raises_tool_error() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/components/search",
        json={"errors": [{"msg": "Unauthorized"}]},
        status=401,
    )
    with pytest.raises(ToolError, match="401"):
        sonarqube_list_projects()


# ── sonarqube_project_metrics ──────────────────────────────────────────────


@responses.activate
def test_project_metrics_default_keys() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/measures/component",
        json={
            "component": {
                "key": "einvy:aut_einvy",
                "name": "EINVY autotests",
                "qualifier": "TRK",
                "measures": [
                    {"metric": "bugs", "value": "12"},
                    {"metric": "coverage", "value": "78.4"},
                    {"metric": "alert_status", "value": "ERROR"},
                ],
            }
        },
        status=200,
    )
    result = sonarqube_project_metrics(project_key="einvy:aut_einvy")
    data = result.structuredContent
    assert data["project_key"] == "einvy:aut_einvy"
    assert data["project_name"] == "EINVY autotests"
    assert data["measures_count"] == 3
    assert data["measures_by_metric"]["bugs"] == "12"
    assert data["measures_by_metric"]["coverage"] == "78.4"
    assert data["measures_by_metric"]["alert_status"] == "ERROR"


@responses.activate
def test_project_metrics_custom_keys() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/measures/component",
        json={"component": {"measures": [{"metric": "ncloc", "value": "150000"}]}},
        status=200,
    )
    result = sonarqube_project_metrics(project_key="proj", metric_keys=["ncloc"])
    data = result.structuredContent
    assert data["measures_by_metric"] == {"ncloc": "150000"}
    # Verify the right metricKeys param was passed.
    assert "metricKeys=ncloc" in responses.calls[0].request.url


@responses.activate
def test_project_metrics_forwards_branch_param() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/measures/component",
        json={"component": {"measures": []}},
        status=200,
    )
    sonarqube_project_metrics(project_key="einvy", branch="feature/xyz")
    url = responses.calls[0].request.url
    assert "branch=feature" in url and "xyz" in url


@responses.activate
def test_project_metrics_forwards_pr_param() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/measures/component",
        json={"component": {"measures": []}},
        status=200,
    )
    sonarqube_project_metrics(project_key="einvy", pull_request="42")
    url = responses.calls[0].request.url
    assert "pullRequest=42" in url


def test_project_metrics_rejects_branch_and_pr_together() -> None:
    with pytest.raises(ToolError, match="mutually exclusive"):
        sonarqube_project_metrics(project_key="einvy", branch="main", pull_request="42")


@responses.activate
def test_project_metrics_404() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/measures/component",
        json={"errors": [{"msg": "Component not found"}]},
        status=404,
    )
    with pytest.raises(ToolError, match="404"):
        sonarqube_project_metrics(project_key="nonexistent")


# ── sonarqube_quality_gate_status ──────────────────────────────────────────


@responses.activate
def test_quality_gate_passed() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/qualitygates/project_status",
        json={
            "projectStatus": {
                "status": "OK",
                "conditions": [
                    {
                        "metricKey": "new_coverage",
                        "status": "OK",
                        "actualValue": "85",
                        "comparator": "LT",
                        "errorThreshold": "80",
                    }
                ],
            }
        },
        status=200,
    )
    result = sonarqube_quality_gate_status(project_key="einvy")
    data = result.structuredContent
    assert data["status"] == "OK"
    assert data["passed"] is True
    assert data["failing_conditions"] == 0
    assert data["conditions_count"] == 1


@responses.activate
def test_quality_gate_failed_with_conditions() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/qualitygates/project_status",
        json={
            "projectStatus": {
                "status": "ERROR",
                "conditions": [
                    {
                        "metricKey": "new_coverage",
                        "status": "ERROR",
                        "actualValue": "40",
                        "comparator": "LT",
                        "errorThreshold": "80",
                    },
                    {
                        "metricKey": "new_bugs",
                        "status": "OK",
                        "actualValue": "0",
                        "comparator": "GT",
                        "errorThreshold": "0",
                    },
                ],
            }
        },
        status=200,
    )
    result = sonarqube_quality_gate_status(project_key="einvy")
    data = result.structuredContent
    assert data["status"] == "ERROR"
    assert data["passed"] is False
    assert data["failing_conditions"] == 1
    assert data["conditions_count"] == 2
    assert data["conditions"][0]["metric"] == "new_coverage"
    assert data["conditions"][0]["actual"] == "40"


@responses.activate
def test_quality_gate_forwards_branch_param() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/qualitygates/project_status",
        json={"projectStatus": {"status": "OK", "conditions": []}},
        status=200,
    )
    sonarqube_quality_gate_status(project_key="einvy", branch="feature/xyz")
    url = responses.calls[0].request.url
    assert "branch=feature" in url and "xyz" in url


@responses.activate
def test_quality_gate_forwards_pr_param() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/qualitygates/project_status",
        json={"projectStatus": {"status": "ERROR", "conditions": []}},
        status=200,
    )
    result = sonarqube_quality_gate_status(project_key="einvy", pull_request="42")
    url = responses.calls[0].request.url
    assert "pullRequest=42" in url
    assert result.structuredContent["passed"] is False


def test_quality_gate_rejects_branch_and_pr_together() -> None:
    with pytest.raises(ToolError, match="mutually exclusive"):
        sonarqube_quality_gate_status(project_key="einvy", branch="main", pull_request="42")


@responses.activate
def test_quality_gate_no_gate_attached() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/qualitygates/project_status",
        json={"projectStatus": {"status": "NONE", "conditions": []}},
        status=200,
    )
    result = sonarqube_quality_gate_status(project_key="einvy")
    data = result.structuredContent
    assert data["status"] == "NONE"
    assert data["passed"] is False
    assert data["conditions_count"] == 0


# ── sonarqube_get_issues ───────────────────────────────────────────────────


@responses.activate
def test_get_issues_happy_path() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/issues/search",
        json={
            "total": 2,
            "issues": [
                {
                    "key": "AYx-1",
                    "rule": "python:S1192",
                    "severity": "CRITICAL",
                    "type": "BUG",
                    "status": "OPEN",
                    "component": "einvy:src/foo.py",
                    "line": 42,
                    "message": "Duplicate literal",
                    "creationDate": "2026-04-18T12:00:00+0300",
                    "effort": "10min",
                },
                {
                    "key": "AYx-2",
                    "rule": "python:S125",
                    "severity": "MAJOR",
                    "type": "CODE_SMELL",
                    "status": "OPEN",
                    "component": "einvy:src/bar.py",
                    "line": 7,
                    "message": "Remove commented out code",
                },
            ],
        },
        status=200,
    )
    result = sonarqube_get_issues(project_key="einvy")
    data = result.structuredContent
    assert data["total"] == 2
    assert data["returned"] == 2
    assert data["by_severity"] == {"CRITICAL": 1, "MAJOR": 1}
    assert data["by_type"] == {"BUG": 1, "CODE_SMELL": 1}
    assert data["has_more"] is False
    assert data["issues"][0]["line"] == 42


@responses.activate
def test_get_issues_with_filters() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/issues/search",
        json={"total": 0, "issues": []},
        status=200,
    )
    sonarqube_get_issues(
        project_key="einvy",
        severities=["blocker", "CRITICAL"],
        types=["VULNERABILITY"],
    )
    url = responses.calls[0].request.url
    assert "severities=BLOCKER%2CCRITICAL" in url or "severities=BLOCKER,CRITICAL" in url
    assert "types=VULNERABILITY" in url
    assert "resolved=false" in url


def test_get_issues_rejects_invalid_severity() -> None:
    with pytest.raises(ToolError, match="Unknown severity"):
        sonarqube_get_issues(project_key="einvy", severities=["SEVERE"])


def test_get_issues_rejects_invalid_type() -> None:
    with pytest.raises(ToolError, match="Unknown issue type"):
        sonarqube_get_issues(project_key="einvy", types=["SECURITY"])


def test_get_issues_rejects_security_hotspot_type() -> None:
    """SECURITY_HOTSPOT is intentionally not supported on /issues/search."""
    with pytest.raises(ToolError, match="Unknown issue type"):
        sonarqube_get_issues(project_key="einvy", types=["SECURITY_HOTSPOT"])


def test_get_issues_rejects_branch_and_pr_together() -> None:
    with pytest.raises(ToolError, match="mutually exclusive"):
        sonarqube_get_issues(project_key="einvy", branch="feature/x", pull_request="42")


@responses.activate
def test_get_issues_forwards_branch_param() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/issues/search",
        json={"total": 0, "issues": []},
        status=200,
    )
    sonarqube_get_issues(project_key="einvy", branch="feature/xyz")
    url = responses.calls[0].request.url
    # Branch may be URL-encoded (%2F) or raw depending on requests version.
    assert "branch=feature" in url and ("xyz" in url)


@responses.activate
def test_get_issues_forwards_pull_request_param() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/issues/search",
        json={"total": 0, "issues": []},
        status=200,
    )
    sonarqube_get_issues(project_key="einvy", pull_request="42")
    url = responses.calls[0].request.url
    assert "pullRequest=42" in url


@responses.activate
def test_get_issues_markdown_shows_truncation_hint() -> None:
    # 100 issues — markdown limit is 40, so the hint must appear.
    responses.add(
        responses.GET,
        f"{BASE}/api/issues/search",
        json={
            "total": 100,
            "issues": [
                {
                    "key": f"k{i}",
                    "rule": "r",
                    "severity": "MAJOR",
                    "type": "BUG",
                    "status": "OPEN",
                    "component": "c",
                    "message": f"msg {i}",
                }
                for i in range(100)
            ],
        },
        status=200,
    )
    result = sonarqube_get_issues(project_key="einvy", page_size=100)
    markdown = result.content[0].text
    assert "Showing first 40 of 100" in markdown
    assert result.structuredContent["returned"] == 100


@responses.activate
def test_get_issues_pagination_indicator() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/issues/search",
        json={
            "total": 500,
            "issues": [
                {
                    "key": f"k{i}",
                    "rule": "r",
                    "severity": "MAJOR",
                    "type": "BUG",
                    "status": "OPEN",
                    "component": "c",
                    "message": "m",
                }
                for i in range(100)
            ],
        },
        status=200,
    )
    result = sonarqube_get_issues(project_key="einvy", page=1, page_size=100)
    data = result.structuredContent
    assert data["has_more"] is True
    assert data["next_page"] == 2


# ── sonarqube_worst_metrics ────────────────────────────────────────────────


@responses.activate
def test_worst_metrics_higher_is_worse_sorts_descending() -> None:
    # Step 1 — project discovery.
    responses.add(
        responses.GET,
        f"{BASE}/api/components/search",
        json={
            "components": [
                {"key": "a", "name": "A"},
                {"key": "b", "name": "B"},
                {"key": "c", "name": "C"},
            ],
            "paging": {"total": 3},
        },
        status=200,
    )
    # Step 2 — bulk measures.
    responses.add(
        responses.GET,
        f"{BASE}/api/measures/search",
        json={
            "measures": [
                {"component": "a", "metric": "bugs", "value": "10"},
                {"component": "b", "metric": "bugs", "value": "100"},
                {"component": "c", "metric": "bugs", "value": "50"},
            ]
        },
        status=200,
    )
    result = sonarqube_worst_metrics(metric="bugs", limit=3)
    data = result.structuredContent
    assert data["direction"] == "higher is worse"
    assert data["candidates_scanned"] == 3
    ranked_keys = [r["project_key"] for r in data["ranked"]]
    # Descending: most bugs first.
    assert ranked_keys == ["b", "c", "a"]


@responses.activate
def test_worst_metrics_lower_is_worse_sorts_ascending() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/components/search",
        json={
            "components": [
                {"key": "a", "name": "A"},
                {"key": "b", "name": "B"},
                {"key": "c", "name": "C"},
            ],
            "paging": {"total": 3},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/measures/search",
        json={
            "measures": [
                {"component": "a", "metric": "coverage", "value": "85"},
                {"component": "b", "metric": "coverage", "value": "20"},
                {"component": "c", "metric": "coverage", "value": "50"},
            ]
        },
        status=200,
    )
    result = sonarqube_worst_metrics(metric="coverage", limit=2)
    data = result.structuredContent
    assert data["direction"] == "lower is worse"
    ranked_keys = [r["project_key"] for r in data["ranked"]]
    # Ascending: worst (lowest) coverage first, limit 2.
    assert ranked_keys == ["b", "c"]
    assert data["ranked_count"] == 2


@responses.activate
def test_worst_metrics_missing_measures_sink_to_bottom() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/components/search",
        json={
            "components": [
                {"key": "a", "name": "A"},
                {"key": "b", "name": "B"},
                {"key": "c", "name": "C"},
            ],
            "paging": {"total": 3},
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"{BASE}/api/measures/search",
        json={
            "measures": [
                {"component": "a", "metric": "bugs", "value": "5"},
                # 'b' and 'c' have no measure at all — sink to bottom.
            ]
        },
        status=200,
    )
    result = sonarqube_worst_metrics(metric="bugs", limit=3)
    data = result.structuredContent
    ranked = data["ranked"]
    assert ranked[0]["project_key"] == "a"
    assert ranked[0]["value"] == "5"
    # Others missing — None values at end, order among them unspecified but both present.
    assert {ranked[1]["project_key"], ranked[2]["project_key"]} == {"b", "c"}
    for tail in ranked[1:]:
        assert tail["numeric_value"] is None
        assert tail["value"] is None


@responses.activate
def test_worst_metrics_empty_pool() -> None:
    responses.add(
        responses.GET,
        f"{BASE}/api/components/search",
        json={"components": [], "paging": {"total": 0}},
        status=200,
    )
    result = sonarqube_worst_metrics(metric="bugs", limit=5, query="nothing")
    data = result.structuredContent
    assert data["candidates_scanned"] == 0
    assert data["ranked_count"] == 0
    assert data["ranked"] == []
