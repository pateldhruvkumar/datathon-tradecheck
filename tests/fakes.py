"""Stand-ins for HTTP responses, shared by the tests."""

from __future__ import annotations

import json

import requests


class FakeResponse:
    """The parts of requests.Response the code reads."""

    def __init__(self, status: int = 200, body=None):
        self.status_code = status
        self.ok = status < 400
        self.reason = ""
        self._body = body

    def json(self):
        if self._body is None:
            raise requests.exceptions.JSONDecodeError("no body", "", 0)
        return self._body

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def chat_reply(obj) -> FakeResponse:
    """An OpenRouter answer whose text is ``obj`` as JSON (a string is sent as-is)."""
    text = obj if isinstance(obj, str) else json.dumps(obj)
    return FakeResponse(200, {"choices": [{"message": {"content": text}}]})
