"""Outbound HTTP for every layer: retries with backoff, the OS trust store, and the
one OpenRouter call that both model steps use.

Certificate verification is never turned off. ``truststore`` (in requirements.txt)
makes Python trust the same certificates as the browser, so networks that inspect
HTTPS still verify -- the same approach as ``ingest/fetch.py``.
"""

from __future__ import annotations

import os
import threading
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
            params: dict | None = None, timeout: float = 15,
            deadline: float | None = None) -> requests.Response:
    """Send one request, retrying a dropped connection, HTTP 429 or 5xx up to RETRIES times.

    Returns the last response, so the caller decides what a 4xx means. Raises the
    requests exception when the last attempt could not connect. Certificate and proxy
    failures come back the same every time, and a read timeout means the server is
    slow rather than gone, so those are raised at once instead of retried.

    ``deadline`` is a ``time.monotonic()`` reading. No retry starts after it, because by
    then nobody is waiting for the answer (see ``_within``).
    """
    for attempt in range(RETRIES + 1):
        try:
            resp = requests.request(method, url, headers=headers, json=json, params=params, timeout=timeout)
        except (requests.exceptions.SSLError, requests.exceptions.ProxyError):
            raise
        except requests.exceptions.ConnectionError:  # includes a connect timeout
            if _last_try(attempt, deadline):
                raise
        else:
            if (resp.status_code != 429 and resp.status_code < 500) or _last_try(attempt, deadline):
                return resp
        # Only a failure worth retrying gets this far: wait, then go round again.
        time.sleep(BACKOFF_S[attempt])


def _last_try(attempt: int, deadline: float | None) -> bool:
    """True when this attempt has to be the final one: the retries are used up, or the
    next one would only start after the deadline."""
    if attempt == RETRIES:
        return True
    return deadline is not None and time.monotonic() + BACKOFF_S[attempt] >= deadline


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
        # only providers that honour the schema and the seed; the fastest of them
        "provider": {"require_parameters": True, "sort": "throughput"},
        "reasoning": {"effort": "low"},
    }
    try:
        resp = _within(timeout, "POST", OPENROUTER_URL, headers={"Authorization": f"Bearer {key}"},
                       json=body, timeout=timeout)
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


def _within(seconds: float, *args, **kwargs) -> requests.Response:
    """request(), given up after ``seconds`` in total. requests' timeout limits each
    socket read, and OpenRouter keeps the connection alive with whitespace while the
    model works, so only a bound on the whole call stops a slow one. The abandoned
    worker is a daemon thread, so it never holds up shutdown, and the shared deadline
    stops it from starting retries whose answers nobody would read."""
    # ponytail: Python can't cancel a thread, so an abandoned call still runs its current
    # attempt to the end; stream the reply and close the connection if that ever costs too much.
    box: dict = {}
    deadline = time.monotonic() + seconds

    def run():
        try:
            box["resp"] = request(*args, deadline=deadline, **kwargs)
        except BaseException as err:  # re-raised in the caller's thread below
            box["err"] = err

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(seconds)
    if worker.is_alive():
        raise ModelError(f"no reply within {seconds:g} s")
    if "err" in box:
        raise box["err"]
    return box["resp"]


def _error_message(resp: requests.Response) -> str:
    """OpenRouter's own explanation, e.g. 'No endpoints found that can handle the requested parameters'."""
    try:
        return str(resp.json()["error"]["message"])[:200]
    except (ValueError, KeyError, TypeError):
        return resp.reason or "no details"
