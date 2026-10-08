"""Shared fixtures. No test reaches the network or the real data folder."""

from __future__ import annotations

import pytest
import requests

from tradecheck import http


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Fail any test that would open a real connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("a test tried to reach the network")
    monkeypatch.setattr(requests.Session, "request", refuse)


@pytest.fixture(autouse=True)
def fake_keys(monkeypatch):
    """Test keys, so code that needs a key runs. They never leave the process."""
    for name in ("OPENSANCTIONS_API_KEY", "OPENROUTER_API_KEY", "TAVILY_API_KEY"):
        monkeypatch.setenv(name, "test-key")


@pytest.fixture
def fake_http(monkeypatch):
    """Replace http.request with a router. ``routes`` maps a URL fragment to a
    FakeResponse, an exception to raise, or a function of the request's keyword
    arguments that returns either. ``calls`` records (method, url, kwargs)."""
    routes: dict = {}
    calls: list = []

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        for fragment, answer in routes.items():
            if fragment in url:
                if callable(answer):
                    answer = answer(kwargs)
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        raise AssertionError(f"no fake route for {method} {url}")

    monkeypatch.setattr(http, "request", fake_request)
    return routes, calls
