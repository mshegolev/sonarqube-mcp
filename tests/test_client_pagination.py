"""Tests for :meth:`SonarQubeClient.get_all_pages`.

SonarQube list endpoints paginate via ``p`` + ``ps`` and report
``paging.total``. The helper must walk pages until either (a) total is
reached, (b) a short page arrives, or (c) the API returns an empty page.
"""

from __future__ import annotations

import pytest
import responses

from sonarqube_mcp.client import SonarQubeClient


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> SonarQubeClient:
    monkeypatch.setenv("SONARQUBE_URL", "https://sonar.example.com")
    monkeypatch.setenv("SONARQUBE_TOKEN", "t")  # pragma: allowlist secret
    c = SonarQubeClient()
    yield c
    c.close()


@responses.activate
def test_walks_until_total_reached(client: SonarQubeClient) -> None:
    base = "https://sonar.example.com/api/components/search"
    responses.add(
        responses.GET,
        base,
        json={"components": [{"key": f"k{i}"} for i in range(3)], "paging": {"total": 8}},
        status=200,
    )
    responses.add(
        responses.GET,
        base,
        json={"components": [{"key": f"k{i}"} for i in range(3, 6)], "paging": {"total": 8}},
        status=200,
    )
    responses.add(
        responses.GET,
        base,
        json={"components": [{"key": f"k{i}"} for i in range(6, 8)], "paging": {"total": 8}},
        status=200,
    )

    out = client.get_all_pages("/components/search", items_key="components", page_size=3)
    assert [r["key"] for r in out] == [f"k{i}" for i in range(8)]

    calls = [call.request.url for call in responses.calls]
    assert any("p=1" in u and "ps=3" in u for u in calls)
    assert any("p=2" in u for u in calls)
    assert any("p=3" in u for u in calls)


@responses.activate
def test_stops_on_short_page_without_total(client: SonarQubeClient) -> None:
    base = "https://sonar.example.com/api/issues/search"
    # No ``paging.total`` — the helper must still stop once it sees a page
    # smaller than ``page_size``.
    responses.add(
        responses.GET,
        base,
        json={"issues": [{"key": "i1"}, {"key": "i2"}]},
        status=200,
    )
    responses.add(
        responses.GET,
        base,
        json={"issues": [{"key": "i3"}]},  # short page → stop
        status=200,
    )

    out = client.get_all_pages("/issues/search", items_key="issues", page_size=2)
    assert [r["key"] for r in out] == ["i1", "i2", "i3"]
    assert len(responses.calls) == 2


@responses.activate
def test_stops_on_empty_first_page(client: SonarQubeClient) -> None:
    responses.add(
        responses.GET,
        "https://sonar.example.com/api/components/search",
        json={"components": [], "paging": {"total": 0}},
        status=200,
    )
    out = client.get_all_pages("/components/search", items_key="components", page_size=50)
    assert out == []
    assert len(responses.calls) == 1


@responses.activate
def test_passes_extra_params(client: SonarQubeClient) -> None:
    base = "https://sonar.example.com/api/components/search"
    responses.add(
        responses.GET,
        base,
        json={"components": [{"key": "einvy"}], "paging": {"total": 1}},
        status=200,
    )

    out = client.get_all_pages(
        "/components/search",
        items_key="components",
        page_size=100,
        extra_params={"qualifiers": "TRK", "q": "einvy"},
    )
    assert out == [{"key": "einvy"}]
    url = responses.calls[0].request.url
    assert "qualifiers=TRK" in url
    assert "q=einvy" in url


@responses.activate
def test_max_pages_cap(client: SonarQubeClient) -> None:
    base = "https://sonar.example.com/api/components/search"
    # Huge declared total — the cap must still stop the loop.
    for _ in range(5):
        responses.add(
            responses.GET,
            base,
            json={"components": [{"key": "a"}, {"key": "b"}], "paging": {"total": 10_000}},
            status=200,
        )

    out = client.get_all_pages(
        "/components/search",
        items_key="components",
        page_size=2,
        max_pages=3,
    )
    assert len(out) == 6
    assert len(responses.calls) == 3


@responses.activate
def test_empty_body_returns_empty_list(client: SonarQubeClient) -> None:
    responses.add(
        responses.GET,
        "https://sonar.example.com/api/issues/search",
        json={},
        status=200,
    )
    out = client.get_all_pages("/issues/search", items_key="issues", page_size=50)
    assert out == []
