"""Unit tests for :mod:`sonarqube_mcp.errors`.

Verifies that every HTTP status we special-case produces an actionable
message that names the relevant env vars where appropriate and hints at
a concrete next step. Network failures are simulated via :mod:`responses`.
"""

from __future__ import annotations

import pytest
import requests
import responses

from sonarqube_mcp.errors import ConfigError, handle


def _http_error(
    status: int,
    url: str = "https://sonar.example.com/api/components/search",
    body: str | None = None,
) -> requests.HTTPError:
    """Trigger a real ``requests.HTTPError`` carrying a response with ``status``."""
    with responses.RequestsMock() as rsps:
        if body is None:
            rsps.add(responses.GET, url, json={}, status=status)
        else:
            rsps.add(responses.GET, url, body=body, status=status)
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
        except requests.HTTPError as e:
            return e
    raise AssertionError(f"expected HTTPError for status {status}")  # pragma: no cover


class TestConfigError:
    def test_message_mentions_env_vars(self) -> None:
        msg = handle(ConfigError("SONARQUBE_URL is not set"), "listing projects")
        assert "configuration problem" in msg
        assert "listing projects" in msg
        assert "SONARQUBE_URL" in msg
        assert "SONARQUBE_TOKEN" in msg


class TestHttpStatusMapping:
    def test_401_mentions_token_regeneration(self) -> None:
        msg = handle(_http_error(401), "listing projects")
        assert "401" in msg
        assert "SONARQUBE_TOKEN" in msg
        assert "Tokens" in msg or "token" in msg

    def test_403_mentions_permission(self) -> None:
        msg = handle(_http_error(403), "fetching Quality Gate for einvy")
        assert "403" in msg
        assert "permission" in msg or "Browse" in msg
        assert "fetching Quality Gate for einvy" in msg

    def test_404_suggests_discovery(self) -> None:
        msg = handle(_http_error(404), "fetching metrics")
        assert "404" in msg
        assert "sonarqube_list_projects" in msg

    def test_400_includes_body_snippet(self) -> None:
        err = _http_error(400, body="Invalid metric key 'xyz'")
        msg = handle(err, "fetching metrics")
        assert "400" in msg
        assert "Invalid metric key" in msg

    def test_429_suggests_backoff(self) -> None:
        msg = handle(_http_error(429), "listing issues")
        assert "429" in msg
        assert "Wait" in msg or "rate" in msg

    @pytest.mark.parametrize("code", [500, 502, 503, 504])
    def test_5xx_flags_transient(self, code: int) -> None:
        msg = handle(_http_error(code), "scan")
        assert str(code) in msg
        assert "transient" in msg or "api/system/status" in msg

    def test_unknown_4xx_includes_body_snippet(self) -> None:
        with responses.RequestsMock() as rsps:
            rsps.add(
                responses.GET,
                "https://sonar.example.com/api/x",
                body="boom" * 100,
                status=418,
            )
            try:
                r = requests.get("https://sonar.example.com/api/x", timeout=5)
                r.raise_for_status()
            except requests.HTTPError as e:
                msg = handle(e, "teapot call")
                assert "418" in msg
                assert "boom" in msg


class TestNetworkErrors:
    def test_connection_error_mentions_url_and_proxy(self) -> None:
        msg = handle(requests.ConnectionError("DNS fail"), "listing")
        assert "connect" in msg.lower()
        assert "SONARQUBE_URL" in msg
        assert "proxy" in msg

    def test_timeout_mentions_page_size(self) -> None:
        msg = handle(requests.Timeout("slow"), "listing issues")
        assert "timed out" in msg
        assert "page_size" in msg

    def test_unexpected_exception_fallthrough(self) -> None:
        msg = handle(RuntimeError("kaboom"), "something")
        assert "RuntimeError" in msg
        assert "kaboom" in msg
        assert "something" in msg

    def test_value_error_surfaces_cleanly(self) -> None:
        """Plain ValueError gets its own branch — no 'unexpected ValueError' leak."""
        msg = handle(ValueError("Unknown severity: ['XYZ']"), "fetching issues for einvy")
        assert msg.startswith("Error: invalid input while fetching issues for einvy")
        assert "Unknown severity" in msg
        assert "unexpected" not in msg
