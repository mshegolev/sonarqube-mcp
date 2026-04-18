# sonarqube-mcp

<!-- mcp-name: io.github.mshegolev/sonarqube-mcp -->

[![PyPI](https://img.shields.io/pypi/v/sonarqube-mcp.svg?logo=pypi&logoColor=white)](https://pypi.org/project/sonarqube-mcp/)
[![Python](https://img.shields.io/pypi/pyversions/sonarqube-mcp.svg?logo=python&logoColor=white)](https://pypi.org/project/sonarqube-mcp/)
[![License: MIT](https://img.shields.io/pypi/l/sonarqube-mcp.svg)](LICENSE)

MCP server for [SonarQube](https://www.sonarsource.com/products/sonarqube/). Lets an LLM agent (Claude Code, Cursor, OpenCode, etc.) discover projects, pull headline metrics, check Quality Gate status, search issues with severity/type filters, and rank projects by the *worst* value of any metric.

Python, [FastMCP](https://github.com/modelcontextprotocol/python-sdk), stdio transport.

Works with any SonarQube 9.x / 10.x instance (self-hosted) and with SonarCloud.

## Why another SonarQube MCP?

A few community SonarQube MCPs exist, but they tend to stop at single-project reads. This one adds **cross-project ranking** (`sonarqube_worst_metrics`) — the operation a lead actually runs during a triage session: "show me the top 10 worst-coverage services in the org". All tools are read-only and safely parameterised (Pydantic input validation, severity / type whitelists).

## Design highlights

- **Tool annotations** — all five tools carry `readOnlyHint: True`, `destructiveHint: False`, `idempotentHint: True`. Nothing can mutate SonarQube from this server.
- **Structured output** — every tool returns a typed payload (TypedDict) + a markdown summary, so clients with and without structured-content support both get a usable response.
- **Structured errors** — 401 / 403 / 404 / 400 / 429 / 5xx mapped to actionable hints (e.g. "regenerate token", "check project key with sonarqube_list_projects").
- **Pydantic input validation** for every argument; severity / type filters are checked against the valid SonarQube enum before the request is sent.
- **Cross-project worst-metric ranking** — batches `/api/measures/search` calls under the hood, sorts ascending or descending based on whether higher is worse for the chosen metric.

## Features (5 tools)

**Discovery**
- `sonarqube_list_projects` — paginated project search with optional text filter

**Single-project insight**
- `sonarqube_project_metrics` — measures for one project (default set covers bugs / coverage / smells / ratings / ncloc / tests / alert_status)
- `sonarqube_quality_gate_status` — Quality Gate status + per-condition failures

**Issue triage**
- `sonarqube_get_issues` — issue search filtered by severity / type / resolution status

**Cross-project ranking**
- `sonarqube_worst_metrics` — top-N projects sorted by the worst value of a metric (e.g. worst coverage, most bugs)

## Installation

Requires Python 3.10+.

```bash
# via uvx (recommended — no install, just run)
uvx --from sonarqube-mcp sonarqube-mcp

# or via pipx
pipx install sonarqube-mcp
```

## Configuration

```bash
claude mcp add sonarqube -s project \
  --env SONARQUBE_URL=https://sonar.example.com \
  --env SONARQUBE_TOKEN=squ_your_token \
  --env SONARQUBE_SSL_VERIFY=true \
  -- uvx --from sonarqube-mcp sonarqube-mcp
```

Or in `.mcp.json`:

```json
{
  "mcpServers": {
    "sonarqube": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "sonarqube-mcp", "sonarqube-mcp"],
      "env": {
        "SONARQUBE_URL": "https://sonar.example.com",
        "SONARQUBE_TOKEN": "${SONARQUBE_TOKEN}",
        "SONARQUBE_SSL_VERIFY": "true"
      }
    }
  }
}
```

Check:

```bash
claude mcp list
# sonarqube: uvx --from sonarqube-mcp sonarqube-mcp - ✓ Connected
```

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `SONARQUBE_URL` | yes | SonarQube URL (no trailing slash) |
| `SONARQUBE_TOKEN` | yes | Bearer token. Generate in: My Account → Security → Tokens |
| `SONARQUBE_SSL_VERIFY` | no | `true`/`false`. Default: `true`. |

**Note on HTTP proxies.** The client intentionally disables env-based proxy discovery (`trust_env=False`) because self-hosted SonarQube is typically reachable only on an internal network. If you connect to SonarCloud or any SonarQube that lives *behind* a corporate proxy, you'll currently need to drop the proxy variables at the process level — a `SONARQUBE_TRUST_ENV_PROXY` knob is planned for a follow-up release.

## Example usage

- "List all SonarQube projects matching 'einvy'"
- "What's the Quality Gate status for `einvy:aut_einvy`?"
- "Show me the top 10 projects with the most bugs"
- "Find all BLOCKER / CRITICAL vulnerabilities in `einvy:aut_einvy`"
- "What's the coverage on `einvy:qa_assistant`?"
- "Top 5 worst-coverage projects matching query 'einvy'"

## Metric directions (used by `sonarqube_worst_metrics`)

**Higher is worse** (sorted descending — more is worse):
`bugs`, `code_smells`, `vulnerabilities`, `duplicated_lines_density`, `reliability_rating`, `security_rating`, `security_review_rating`, `sqale_rating`, `open_issues`

**Lower is worse** (sorted ascending — less is worse):
`coverage`, `line_coverage`, `branch_coverage`, `test_success_density`, `tests`

Ratings in SonarQube are numeric strings `"1"` (A, best) through `"5"` (E, worst).

## Safety

- All tools are `readOnlyHint: True` — nothing can mutate SonarQube.
- No `POST` / `PUT` / `DELETE` is ever called.
- Severity / type / qualifier inputs are validated against SonarQube enums before the API call, so the tool fails fast on typos rather than hitting the API.

## Performance characteristics

- Every tool makes **one HTTP call** to SonarQube except `sonarqube_worst_metrics`, which makes **one search call + ⌈candidate_pool/100⌉ bulk-measures calls**. Default settings land at ≤ 2 calls.
- Single-tool response time on a healthy SonarQube instance: typically < 500 ms.
- Pagination is passed through to SonarQube (`p` + `ps` params) — no full-result buffering in the MCP server.
- `sonarqube_worst_metrics` caps `candidate_pool` at 500 — on instances with thousands of projects, pre-filter with `query=` before ranking (see the tool docstring).
- SonarQube has no published hard rate limit. If 429 is received the server surfaces an actionable error ("Wait 30-60 s before retrying; reduce page_size").

## Development

```bash
git clone https://github.com/mshegolev/sonarqube-mcp.git
cd sonarqube-mcp
pip install -e '.[dev]'
pytest
```

## License

MIT © Mikhail Shchegolev
