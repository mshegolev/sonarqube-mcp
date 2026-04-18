# Evaluation suite

This directory ships a 10-question evaluation (`evaluation.xml`) built per the
[mcp-builder Phase 4 specification](https://claude.ai/ — reference/evaluation.md).
The suite measures whether an LLM can productively use sonarqube-mcp to answer
realistic, read-only SonarQube questions that require multiple tool calls and
cross-tool reasoning.

## Design principles

Every question in `evaluation.xml` is:

- **Read-only** — no POST / PUT / DELETE on SonarQube.
- **Independent** — solving one question never depends on another.
- **Stable** — based on closed-bucket historical state (past analyses), not
  on "current state" like live issue count that drifts daily.
- **Verifiable** — single-value answer (project key, rule key, digit 1-5,
  status enum, integer string) that can be direct-string-compared.
- **Complex** — each one forces at least 2-3 tool calls: a list/search +
  per-project drill-down + sometimes cross-project ranking.
- **Instance-agnostic** — the questions don't bake in project keys from a
  specific instance; they describe structural queries ("project with the
  worst sqale_rating matching query X").

## Filling in answers

The shipped XML has every `<answer>__VERIFY_ON_INSTANCE__</answer>` as a
placeholder. This is intentional — the suite is a **template**, not a pre-solved
benchmark, because different SonarQube instances will have different project
keys and metric values.

To turn it into a runnable evaluation:

1. Pick a target SonarQube (your team's self-hosted, SonarCloud with a public
   organisation, or the SonarQube demo at https://next.sonarqube.com/sonarqube).
2. Export env vars:
   ```bash
   export SONARQUBE_URL=https://sonar.example.com
   export SONARQUBE_TOKEN=squ_your_token
   ```
3. Solve each question manually — the fastest path is to run Claude Code
   with this MCP server configured and simply ask it the question verbatim.
4. Replace the placeholder answer with the verified value.
5. (Recommended) narrow each question so it unambiguously targets one specific
   object — e.g. replace "first project returned by default listing" with
   "project with key `my-org:my-service`". That makes answers stable against
   SonarQube ordering changes.

## Running the harness

The mcp-builder skill ships a `scripts/evaluation.py` harness that launches
the MCP server via stdio, feeds each question to Claude, compares the result
to the expected answer, and emits a Markdown report.

```bash
# from the mcp-builder skill bundle
python scripts/evaluation.py \
  -t stdio \
  -c uvx \
  -a --from sonarqube-mcp sonarqube-mcp \
  -e SONARQUBE_URL=$SONARQUBE_URL \
  -e SONARQUBE_TOKEN=$SONARQUBE_TOKEN \
  -o evaluation_report.md \
  evaluation.xml
```

Review `evaluation_report.md` to see which questions passed, how many tool
calls Claude made, and the agent's per-question feedback. Low accuracy on a
specific question usually means either:

- The tool description is ambiguous → tighten it in `tools.py`.
- The output schema returns too much or too little → adjust the TypedDict
  model in `models.py`.
- The question itself is ambiguous on that instance → rephrase it.

## Design deviations from skill template

The mcp-builder skill guide describes eval harnesses for MCPs with
**self-contained test data** (Slack workspaces, GitHub repos under your
control). SonarQube MCP is different: it's a read-only wrapper over a
customer-owned external service, so there's no shared fixture to pin answers
against. Shipping a template + verify-on-instance workflow is the honest
compromise — the question *structure* is fixed (validates the MCP design),
and the *values* come from whichever instance you verify against.
