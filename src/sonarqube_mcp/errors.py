"""Actionable error messages for SonarQube HTTP errors."""

from __future__ import annotations

import requests


class ConfigError(ValueError):
    """Raised when required environment variables are missing or malformed.

    Subclass of :class:`ValueError` so callers can continue to use
    ``isinstance(..., ValueError)``, but narrow enough that :func:`handle`
    can distinguish config errors from Pydantic validation errors bubbling
    up from tool input.
    """


def handle(exc: Exception, action: str) -> str:
    """Convert an exception raised while performing ``action`` into an
    LLM-readable string with a suggested next step.

    The goal is that the agent sees *why* the call failed and *what it could
    do about it* without needing to inspect a Python traceback.
    """
    if isinstance(exc, ConfigError):
        return (
            f"Error: configuration problem while {action} — {exc}. "
            "Check SONARQUBE_URL, SONARQUBE_TOKEN, SONARQUBE_SSL_VERIFY environment variables."
        )

    if isinstance(exc, requests.HTTPError):
        code = exc.response.status_code if exc.response is not None else None
        if code == 401:
            return (
                f"Error: authentication failed (HTTP 401) while {action}. "
                "Verify SONARQUBE_TOKEN is set and not expired (generate a new one at "
                "My Account → Security → Tokens in the SonarQube UI)."
            )
        if code == 403:
            return (
                f"Error: forbidden (HTTP 403) while {action}. "
                "The SonarQube token lacks permission on the target resource. "
                "Use a token from a user with 'Browse' permission on the project, "
                "or an admin token for organisation-wide queries."
            )
        if code == 404:
            return (
                f"Error: resource not found (HTTP 404) while {action}. "
                "Check that the project key / component exists. "
                "Use sonarqube_list_projects with a q= filter to find valid keys."
            )
        if code == 400:
            body = ""
            if exc.response is not None:
                try:
                    body = exc.response.text[:300]
                except Exception:
                    pass
            return (
                f"Error: bad request (HTTP 400) while {action}. "
                "SonarQube rejected the parameters — most often an invalid metric key, "
                f"severity, or project qualifier. Response: {body}"
            )
        if code == 429:
            return (
                f"Error: rate-limited (HTTP 429) while {action}. "
                "Wait 30-60s before retrying; reduce page_size or fetch fewer projects at once."
            )
        if code is not None and 500 <= code < 600:
            return (
                f"Error: SonarQube server error (HTTP {code}) while {action}. "
                "This is usually transient — retry in a few seconds; check SonarQube /api/system/status."
            )
        body = ""
        if exc.response is not None:
            try:
                body = exc.response.text[:200]
            except Exception:
                pass
        return f"Error: HTTP {code} while {action}. Response: {body}"

    if isinstance(exc, requests.ConnectionError):
        return (
            f"Error: could not connect to SonarQube while {action}. "
            "Check SONARQUBE_URL, network access, proxy settings."
        )

    if isinstance(exc, requests.Timeout):
        return (
            f"Error: request timed out while {action}. "
            "Check network latency and retry; reduce page_size if fetching many issues."
        )

    if isinstance(exc, ValueError):
        # `_validate_list_against` and similar helpers raise ValueError with a
        # human-readable message. Surface it cleanly rather than through the
        # generic "unexpected ValueError" fallthrough below.
        return f"Error: invalid input while {action} — {exc}"

    return f"Error: unexpected {type(exc).__name__} while {action}: {exc}"
