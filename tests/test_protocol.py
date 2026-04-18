"""Wire-protocol smoke-test (substitute for MCP Inspector).

FastMCP exposes ``mcp.list_tools()`` as the in-process equivalent of the
``tools/list`` MCP request. Running it confirms that:

- The shared ``FastMCP`` instance actually has tools registered.
- Each tool carries the expected ``annotations`` (readOnlyHint, etc.).
- The ``outputSchema`` is generated from the TypedDict return annotation
  (FastMCP introspection — Pydantic-safe patterns in ``models.py`` are
  exercised here).
- The ``inputSchema`` contains the right param names, constraints, and
  required markers — what an MCP client would use to build tool-call
  arguments.

This is the closest we can get to ``npx @modelcontextprotocol/inspector``
without a UI.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

# Importing tools attaches @mcp.tool decorators.
import sonarqube_mcp.tools  # noqa: F401
from sonarqube_mcp._mcp import mcp

EXPECTED_TOOLS: dict[str, dict[str, Any]] = {
    "sonarqube_list_projects": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": set(),
        "optional_params": {"query", "page", "page_size"},
    },
    "sonarqube_project_metrics": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"project_key"},
        "optional_params": {"metric_keys", "branch", "pull_request"},
    },
    "sonarqube_quality_gate_status": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"project_key"},
        "optional_params": {"branch", "pull_request"},
    },
    "sonarqube_get_issues": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"project_key"},
        "optional_params": {"severities", "types", "resolved", "branch", "pull_request", "page", "page_size"},
    },
    "sonarqube_worst_metrics": {
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "required_params": {"metric"},
        "optional_params": {"limit", "query", "candidate_pool"},
    },
}


@pytest.fixture(scope="module")
def listed_tools() -> list[Any]:
    """One-shot handshake equivalent: fetch the tool catalogue FastMCP exposes."""
    return asyncio.run(mcp.list_tools())


def test_all_five_tools_registered(listed_tools: list[Any]) -> None:
    names = {t.name for t in listed_tools}
    assert names == set(EXPECTED_TOOLS), (
        f"tool list mismatch.\n  registered: {sorted(names)}\n  expected:   {sorted(EXPECTED_TOOLS)}"
    )


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_tool_annotations(listed_tools: list[Any], tool_name: str) -> None:
    """Every tool must carry readOnly/destructive/idempotent hints matching our design."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    ann = tool.annotations
    expected = EXPECTED_TOOLS[tool_name]
    assert ann.readOnlyHint is expected["readOnlyHint"], f"{tool_name}.readOnlyHint"
    assert ann.destructiveHint is expected["destructiveHint"], f"{tool_name}.destructiveHint"
    assert ann.idempotentHint is expected["idempotentHint"], f"{tool_name}.idempotentHint"


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_input_schema_shape(listed_tools: list[Any], tool_name: str) -> None:
    """Required + optional parameter sets must match the tool signatures."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    schema = tool.inputSchema
    assert schema["type"] == "object"
    properties = set(schema.get("properties", {}).keys())
    required = set(schema.get("required", []))

    expected = EXPECTED_TOOLS[tool_name]
    assert required == expected["required_params"], (
        f"{tool_name}.required: got {required}, expected {expected['required_params']}"
    )
    # All expected props (required + optional) must appear.
    expected_all = expected["required_params"] | expected["optional_params"]
    assert expected_all.issubset(properties), f"{tool_name}: missing properties {expected_all - properties}"


@pytest.mark.parametrize("tool_name", list(EXPECTED_TOOLS))
def test_output_schema_is_generated(listed_tools: list[Any], tool_name: str) -> None:
    """structured_output=True must produce an outputSchema for every tool."""
    tool = next(t for t in listed_tools if t.name == tool_name)
    assert tool.outputSchema is not None, f"{tool_name} has no outputSchema"
    assert tool.outputSchema.get("type") == "object", f"{tool_name} outputSchema not an object"
    # Non-empty properties — our TypedDict models declare multiple fields each.
    assert tool.outputSchema.get("properties"), f"{tool_name} outputSchema has no properties"


def test_branch_pr_mutual_exclusion_fields_present(listed_tools: list[Any]) -> None:
    """Spot-check: branch + pull_request both exposed on the three tools that accept them."""
    for tool_name in ("sonarqube_project_metrics", "sonarqube_quality_gate_status", "sonarqube_get_issues"):
        tool = next(t for t in listed_tools if t.name == tool_name)
        props = tool.inputSchema["properties"]
        assert "branch" in props, f"{tool_name} missing branch param"
        assert "pull_request" in props, f"{tool_name} missing pull_request param"


def test_severity_and_type_enums_documented(listed_tools: list[Any]) -> None:
    """Descriptions on severities/types must list valid values so the LLM
    knows the whitelist without trial-and-error."""
    tool = next(t for t in listed_tools if t.name == "sonarqube_get_issues")
    sev_desc = tool.inputSchema["properties"]["severities"].get("description", "")
    type_desc = tool.inputSchema["properties"]["types"].get("description", "")
    for v in ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO"):
        assert v in sev_desc, f"severity {v} not documented"
    for v in ("BUG", "VULNERABILITY", "CODE_SMELL"):
        assert v in type_desc, f"type {v} not documented"
    # SECURITY_HOTSPOT must NOT appear as a valid option (intentionally excluded).
    assert "SECURITY_HOTSPOT" not in type_desc or "not supported" in type_desc.lower()
