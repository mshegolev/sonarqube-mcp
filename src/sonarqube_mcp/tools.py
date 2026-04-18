"""MCP tools for SonarQube.

5 read-only tools covering the SonarQube Web API surface most useful to an
agent helping a developer triage code-quality state:

- ``sonarqube_list_projects``       — discover project keys
- ``sonarqube_project_metrics``     — fetch headline measures for one project
- ``sonarqube_quality_gate_status`` — QG status + per-condition breakdown
- ``sonarqube_get_issues``          — search issues with filters
- ``sonarqube_worst_metrics``       — rank projects by worst value of a metric

**Threading model.** All tools are synchronous ``def``. FastMCP runs them
in a worker thread via ``anyio.to_thread.run_sync``, so blocking HTTP
calls don't block the asyncio event loop.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from sonarqube_mcp import output
from sonarqube_mcp._mcp import get_client, mcp
from sonarqube_mcp.client import (
    DEFAULT_METRIC_KEYS,
    METRICS_HIGHER_IS_WORSE,
    VALID_ISSUE_TYPES,
    VALID_SEVERITIES,
)
from sonarqube_mcp.models import (
    IssueItem,
    IssuesOutput,
    MeasureValue,
    ProjectMetricsOutput,
    ProjectsListOutput,
    ProjectSummary,
    QualityGateCondition,
    QualityGateStatusOutput,
    WorstMetricRow,
    WorstMetricsOutput,
)

# ── Helpers ────────────────────────────────────────────────────────────────


def _short(s: str | None, n: int = 19) -> str | None:
    """Return ``s[:n]`` or ``None``. SonarQube timestamps are ISO-8601 with
    a timezone offset; we trim to seconds-resolution for readability."""
    return s[:n] if s else None


def _shape_project(p: dict[str, Any]) -> ProjectSummary:
    """Convert SonarQube's component/project JSON into :class:`ProjectSummary`."""
    return {
        "key": p.get("key", ""),
        "name": p.get("name", ""),
        "qualifier": p.get("qualifier", "TRK"),
        "visibility": p.get("visibility"),
        "last_analysis": _short(p.get("lastAnalysisDate") or p.get("analysisDate")),
    }


def _parse_float(value: str | None) -> float | None:
    """Parse a SonarQube metric value into a float, or ``None`` on failure.

    SonarQube returns metric values as strings (``"123"``, ``"85.7"``,
    ``"3"`` for ratings). We need a numeric form for sorting in
    ``sonarqube_worst_metrics``.
    """
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _validate_list_against(values: list[str] | None, valid: tuple[str, ...], label: str) -> list[str]:
    """Uppercase and validate a list of SonarQube filter values.

    Raises :class:`ValueError` if any value is outside ``valid``; lets the
    standard ``output.fail`` hand back a usable message. ``None`` / empty
    list means no filter and returns ``[]``.
    """
    if not values:
        return []
    upper = [v.strip().upper() for v in values if v and v.strip()]
    unknown = sorted(set(upper) - set(valid))
    if unknown:
        raise ValueError(
            f"Unknown {label}: {unknown}. Valid values: {list(valid)}",
        )
    return upper


# ── Tools ──────────────────────────────────────────────────────────────────


