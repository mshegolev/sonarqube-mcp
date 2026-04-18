"""Tests for the module-level client cache in :mod:`sonarqube_mcp._mcp`.

``get_client`` lazily instantiates a single :class:`SonarQubeClient`
protected by a lock (double-checked locking). This test covers the happy
path — repeated calls return the *same* instance, and wiping the global
cache rebuilds the client.
"""

from __future__ import annotations

import pytest

from sonarqube_mcp import _mcp
from sonarqube_mcp.client import SonarQubeClient


@pytest.fixture(autouse=True)
def reset_cache() -> None:
    """Clear the module-global client between tests to avoid test-order coupling."""
    with _mcp._client_lock:
        if _mcp._client is not None:
            try:
                _mcp._client.close()
            except Exception:
                pass
        _mcp._client = None


def test_get_client_returns_same_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SONARQUBE_URL", "https://sonar.example.com")
    monkeypatch.setenv("SONARQUBE_TOKEN", "t")  # pragma: allowlist secret
    first = _mcp.get_client()
    second = _mcp.get_client()
    assert first is second
    assert isinstance(first, SonarQubeClient)


def test_get_client_raises_on_missing_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SONARQUBE_URL", raising=False)
    monkeypatch.delenv("SONARQUBE_TOKEN", raising=False)
    with pytest.raises(Exception, match="SONARQUBE_URL"):
        _mcp.get_client()


def test_cache_rebuilds_after_reset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SONARQUBE_URL", "https://sonar.example.com")
    monkeypatch.setenv("SONARQUBE_TOKEN", "t")  # pragma: allowlist secret
    first = _mcp.get_client()
    with _mcp._client_lock:
        _mcp._client = None
    second = _mcp.get_client()
    assert first is not second
