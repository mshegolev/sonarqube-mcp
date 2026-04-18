"""HTTP client for the SonarQube Web API.

Thin wrapper around :mod:`requests` — reads config from env vars, adds
Bearer-token auth, handles SSL-verify toggling, and exposes get/post.
Errors bubble up as :class:`requests.HTTPError` and are mapped to
user-facing messages by :mod:`sonarqube_mcp.errors`.

**Threading model.** The client uses ``requests`` (synchronous). FastMCP
runs synchronous ``@mcp.tool`` in a worker thread via
``anyio.to_thread.run_sync``, so blocking HTTP calls don't block the
asyncio event loop.
"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import requests
import urllib3

from sonarqube_mcp.errors import ConfigError


def _parse_bool(value: str | bool | None, *, default: bool) -> bool:
    """Parse an env-var boolean.

    Accepts true/false/1/0/yes/no/on/off (case-insensitive). Returns
    ``default`` when ``value`` is ``None`` or empty.
    """
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() not in ("false", "0", "no", "off")


def _validate_url(url: str) -> str:
    """Validate that ``url`` is a well-formed HTTP/HTTPS URL.

    Returns the URL with leading/trailing whitespace and any trailing slash
    stripped. Raises :class:`ConfigError` if the URL is missing scheme/host
    or uses an unsupported scheme.
    """
    if not url:
        raise ConfigError("SONARQUBE_URL is not set — configure the env var")

    cleaned = url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ConfigError(f"SONARQUBE_URL must start with http:// or https:// (got: {url!r})")
    if not parsed.netloc:
        raise ConfigError(f"SONARQUBE_URL is missing host (got: {url!r})")
    return cleaned.rstrip("/")


class SonarQubeClient:
    """Minimal SonarQube Web API client.

    The client reads ``SONARQUBE_URL``, ``SONARQUBE_TOKEN``,
    ``SONARQUBE_SSL_VERIFY`` from the environment. Instances are safe to
    reuse — a single :class:`requests.Session` is kept for connection
    pooling.

    Args:
        url: Override ``SONARQUBE_URL``. If ``None``, read from env.
        token: Override ``SONARQUBE_TOKEN``. If ``None``, read from env.
        ssl_verify: Override ``SONARQUBE_SSL_VERIFY``. If ``None``, read
            from env (accepts ``true``/``false``/``1``/``0``/``yes``/``no``,
            default ``True``).

    Raises:
        ConfigError: If required env vars are missing or URL malformed.
    """

    def __init__(
        self,
        url: str | None = None,
        token: str | None = None,
        ssl_verify: bool | None = None,
    ) -> None:
        raw_url = url if url is not None else os.environ.get("SONARQUBE_URL", "")
        self.url = _validate_url(raw_url)
        self.api_url = f"{self.url}/api"

        self.token = token if token is not None else os.environ.get("SONARQUBE_TOKEN", "")
        if not self.token:
            raise ConfigError("SONARQUBE_TOKEN is not set — configure the env var")

        if ssl_verify is None:
            ssl_verify = _parse_bool(os.environ.get("SONARQUBE_SSL_VERIFY"), default=True)
        self.ssl_verify = ssl_verify

        self.session = requests.Session()
        self.session.verify = self.ssl_verify
        self.session.headers.update(
            {
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "User-Agent": "sonarqube-mcp",
            }
        )
        # The MCP server is opinionated about proxies: SonarQube is often a
        # corp service only reachable directly. Disable env-based proxy
        # discovery so the session doesn't hit 127.0.0.1:NNNN unexpectedly.
        self.session.trust_env = False

        if not self.ssl_verify:
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    def _request(
        self,
        method: str,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> requests.Response:
        response = self.session.request(
            method=method,
            url=f"{self.api_url}{endpoint}",
            params=params,
            timeout=30,
        )
        response.raise_for_status()
        return response

    # ── Public API ──────────────────────────────────────────────────────────

    def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any:
        """GET ``{api_url}{endpoint}`` and return parsed JSON (usually a dict).

        SonarQube always returns JSON for 2xx responses; returns ``None`` for
        empty bodies (rare but happens on HEAD-style probes).
        """
        response = self._request("GET", endpoint, params=params)
        if not response.content:
            return None
        return response.json()

    def get_all_pages(
        self,
        endpoint: str,
        *,
        items_key: str,
        page_size: int = 100,
        extra_params: dict[str, Any] | None = None,
        max_pages: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch every page of a SonarQube list endpoint.

        SonarQube paginates with ``p`` + ``ps`` query parameters and reports
        total row count in ``paging.total`` (some newer endpoints use
        ``paging.pageIndex``/``pageSize``, covered here).

        Args:
            endpoint: API path under ``/api`` (leading slash required).
            items_key: Key under which the list of items lives in the
                response body (e.g. ``components``, ``projects``, ``issues``).
            page_size: Items per page (SonarQube caps at 500 for most
                endpoints but 100 is safe everywhere).
            extra_params: Additional query params merged into every request.
            max_pages: Hard cap to prevent runaway loops on misbehaving
                servers.

        Returns:
            A single flat ``list`` with every row across all pages. Returns
            ``[]`` when the endpoint has no data.
        """
        results: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            params: dict[str, Any] = {"p": page, "ps": page_size}
            if extra_params:
                params.update(extra_params)
            data = self.get(endpoint, params=params) or {}
            chunk = data.get(items_key) or []
            if not chunk:
                break
            results.extend(chunk)
            paging = data.get("paging") or {}
            total = int(paging.get("total") or 0)
            if total and page * page_size >= total:
                break
            if len(chunk) < page_size:
                break
            page += 1
        return results

    def close(self) -> None:
        """Close the underlying HTTP session (called from lifespan on shutdown)."""
        self.session.close()


# ── Shared constants ────────────────────────────────────────────────────────

# Default metric keys pulled by ``sonarqube_project_metrics`` when the
# caller does not supply a specific list. Covers the headline values an
# agent wants on first glance (bugs, code smells, coverage, duplications,
# ratings, sizing).
DEFAULT_METRIC_KEYS: tuple[str, ...] = (
    "alert_status",
    "bugs",
    "code_smells",
    "coverage",
    "duplicated_lines_density",
    "ncloc",
    "reliability_rating",
    "security_rating",
    "security_review_rating",
    "sqale_rating",
    "vulnerabilities",
    "tests",
)

# "Worse is higher" metrics — for these, ``sonarqube_worst_metrics`` sorts
# descending (more bugs = worse). The remaining metric keys behave the
# opposite way: lower coverage or higher *letter* rating is worse. Ratings
# come back as numeric strings "1"..."5" where 1=A, 5=E.
METRICS_HIGHER_IS_WORSE: frozenset[str] = frozenset(
    {
        "bugs",
        "code_smells",
        "vulnerabilities",
        "duplicated_lines_density",
        "reliability_rating",
        "security_rating",
        "security_review_rating",
        "sqale_rating",
        "open_issues",
    }
)

# Valid SonarQube issue severities (sorted from noisiest to quietest).
VALID_SEVERITIES: tuple[str, ...] = ("BLOCKER", "CRITICAL", "MAJOR", "MINOR", "INFO")

# Valid SonarQube issue types accepted by ``/api/issues/search``. Note that
# SonarQube 9.x moved Security Hotspots out of the issues API into
# ``/api/hotspots/search``, so ``SECURITY_HOTSPOT`` is intentionally **not**
# included here — passing it to the issues endpoint silently returns nothing.
VALID_ISSUE_TYPES: tuple[str, ...] = ("BUG", "VULNERABILITY", "CODE_SMELL")
