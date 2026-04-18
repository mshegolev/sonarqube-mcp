"""Unit tests for pure helpers in :mod:`sonarqube_mcp.client`.

These tests avoid the network entirely — they cover env-var parsing, URL
validation, :class:`SonarQubeClient` construction (which raises
:class:`ConfigError` when required env vars are missing), and the
module-level constants.
"""

from __future__ import annotations

import pytest

from sonarqube_mcp.client import (
    DEFAULT_METRIC_KEYS,
    METRICS_HIGHER_IS_WORSE,
    VALID_ISSUE_TYPES,
    VALID_SEVERITIES,
    SonarQubeClient,
    _parse_bool,
    _validate_url,
)
from sonarqube_mcp.errors import ConfigError


class TestParseBool:
    @pytest.mark.parametrize("value", ["true", "True", "1", "yes", "on", "YES"])
    def test_truthy_strings(self, value: str) -> None:
        assert _parse_bool(value, default=False) is True

    @pytest.mark.parametrize("value", ["false", "False", "0", "no", "off", "OFF"])
    def test_falsy_strings(self, value: str) -> None:
        assert _parse_bool(value, default=True) is False

    @pytest.mark.parametrize("value", [None, ""])
    def test_empty_returns_default(self, value: str | None) -> None:
        assert _parse_bool(value, default=True) is True
        assert _parse_bool(value, default=False) is False

    def test_bool_passthrough(self) -> None:
        assert _parse_bool(True, default=False) is True
        assert _parse_bool(False, default=True) is False


class TestValidateUrl:
    def test_strips_trailing_slash(self) -> None:
        assert _validate_url("https://sonar.example.com/") == "https://sonar.example.com"

    def test_strips_whitespace(self) -> None:
        assert _validate_url("  https://sonar.example.com  ") == "https://sonar.example.com"

    def test_preserves_no_trailing_slash(self) -> None:
        assert _validate_url("https://sonar.example.com") == "https://sonar.example.com"

    def test_http_scheme_allowed(self) -> None:
        assert _validate_url("http://sonar.local") == "http://sonar.local"

    def test_empty_raises(self) -> None:
        with pytest.raises(ConfigError, match="SONARQUBE_URL is not set"):
            _validate_url("")

    def test_missing_scheme_raises(self) -> None:
        with pytest.raises(ConfigError, match="http:// or https://"):
            _validate_url("sonar.example.com")

    def test_wrong_scheme_raises(self) -> None:
        with pytest.raises(ConfigError, match="http:// or https://"):
            _validate_url("ftp://sonar.example.com")

    def test_missing_host_raises(self) -> None:
        with pytest.raises(ConfigError, match="missing host"):
            _validate_url("https://")


class TestConstants:
    def test_default_metrics_nonempty(self) -> None:
        assert len(DEFAULT_METRIC_KEYS) >= 8
        assert "bugs" in DEFAULT_METRIC_KEYS
        assert "coverage" in DEFAULT_METRIC_KEYS
        assert "alert_status" in DEFAULT_METRIC_KEYS

    def test_valid_severities(self) -> None:
        assert VALID_SEVERITIES == ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO")

    def test_valid_issue_types(self) -> None:
        # Security Hotspots live on a separate API endpoint — intentionally excluded
        # to prevent silent empty responses.
        assert set(VALID_ISSUE_TYPES) == {"BUG", "VULNERABILITY", "CODE_SMELL"}
        assert "SECURITY_HOTSPOT" not in VALID_ISSUE_TYPES

    def test_metrics_higher_is_worse_direction(self) -> None:
        assert "bugs" in METRICS_HIGHER_IS_WORSE
        assert "vulnerabilities" in METRICS_HIGHER_IS_WORSE
        assert "sqale_rating" in METRICS_HIGHER_IS_WORSE
        # Coverage is the opposite — lower is worse.
        assert "coverage" not in METRICS_HIGHER_IS_WORSE


class TestSonarQubeClientInit:
    def test_missing_url_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SONARQUBE_URL", raising=False)
        monkeypatch.setenv("SONARQUBE_TOKEN", "t")  # pragma: allowlist secret
        with pytest.raises(ConfigError, match="SONARQUBE_URL"):
            SonarQubeClient()

    def test_missing_token_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SONARQUBE_URL", "https://sonar.example.com")
        monkeypatch.delenv("SONARQUBE_TOKEN", raising=False)
        with pytest.raises(ConfigError, match="SONARQUBE_TOKEN"):
            SonarQubeClient()

    def test_happy_path_builds_api_url_and_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SONARQUBE_URL", "https://sonar.example.com/")
        monkeypatch.setenv("SONARQUBE_TOKEN", "squ_test_token")  # pragma: allowlist secret
        monkeypatch.setenv("SONARQUBE_SSL_VERIFY", "false")
        client = SonarQubeClient()
        try:
            assert client.url == "https://sonar.example.com"
            assert client.api_url == "https://sonar.example.com/api"
            assert client.token == "squ_test_token"  # pragma: allowlist secret
            assert client.ssl_verify is False
            assert client.session.trust_env is False
            assert client.session.headers["Accept"] == "application/json"
            assert client.session.headers["Authorization"].startswith("Bearer ")
        finally:
            client.close()

    def test_overrides_take_precedence_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SONARQUBE_URL", "https://env.example.com")
        monkeypatch.setenv("SONARQUBE_TOKEN", "env-token")  # pragma: allowlist secret
        client = SonarQubeClient(
            url="https://explicit.example.com",
            token="explicit-token",  # pragma: allowlist secret
            ssl_verify=True,
        )
        try:
            assert client.url == "https://explicit.example.com"
            assert client.token == "explicit-token"  # pragma: allowlist secret
            assert client.ssl_verify is True
        finally:
            client.close()

    def test_ssl_verify_default_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SONARQUBE_URL", "https://sonar.example.com")
        monkeypatch.setenv("SONARQUBE_TOKEN", "t")  # pragma: allowlist secret
        monkeypatch.delenv("SONARQUBE_SSL_VERIFY", raising=False)
        client = SonarQubeClient()
        try:
            assert client.ssl_verify is True
        finally:
            client.close()
