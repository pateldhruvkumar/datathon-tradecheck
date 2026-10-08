"""Retries, backoff and the OpenRouter call. requests is faked; nothing leaves the process."""

import pytest
import requests

from tests.fakes import FakeResponse, chat_reply
from tradecheck import http


@pytest.fixture
def transport(monkeypatch):
    """requests.request answers from a list (the last answer repeats); sleeps are recorded, not slept."""
    state = {"answers": [], "n": 0, "slept": []}

    def fake(method, url, **kwargs):
        state["n"] += 1
        answer = state["answers"][min(state["n"], len(state["answers"])) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer

    monkeypatch.setattr(http.requests, "request", fake)
    monkeypatch.setattr(http.time, "sleep", state["slept"].append)
    return state


def test_503_is_retried_three_times_with_backoff(transport):
    transport["answers"] = [FakeResponse(503)]
    assert http.request("GET", "https://example.invalid").status_code == 503
    assert transport["n"] == 4
    assert transport["slept"] == [1, 2, 4]


def test_429_then_success(transport):
    transport["answers"] = [FakeResponse(429), FakeResponse(200, {})]
    assert http.request("GET", "https://example.invalid").status_code == 200
    assert transport["n"] == 2


def test_401_is_not_retried(transport):
    transport["answers"] = [FakeResponse(401)]
    assert http.request("GET", "https://example.invalid").status_code == 401
    assert transport["n"] == 1


def test_connection_error_is_retried_then_raised(transport):
    transport["answers"] = [requests.exceptions.ConnectionError("connection reset")]
    with pytest.raises(requests.exceptions.ConnectionError):
        http.request("GET", "https://example.invalid")
    assert transport["n"] == 4


@pytest.mark.parametrize("error", [
    requests.exceptions.ReadTimeout("server is slow"),  # a retry would double the wait
    requests.exceptions.SSLError("certificate verify failed"),
    requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden"),
])
def test_slow_or_deterministic_failures_are_not_retried(transport, error):
    transport["answers"] = [error]
    with pytest.raises(type(error)):
        http.request("GET", "https://example.invalid")
    assert transport["n"] == 1


def test_chat_sends_the_pinned_settings(fake_http):
    routes, calls = fake_http
    routes["openrouter.ai"] = chat_reply({"ok": True})
    assert http.chat([{"role": "user", "content": "hi"}], "probe", {"type": "object"}) == '{"ok": true}'
    _, url, kwargs = calls[0]
    body = kwargs["json"]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert body["model"] == "qwen/qwen3.8-27b"
    assert (body["temperature"], body["seed"]) == (0, 42)
    assert body["provider"] == {"require_parameters": True}
    assert body["reasoning"] == {"effort": "low"}
    assert body["response_format"] == {"type": "json_schema", "json_schema": {
        "name": "probe", "strict": True, "schema": {"type": "object"}}}
    assert kwargs["headers"] == {"Authorization": "Bearer test-key"}
    assert kwargs["timeout"] == 40


@pytest.mark.parametrize("answer, message", [
    (FakeResponse(404, {"error": {"message": "No endpoints found"}}), "HTTP 404: No endpoints found"),
    (FakeResponse(200, {"error": {"message": "upstream failed"}}), "reply had no choices: upstream failed"),
    (chat_reply(""), "reply had no text"),
    (requests.exceptions.ReadTimeout("slow"), "no connection: ReadTimeout"),
])
def test_chat_failures_raise_model_error(fake_http, answer, message):
    routes, _ = fake_http
    routes["openrouter.ai"] = answer
    with pytest.raises(http.ModelError, match=message):
        http.chat([], "probe", {})


def test_chat_without_a_key_makes_no_call(fake_http, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY")
    _, calls = fake_http
    with pytest.raises(http.ModelError, match="OPENROUTER_API_KEY is not set"):
        http.chat([], "probe", {})
    assert calls == []
