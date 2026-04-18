"""TypedDict output schemas for every MCP tool.

These schemas are read by FastMCP (``structured_output=True``) to generate
a JSON-Schema ``outputSchema`` for each tool. Clients that support
structured data use that schema to validate the ``structuredContent``
payload; clients that don't use the markdown ``content`` block instead.

**Python / Pydantic compat note.** We deliberately avoid
``Required`` / ``NotRequired`` qualifiers: Pydantic 2.13+ mishandles them
during runtime schema generation on Py < 3.12 (see
https://errors.pydantic.dev/2.13/u/typed-dict-version). Optional fields use
``| None`` convention; the code always sets the key (``None`` when absent).
"""

from __future__ import annotations

import sys

if sys.version_info >= (3, 12):
    from typing import TypedDict
else:
    from typing_extensions import TypedDict


# ── Projects ────────────────────────────────────────────────────────────────


class ProjectSummary(TypedDict):
    key: str
    name: str
    qualifier: str
    visibility: str | None
    last_analysis: str | None


class ProjectsListOutput(TypedDict):
    projects_count: int
    total: int
    page: int
    page_size: int
    has_more: bool
    next_page: int | None
    query: str | None
    projects: list[ProjectSummary]


# ── Project metrics ─────────────────────────────────────────────────────────


class MeasureValue(TypedDict):
    metric: str
    value: str | None
    best_value: bool | None


class ProjectMetricsOutput(TypedDict):
    project_key: str
    project_name: str | None
    qualifier: str | None
    measures_count: int
    measures: list[MeasureValue]
    measures_by_metric: dict[str, str | None]


# ── Quality Gate ────────────────────────────────────────────────────────────


class QualityGateCondition(TypedDict):
    metric: str
    status: str
    actual: str | None
    comparator: str | None
    error_threshold: str | None


class QualityGateStatusOutput(TypedDict):
    project_key: str
    status: str
    passed: bool
    conditions_count: int
    failing_conditions: int
    conditions: list[QualityGateCondition]


# ── Issues ──────────────────────────────────────────────────────────────────


class IssueItem(TypedDict):
    key: str
    rule: str
    severity: str
    type: str
    status: str
    component: str
    line: int | None
    message: str
    author: str | None
    creation_date: str | None
    effort: str | None


class IssuesOutput(TypedDict):
    project_key: str
    total: int
    returned: int
    page: int
    page_size: int
    has_more: bool
    next_page: int | None
    by_severity: dict[str, int]
    by_type: dict[str, int]
    issues: list[IssueItem]


# ── Worst metrics ───────────────────────────────────────────────────────────


class WorstMetricRow(TypedDict):
    project_key: str
    project_name: str | None
    value: str | None
    numeric_value: float | None


class WorstMetricsOutput(TypedDict):
    metric: str
    direction: str
    limit: int
    candidates_scanned: int
    ranked_count: int
    query: str | None
    ranked: list[WorstMetricRow]
