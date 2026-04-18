"""Shared FastMCP instance and client cache."""

from __future__ import annotations

import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP

from sonarqube_mcp.client import SonarQubeClient

logger = logging.getLogger(__name__)

_client: SonarQubeClient | None = None
_client_lock = threading.Lock()


@asynccontextmanager
async def app_lifespan(_app: FastMCP) -> AsyncIterator[dict[str, Any]]:
    """Server lifespan: close HTTP session on shutdown."""
    logger.debug("sonarqube_mcp: startup")
    try:
        yield {}
    finally:
        global _client
        with _client_lock:
            if _client is not None:
                try:
                    _client.close()
                except Exception:
                    pass
                _client = None
        logger.debug("sonarqube_mcp: shutdown — HTTP session closed")


mcp = FastMCP("sonarqube_mcp", lifespan=app_lifespan)


def get_client() -> SonarQubeClient:
    """Return a cached :class:`SonarQubeClient` (thread-safe lazy-init).

    FastMCP runs synchronous tools in worker threads via
    ``anyio.to_thread.run_sync``; concurrent first-calls could otherwise
    race on the ``_client`` global. The lock ensures exactly one instance
    is constructed.
    """
    global _client
    if _client is None:
        with _client_lock:
            if _client is None:  # double-checked locking
                _client = SonarQubeClient()
    return _client
