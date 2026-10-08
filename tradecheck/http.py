"""Outbound HTTP for every layer: retries with backoff, the OS trust store, and the
one OpenRouter call that both model steps use.

Certificate verification is never turned off. ``truststore`` (in requirements.txt)
makes Python trust the same certificates as the browser, so networks that inspect
HTTPS still verify -- the same approach as ``ingest/fetch.py``.
"""

from __future__ import annotations

import os
import time

import requests

RETRIES = 3            # after the first attempt
BACKOFF_S = (1, 2, 4)  # wait before retry 1, 2 and 3

OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "qwen/qwen3.8-27b"
SEED = 42
CHAT_TIMEOUT_S = 40

try:
    import truststore
except ImportError:  # optional: certifi's bundle stays in place
    pass
else:
    truststore.inject_into_ssl()


class ModelError(Exception):
    """The model call failed: no key, no connection, an HTTP error, or a reply with no text."""


def request(method: str, url: str, *, headers: dict | None = None, json: dict | None = None,
            params: dict | None = None, timeout: float = 15) -> requests.Response:
    """Send one request, retrying a dropped connection, HTTP 429 or 5xx up to RETRIES times.

    Returns the last response, so the caller decides what a 4xx means. Raises the
    requests exception when the last attempt could not connect. Certificate and proxy
    failures come back the same every time, and a read timeout means the server is
    slow rather than gone, so those are raised at once instead of retried.
    """
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.request(method, url, headers=headers, json=json, params=params, timeout=timeout)
        except (requests.exceptions.SSLError, requests.exceptions.ProxyError):
            raise
        except requests.exceptions.ConnectionError:  # includes a connect timeout
            if attempt == RETRIES:
                raise
        else:
            if attempt == RETRIES or (resp.status_code != 429 and resp.status_code < 500):
                return resp
        time.sleep(BACKOFF_S[attempt])


def chat(messages: list[dict], name: str, schema: dict, timeout: float = CHAT_TIMEOUT_S) -> str:
    """One OpenRouter call with the pinned settings. Returns the reply text, which the
    strict JSON schema ``schema`` shapes. Raises ModelError on any failure."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise ModelError("OPENROUTER_API_KEY is not set")
    body = {
        "model": MODEL,
        "messages": messages,
        "temperature": 0,
        "seed": SEED,
        "response_format": {"type": "json_schema", "json_schema": {"name": name, "strict": True, "schema": schema}},
        "provider": {"require_parameters": True},  # only providers that honour the schema and the seed
        "reasoning": {"effort": "low"},
    }
    try:
        resp = request("POST", OPENROUTER_URL, headers={"Authorization": f"Bearer {key}"}, json=body, timeout=timeout)
    except requests.RequestException as err:
        raise ModelError(f"no connection: {type(err).__name__}") from err
    if not resp.ok:
        raise ModelError(f"HTTP {resp.status_code}: {_error_message(resp)}")
    try:
        text = resp.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError) as err:
        raise ModelError(f"reply had no choices: {_error_message(resp)}") from err
    if not isinstance(text, str) or not text.strip():
        raise ModelError("reply had no text")
    return text


def _error_message(resp: requests.Response) -> str:
    """OpenRouter's own explanation, e.g. 'No endpoints found that can handle the requested parameters'."""
    try:
        return str(resp.json()["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return resp.reason or "no details"