@mcp.tool(
    name="sonarqube_list_projects",
    annotations={
        "title": "List Projects",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def sonarqube_list_projects(
    query: Annotated[
        str | None,
        Field(
            default=None,
            max_length=200,
            description=(
                "Optional substring to filter project keys or names (case-insensitive). "
                "Example: 'einvy' matches any project containing that substring."
            ),
        ),
    ] = None,
    page: Annotated[int, Field(default=1, ge=1, le=1000, description="Page number (1-based).")] = 1,
    page_size: Annotated[
        int,
        Field(default=50, ge=1, le=500, description="Items per page (1-500)."),
    ] = 50,
) -> ProjectsListOutput:
    """List SonarQube projects (components with qualifier ``TRK``).

    Use this first to discover which project keys exist before calling
    ``sonarqube_project_metrics`` or ``sonarqube_get_issues``.

    Pagination: if ``has_more`` is ``True``, call again with ``page + 1``.
    Results are sorted by SonarQube default order (component name ascending).

    Returns:
        dict with keys ``projects_count`` / ``total`` / ``page`` /
        ``page_size`` / ``has_more`` / ``next_page`` / ``query`` /
        ``projects`` (list).
    """
    try:
        client = get_client()
        params: dict[str, Any] = {
            "qualifiers": "TRK",
            "p": page,
            "ps": page_size,
        }
        if query:
            params["q"] = query

        data = client.get("/components/search", params=params) or {}
        raw_components: list[dict[str, Any]] = data.get("components") or []
        paging = data.get("paging") or {}
        total = int(paging.get("total") or 0)

        projects: list[ProjectSummary] = [_shape_project(p) for p in raw_components]
        has_more = bool(total and page * page_size < total)

        result: ProjectsListOutput = {
            "projects_count": len(projects),
            "total": total,
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
            "query": query,
            "projects": projects,
        }
        heading = f"## Projects — page {page} ({len(projects)} of {total} total, has_more={has_more})" + (
            f" — query={query!r}" if query else ""
        )
        md = (
            heading
            + "\n\n"
            + "\n".join(
                [f"- **{p['key']}** — {p['name']} (last analysis: {p['last_analysis'] or 'never'})" for p in projects]
            )
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, "listing SonarQube projects")


@mcp.tool(
    name="sonarqube_project_metrics",
    annotations={
        "title": "Project Metrics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def sonarqube_project_metrics(
    project_key: Annotated[
        str,
        Field(min_length=1, max_length=400, description="SonarQube project key (e.g. 'einvy:aut_einvy')."),
    ],
    metric_keys: Annotated[
        list[str] | None,
        Field(
            default=None,
            description=(
                "Metric keys to fetch (e.g. ['bugs', 'coverage', 'sqale_rating']). "
                "If omitted, a sensible default set is used: bugs, code_smells, coverage, "
                "vulnerabilities, ratings, ncloc, tests, alert_status."
            ),
        ),
    ] = None,
    branch: Annotated[
        str | None,
        Field(
            default=None,
            max_length=255,
            description=(
                "Branch name to query (e.g. 'feature/xyz'). If omitted, the project's main "
                "branch is used. Mutually exclusive with pull_request."
            ),
        ),
    ] = None,
    pull_request: Annotated[
        str | None,
        Field(
            default=None,
            max_length=255,
            description=(
                "Pull request identifier (e.g. '42'). If set, fetches measures from the PR "
                "decoration analysis. Mutually exclusive with branch."
            ),
        ),
    ] = None,
) -> ProjectMetricsOutput:
    """Fetch measures for a single project.

    Wraps ``/api/measures/component``. Returns both the raw list
    (``measures``) and a dict keyed by metric name (``measures_by_metric``)
    — handy when the agent wants to look up a single value quickly.

    To find valid metric keys, call with the default set first — SonarQube
    ignores unknown metric keys and returns what it knows.
    """
    try:
        if branch and pull_request:
            raise ValueError("branch and pull_request are mutually exclusive — pass one or the other, not both")

        client = get_client()
        keys = metric_keys if metric_keys else list(DEFAULT_METRIC_KEYS)
        params: dict[str, Any] = {"component": project_key, "metricKeys": ",".join(keys)}
        if branch:
            params["branch"] = branch
        if pull_request:
            params["pullRequest"] = pull_request

        data = client.get("/measures/component", params=params) or {}
        component = data.get("component") or {}
        raw_measures: list[dict[str, Any]] = component.get("measures") or []

        measures: list[MeasureValue] = [
            {
                "metric": m.get("metric", ""),
                "value": m.get("value"),
                "best_value": m.get("bestValue"),
            }
            for m in raw_measures
        ]
        by_metric: dict[str, str | None] = {m["metric"]: m["value"] for m in measures if m["metric"]}

        result: ProjectMetricsOutput = {
            "project_key": project_key,
            "project_name": component.get("name"),
            "qualifier": component.get("qualifier"),
            "measures_count": len(measures),
            "measures": measures,
            "measures_by_metric": by_metric,
        }
        md = (
            f"## Metrics for {project_key}\n\n"
            + (f"Project: {component.get('name', '?')}\n\n" if component.get("name") else "")
            + "\n".join([f"- **{m['metric']}** = {m['value']}" for m in measures])
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"fetching metrics for {project_key}")


@mcp.tool(
    name="sonarqube_quality_gate_status",
    annotations={
        "title": "Quality Gate Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def sonarqube_quality_gate_status(
    project_key: Annotated[
        str,
        Field(min_length=1, max_length=400, description="SonarQube project key."),
    ],
    branch: Annotated[
        str | None,
        Field(
            default=None,
            max_length=255,
            description=(
                "Branch name to check (e.g. 'feature/xyz'). If omitted, the main branch's gate "
                "status is returned. Mutually exclusive with pull_request."
            ),
        ),
    ] = None,
    pull_request: Annotated[
        str | None,
        Field(
            default=None,
            max_length=255,
            description=(
                "Pull request identifier (e.g. '42'). Returns the PR's gate status from the "
                "decoration analysis. Mutually exclusive with branch."
            ),
        ),
    ] = None,
) -> QualityGateStatusOutput:
    """Fetch the Quality Gate status for a project.

    Wraps ``/api/qualitygates/project_status``. Returns the overall status
    (``OK`` / ``WARN`` / ``ERROR`` / ``NONE``) plus a per-condition
    breakdown — exactly what's needed for "why is my QG failing?" or
    "is PR #42 passing the gate?" queries.

    ``NONE`` means the project exists but has no Quality Gate attached or
    no analysis yet.
    """
    try:
        if branch and pull_request:
            raise ValueError("branch and pull_request are mutually exclusive — pass one or the other, not both")

        client = get_client()
        params: dict[str, Any] = {"projectKey": project_key}
        if branch:
            params["branch"] = branch
        if pull_request:
            params["pullRequest"] = pull_request

        data = client.get("/qualitygates/project_status", params=params) or {}
        project_status = data.get("projectStatus") or {}
        status = project_status.get("status", "NONE")
        conditions_raw: list[dict[str, Any]] = project_status.get("conditions") or []

        conditions: list[QualityGateCondition] = [
            {
                "metric": c.get("metricKey", ""),
                "status": c.get("status", "NONE"),
                "actual": c.get("actualValue"),
                "comparator": c.get("comparator"),
                "error_threshold": c.get("errorThreshold"),
            }
            for c in conditions_raw
        ]
        failing = sum(1 for c in conditions if c["status"] == "ERROR")
        passed = status == "OK"

        result: QualityGateStatusOutput = {
            "project_key": project_key,
            "status": status,
            "passed": passed,
            "conditions_count": len(conditions),
            "failing_conditions": failing,
            "conditions": conditions,
        }
        icon = "✅" if passed else ("⚠" if status == "WARN" else "❌")
        md = (
            f"## {icon} Quality Gate for {project_key}: **{status}**\n\n"
            f"{failing} failing of {len(conditions)} conditions\n\n"
            + "\n".join(
                [
                    f"- **{c['metric']}** — {c['status']}"
                    + (f" (actual={c['actual']}, threshold={c['error_threshold']})" if c["status"] == "ERROR" else "")
                    for c in conditions
                ]
            )
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"fetching Quality Gate for {project_key}")


@mcp.tool(
    name="sonarqube_get_issues",
    annotations={
        "title": "Get Issues",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def sonarqube_get_issues(
    project_key: Annotated[
        str,
        Field(min_length=1, max_length=400, description="SonarQube project key to query issues for."),
    ],
    severities: Annotated[
        list[str] | None,
        Field(
            default=None,
            description=(
                "Filter by severity. Valid values: BLOCKER, CRITICAL, MAJOR, MINOR, INFO. "
                "Case-insensitive. Omit to return all severities."
            ),
        ),
    ] = None,
    types: Annotated[
        list[str] | None,
        Field(
            default=None,
            description=(
                "Filter by issue type. Valid values: BUG, VULNERABILITY, CODE_SMELL. "
                "Case-insensitive. Security Hotspots live on a separate API endpoint "
                "(not supported by this tool). Omit to return all supported types."
            ),
        ),
    ] = None,
    resolved: Annotated[
        bool,
        Field(
            default=False,
            description=(
                "Whether to include resolved issues. Default False — only unresolved issues, "
                "which is what an agent fixing code usually wants."
            ),
        ),
    ] = False,
    branch: Annotated[
        str | None,
        Field(
            default=None,
            max_length=255,
            description=(
                "Branch name to query (e.g. 'feature/xyz'). If omitted, the project's main "
                "branch is used. Mutually exclusive with pull_request."
            ),
        ),
    ] = None,
    pull_request: Annotated[
        str | None,
        Field(
            default=None,
            max_length=255,
            description=(
                "Pull request identifier (e.g. '42'). If set, fetches issues raised on the PR "
                "decoration analysis. Mutually exclusive with branch."
            ),
        ),
    ] = None,
    page: Annotated[int, Field(default=1, ge=1, le=200, description="Page number (1-based).")] = 1,
    page_size: Annotated[
        int,
        Field(
            default=100,
            ge=1,
            le=500,
            description="Items per page (1-500). SonarQube caps total pagination at 10 000.",
        ),
    ] = 100,
) -> IssuesOutput:
    """Search issues for a SonarQube project.

    Wraps ``/api/issues/search``. Use the filter parameters to narrow
    results — e.g. ``severities=['BLOCKER','CRITICAL']`` for triage, or
    ``types=['VULNERABILITY']`` for a security sweep.

    Pagination: if ``has_more`` is ``True``, call again with ``page + 1``.
    SonarQube caps total pagination at 10 000 issues; tighten the filters
    if you need to go deeper.
    """
    try:
        if branch and pull_request:
            raise ValueError("branch and pull_request are mutually exclusive — pass one or the other, not both")

        sev = _validate_list_against(severities, VALID_SEVERITIES, "severity")
        tps = _validate_list_against(types, VALID_ISSUE_TYPES, "issue type")

        client = get_client()
        params: dict[str, Any] = {
            "componentKeys": project_key,
            "resolved": "true" if resolved else "false",
            "p": page,
            "ps": page_size,
        }
        if sev:
            params["severities"] = ",".join(sev)
        if tps:
            params["types"] = ",".join(tps)
        if branch:
            params["branch"] = branch
        if pull_request:
            params["pullRequest"] = pull_request

        data = client.get("/issues/search", params=params) or {}
        raw_issues: list[dict[str, Any]] = data.get("issues") or []
        total = int(data.get("total") or (data.get("paging") or {}).get("total") or 0)

        issues: list[IssueItem] = []
        by_severity: dict[str, int] = {}
        by_type: dict[str, int] = {}
        for i in raw_issues:
            issue: IssueItem = {
                "key": i.get("key", ""),
                "rule": i.get("rule", ""),
                "severity": i.get("severity", ""),
                "type": i.get("type", ""),
                "status": i.get("status", ""),
                "component": i.get("component", ""),
                "line": i.get("line"),
                "message": i.get("message", ""),
                "author": i.get("author"),
                "creation_date": _short(i.get("creationDate")),
                "effort": i.get("effort"),
            }
            issues.append(issue)
            by_severity[issue["severity"]] = by_severity.get(issue["severity"], 0) + 1
            by_type[issue["type"]] = by_type.get(issue["type"], 0) + 1

        has_more = bool(total and page * page_size < total)
        result: IssuesOutput = {
            "project_key": project_key,
            "total": total,
            "returned": len(issues),
            "page": page,
            "page_size": page_size,
            "has_more": has_more,
            "next_page": page + 1 if has_more else None,
            "by_severity": by_severity,
            "by_type": by_type,
            "issues": issues,
        }
        header = (
            f"## Issues in {project_key} — page {page} "
            f"({len(issues)} of {total}, has_more={has_more})\n\n"
            f"By severity: {by_severity}\n\n"
            f"By type: {by_type}\n\n"
        )
        md_limit = 40
        md = header + "\n".join(
            [
                f"- [{i['severity']}/{i['type']}] **{i['component'].split(':')[-1]}:{i['line'] or '?'}** — "
                f"{i['message'][:140]}"
                for i in issues[:md_limit]
            ]
        )
        if len(issues) > md_limit:
            md += (
                f"\n\n_Showing first {md_limit} of {len(issues)} issues in the text rendering — "
                "see the `issues` field in the structured content for the full list._"
            )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"fetching issues for {project_key}")


@mcp.tool(
    name="sonarqube_worst_metrics",
    annotations={
        "title": "Worst Metrics Ranking",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
    structured_output=True,
)
def sonarqube_worst_metrics(
    metric: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description=(
                "Metric key to rank by. Common picks: 'bugs', 'vulnerabilities', 'code_smells', "
                "'coverage', 'duplicated_lines_density', 'sqale_rating', 'reliability_rating', "
                "'security_rating'."
            ),
        ),
    ],
    limit: Annotated[
        int,
        Field(default=10, ge=1, le=100, description="Top-N projects to return after ranking."),
    ] = 10,
    query: Annotated[
        str | None,
        Field(
            default=None,
            max_length=200,
            description=(
                "Optional substring to pre-filter projects by key or name before ranking. "
                "Highly recommended on large SonarQube instances."
            ),
        ),
    ] = None,
    candidate_pool: Annotated[
        int,
        Field(
            default=100,
            ge=1,
            le=500,
            description=(
                "How many projects to pull before ranking. Larger pool = more accurate ranking, "
                "slower response. Start at 100 and bump up if needed."
            ),
        ),
    ] = 100,
) -> WorstMetricsOutput:
    """Rank projects by the worst value of a single metric.

    Algorithm:
    1. Pull up to ``candidate_pool`` projects (optionally filtered by ``query``).
    2. Bulk-fetch ``metric`` for all of them in one ``/api/measures/search`` call.
    3. Sort descending or ascending depending on whether higher is worse
       (e.g. bugs → descending, coverage → ascending).
    4. Return the top ``limit``.

    For fine-grained metrics (``bugs``, ``vulnerabilities``, ``code_smells``,
    ratings, ``duplicated_lines_density``, ``open_issues``) higher is worse.
    For ``coverage``, ``tests``, ``line_coverage``, ``branch_coverage`` —
    lower is worse.
    """
    try:
        client = get_client()

        # Step 1 — discover candidate projects.
        search_params: dict[str, Any] = {"qualifiers": "TRK", "p": 1, "ps": candidate_pool}
        if query:
            search_params["q"] = query
        data = client.get("/components/search", params=search_params) or {}
        candidates = data.get("components") or []
        if not candidates:
            empty: WorstMetricsOutput = {
                "metric": metric,
                "direction": _direction(metric),
                "limit": limit,
                "candidates_scanned": 0,
                "ranked_count": 0,
                "query": query,
                "ranked": [],
            }
            return output.ok(empty, f"No projects found for query={query!r}")  # type: ignore[return-value]

        key_to_name = {c["key"]: c.get("name") for c in candidates if c.get("key")}
        project_keys = list(key_to_name.keys())

        # Step 2 — bulk fetch the chosen metric. ``/api/measures/search`` accepts
        # up to 100 project keys per call; batch.
        measures_by_key: dict[str, dict[str, Any]] = {}
        for i in range(0, len(project_keys), 100):
            batch = project_keys[i : i + 100]
            m_data = (
                client.get(
                    "/measures/search",
                    params={"projectKeys": ",".join(batch), "metricKeys": metric},
                )
                or {}
            )
            for m in m_data.get("measures") or []:
                comp = m.get("component")
                if comp:
                    measures_by_key[comp] = m

        # Step 3 — build rows + sort.
        rows: list[WorstMetricRow] = []
        for key in project_keys:
            m = measures_by_key.get(key) or {}
            value = m.get("value")
            rows.append(
                {
                    "project_key": key,
                    "project_name": key_to_name.get(key),
                    "value": value,
                    "numeric_value": _parse_float(value),
                }
            )

        higher_is_worse = metric in METRICS_HIGHER_IS_WORSE
        # Projects missing the metric sink to the bottom regardless of direction.

        def sort_key(row: WorstMetricRow) -> tuple[int, float]:
            nv = row["numeric_value"]
            if nv is None:
                return (1, 0.0)
            return (0, -nv if higher_is_worse else nv)

        rows.sort(key=sort_key)
        ranked = rows[:limit]

        result: WorstMetricsOutput = {
            "metric": metric,
            "direction": _direction(metric),
            "limit": limit,
            "candidates_scanned": len(candidates),
            "ranked_count": len(ranked),
            "query": query,
            "ranked": ranked,
        }
        md = (
            f"## Worst by **{metric}** ({result['direction']}) — top {len(ranked)} of "
            f"{len(candidates)} candidates\n\n"
            + "\n".join(
                [
                    f"{idx + 1}. **{r['project_key']}** = {r['value']}"
                    + (f" — {r['project_name']}" if r["project_name"] else "")
                    for idx, r in enumerate(ranked)
                ]
            )
        )
        return output.ok(result, md)  # type: ignore[return-value]
    except Exception as exc:
        output.fail(exc, f"ranking projects by {metric}")


def _direction(metric: str) -> str:
    """Human-readable description of how ``metric`` is sorted."""
    return "higher is worse" if metric in METRICS_HIGHER_IS_WORSE else "lower is worse"
