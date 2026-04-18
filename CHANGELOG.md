# Changelog

All notable changes to `sonarqube-mcp` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions use [SemVer](https://semver.org/).

## [0.1.0] — 2026-04-18

### Added
- Initial release with 5 read-only tools covering SonarQube Web API:
  - `sonarqube_list_projects` — search projects with pagination and optional text filter
  - `sonarqube_project_metrics` — fetch measures (bugs, coverage, smells, ratings, etc.) for a project
  - `sonarqube_quality_gate_status` — Quality Gate status + per-condition breakdown
  - `sonarqube_get_issues` — issues search with severity / type / resolution filters
  - `sonarqube_worst_metrics` — rank projects by the worst value of a single metric
- FastMCP + Pydantic input validation + TypedDict output schemas.
- Structured error mapping (401 / 403 / 404 / 429 / 5xx) with actionable hints.
- Tool annotations: `readOnlyHint` / `idempotentHint` / `openWorldHint`.
- `SONARQUBE_SSL_VERIFY` toggle for self-signed certificates.
- MIT license.
- Published on PyPI and in the MCP Registry as `io.github.mshegolev/sonarqube-mcp`.
