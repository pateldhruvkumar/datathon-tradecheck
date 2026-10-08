# TradeCheck Screening Flow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the workflow diagram's flow as a working demo for Build Session 3 (2026-10-09): a free-text question goes through an OpenSanctions match and a UN cross-check, fixed score bands, a cited model report for review and hit cases, a review queue, and an append-only audit log.

**Architecture:** One plain-function module per diagram box under `tradecheck/`, passing plain dicts. `tradecheck/http.py` is the only module that touches the network, so every test replaces one function with a fake. FastAPI serves four endpoints and one static page. SQLite (standard library) holds the audit log, the review queue and saved OpenSanctions responses.

**Tech Stack:** Python 3.10+ (tested on 3.14), requests with truststore, rapidfuzz, lxml, FastAPI with uvicorn[standard], sqlite3, pytest. Services: OpenSanctions hosted yente, OpenRouter (`qwen/qwen3.8-27b`), Tavily.

**Spec:** [`docs/superpowers/specs/2026-10-08-tradecheck-architecture-design.md`](../specs/2026-10-08-tradecheck-architecture-design.md), amended alongside this plan (see "Spec amendments" below). Read both before starting.

## Global Constraints

- No new dependencies. Remove `duckdb` from `requirements.txt`; everything else is already there, and `uvicorn[standard]` brings `python-dotenv`.
- Python 3.10 or later. Run every command from the repo root with the project's Python, after `pip install -r requirements.txt`.
- OpenSanctions: `POST https://api.opensanctions.org/match/sanctions` with `algorithm=logic-v2`, `threshold=0.7`, `limit=5`; header `Authorization: ApiKey <key>`; timeout 15 s.
- OpenRouter: `POST https://openrouter.ai/api/v1/chat/completions`, model `qwen/qwen3.8-27b`, `temperature: 0`, `seed: 42`, strict `json_schema` response format, `provider: {"require_parameters": true}`, `reasoning: {"effort": "low"}`; header `Authorization: Bearer <key>`; timeout 40 s.
- Tavily: `POST https://api.tavily.com/search` with `topic: "news"`, `search_depth: "basic"`, `max_results: 5`; header `Authorization: Bearer <key>`; timeout 15 s.
- Transport retries: up to 3 after the first attempt, waiting 1, 2 and 4 s, only on a dropped connection, HTTP 429 or HTTP 5xx.
- Bands: clear below 0.70, review from 0.70, hit from 0.90, unknown when the match check failed. Labels `Clear*`, `Caution`, `Avoid`, `Unknown`. Statuses `cleared`, `pending_review` (then `reviewed`), `flagged`, `unknown`.
- Layer 3: `MAX_RETRIES = 3` (at most 4 model calls), `DEADLINE_S = 60`, `PROMPT_VERSION = "report-v1"`.
- Keys live only in `.env`: `OPENSANCTIONS_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`. Never print, log or commit a key. The audit log records the model ID, never request headers.
- Never disable TLS verification.
- Fixed text: disclaimer "Not legal advice. Screening reflects the listed sources as of the dates shown."; footer "Sanctions data: OpenSanctions, CC BY-NC 4.0. Not legal advice."; next step for review "An analyst should compare the details and confirm or dismiss this match." and for hit "Hold and escalate. Don't proceed until reviewed."
- The page escapes every piece of text it inserts, and only `http://` and `https://` URLs become links.
- Tests never reach the network or write to `data/`. The shared fixtures in `tests/conftest.py` enforce both.
- Commit after each task on `dhruv/architecture-oct-08-2026`, ending every message with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Don't push unless the user asks.

## Review Focus

The five inputs or failures the spec implies but doesn't spell out that are most likely to bite during the demo, most likely first. Each one has a test in the task named.

1. **OpenRouter fails at the venue and the question is a full sentence.** The fallback screens the whole sentence as the name, and a listed name inside a sentence can score under 0.70. Expected: never Clear\*; the case goes to review and the reason is shown. Tests: Task 6 `test_a_fallback_extraction_is_never_clear`, Task 9 `test_a_fallback_extraction_is_not_clear`.
2. **The demo's own review case, "Chief Kahwa".** A Low alias scores at most 0.60, so the spec's literal rule (b) would call the UN check "disagree" even though the UN list contains the name. Expected: agree. Test: Task 7 `test_a_low_alias_match_is_not_a_disagreement`.
3. **A slow or hung model.** Retrying a 40 s read timeout three times, with the deadline checked only between attempts, would make one screen take minutes. Expected: Layer 3 finishes close to 60 s, with the template if needed. Tests: Task 2 `test_slow_or_deterministic_failures_are_not_retried`; Task 8 `test_the_deadline_stops_new_attempts` and `test_each_model_call_gets_only_the_time_left`.
4. **A truncated or missing UN file** (an interrupted download, a fresh clone). Expected: the app still starts and the UN check is unavailable, so nothing shows Clear\*. Test: Task 7 `test_a_missing_or_truncated_file_makes_every_check_unavailable`.
5. **A name with no Latin letters**, for example in Cyrillic. It normalizes to nothing, so the UN check would silently find no match. Expected: the UN check is unavailable, so the result is not Clear\*. Test: Task 7 `test_a_name_without_latin_letters_is_unavailable`.

## Spec amendments

Planning found these gaps in the approved spec. The spec file is updated in the same commit as this plan, so the two documents agree.

1. **UN rule (b)** now reads "no UN name is at least 70% similar" instead of "the UN check's best score is below 0.70". `best` is the highest weighted score among UN names whose raw similarity is at least 70, or `null`. (Review Focus 2.)
2. **A fallback extraction is never clear.** Layer 2 raises it to review, like a UN doubt. (Review Focus 1.)
3. **A name that normalizes to nothing** makes the UN check unavailable. (Review Focus 5.)
4. **Timeouts.** `http.request` doesn't retry a read timeout, a certificate failure or a proxy refusal. Layer 3 caps each model call at the time left before the deadline, and a model error goes straight to the template. (Review Focus 3.)
5. **One review per screen** is enforced by a partial unique index, so two reviewers at once can't both succeed. The second gets 409.
6. **A review with no matcher candidate**, raised by the UN check or a fallback extraction, gets a fixed report. There is nothing for Layer 3 to explain.
7. **The shared OpenRouter call** lives in `http.py` as `chat()`, with `MODEL`, `SEED` and `CHAT_TIMEOUT_S`.
8. **Extra fields:** `fallback_reason` and `properties` on the parsed query, `list_date` on the UN check, `reasons` on the screen response, and `check_reason` and `steps` on reports.
9. **Extra test files:** `tests/test_layer1_yente.py` and `tests/test_app.py`, plus shared helpers in `tests/fakes.py` and fixtures in `tests/conftest.py`.

## File structure

| Path | Task | Responsibility |
| --- | --- | --- |
| `ingest/fetch.py` | 1 | Download the UN list (source key `un_sc`) with its provenance |
| `ingest/normalize.py` | 1 | `normalize_name()`, used by the UN check |
| `tradecheck/http.py` | 2 | `request()` with retries; `chat()`, the one OpenRouter call |
| `tradecheck/audit.py` | 3 | SQLite events, review queue, reviews, saved yente responses |
| `tradecheck/parse.py` | 4 | Model call 1: question to validated fields to yente properties |
| `tradecheck/layer1_yente.py` | 5 | `match()`, `entity()`, `catalog_as_of()` |
| `tradecheck/layer2.py` | 6 | `band()` and the label and status tables |
| `tradecheck/layer1_un.py` | 7 | `load()` the UN XML; `check()` returns agree, disagree or unavailable |
| `tradecheck/layer3.py` | 8 | Evidence bundle, model call 2, citation check, retries, template |
| `tradecheck/pipeline.py` | 9 | `start()`, `screen()`, fixed reports, command line |
| `app/main.py` | 10 | `POST /screen`, `GET /queue`, `POST /review/{id}`, `GET /audit/{id}`, `GET /health` |
| `app/static/index.html` | 11 | Question box, result card, review queue panel |
| `RUN.md`, `README.md` | 12 | How to run it; the README's build section |
| `tests/fakes.py`, `tests/conftest.py` | 2, 3 | `FakeResponse` and `chat_reply`; no network, test keys, `fake_http`, a fresh database per test |

---

### Task 1: Retire the OFAC/Canada pipeline and fetch the UN list

The old direct-pull pipeline (OFAC and Canada parsers, DuckDB loader, `match/`) is replaced by the hosted matcher. The download helper and name normalization stay.

**Files:**
- Delete: `ingest/parse_ofac.py`, `ingest/parse_canada.py`, `ingest/load.py`, `match/__init__.py`, `match/screen.py`, `tests/test_parsers.py`, `tests/test_screen.py`, `tests/conftest.py`, `tests/fixtures/ofac_sdn_sample.xml`, `tests/fixtures/canada_sema_sample.xml`
- Modify: `ingest/fetch.py`, `ingest/normalize.py`, `ingest/__init__.py`, `app/main.py`, `requirements.txt`, `.gitignore`
- Create: `.env.example`
- Test: `tests/test_fetch.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `ingest.fetch.SOURCES == {"un_sc": ("https://scsanctions.un.org/resources/xml/en/consolidated.xml", "un_sc.xml")}` and `ingest.fetch.RAW_DIR` (unchanged), used by Task 7. `ingest.normalize.normalize_name(raw: str | None) -> str` (unchanged), used by Task 7.

- [ ] **Step 1: Point the test at the new source**

In `tests/test_fetch.py`, in `test_certificate_failure_gets_tls_hint_not_proxy_hint`, replace

```python
    fetch.fetch(only=["ca_sema"])
```

with

```python
    fetch.fetch(only=["un_sc"])
```

- [ ] **Step 2: Run the test to see it fail**

Run: `python -m pytest tests/test_fetch.py -v`
Expected: the two `test_certificate_failure_gets_tls_hint_not_proxy_hint` cases FAIL with `KeyError: 'un_sc'`; the other 5 pass.

- [ ] **Step 3: Switch `ingest/fetch.py` to the UN list**

Make these six replacements in `ingest/fetch.py`.

Replace the first paragraph of the module docstring:

```python
"""Download the US (OFAC) and Canada (GAC) sanctions lists directly from source.

Writes raw files to ``data/raw/`` and a ``manifest.json`` recording the URL,
```

with

```python
"""Download the UN Security Council consolidated sanctions list directly from source.

TradeCheck screens against the hosted OpenSanctions matcher; this file is the
independent copy behind the UN cross-check (``tradecheck/layer1_un.py``).

Writes raw files to ``data/raw/`` and a ``manifest.json`` recording the URL,
```

Replace `Both sources are free government data with no key and no licence restriction.` with `The list is free government data with no key and no licence restriction.`

Replace

```python
Run:  python -m ingest.fetch                 # all lists
      python -m ingest.fetch --only ca_sema  # just Canada
```

with

```python
Run:  python -m ingest.fetch               # every source (today: just the UN list)
      python -m ingest.fetch --only un_sc
```

Replace the whole `SOURCES` block, from `# key -> (url, local filename). SDN + Canada are must-haves; CONS is optional.` through its closing `}`, with

```python
# key -> (url, local filename). The UN answers with a redirect to Azure blob
# storage; requests follows it.
SOURCES: dict[str, tuple[str, str]] = {
    "un_sc": (
        "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
        "un_sc.xml",
    ),
}
```

Replace the `"proxy"` hint text

```python
        "A proxy refused the connection. In a Claude Code cloud session, add "
        "'sanctionslistservice.ofac.treas.gov' and 'www.international.gc.ca' under the "
        "environment's Network access > Allowed domains, or run this fetch on your own machine."
```

with

```python
        "A proxy refused the connection. In a Claude Code cloud session, add "
        "'scsanctions.un.org' and 'unsolprodfiles.blob.core.windows.net' (where the UN "
        "redirects the download) under the environment's Network access > Allowed domains, "
        "or run this fetch on your own machine."
```

Replace `ap = argparse.ArgumentParser(description="Fetch US + Canada sanctions lists.")` with `ap = argparse.ArgumentParser(description="Fetch the UN Security Council sanctions list.")`.

- [ ] **Step 4: Run the test to see it pass**

Run: `python -m pytest tests/test_fetch.py -v`
Expected: 7 passed.

- [ ] **Step 5: Delete the old pipeline**

```bash
git rm -q ingest/parse_ofac.py ingest/parse_canada.py ingest/load.py match/__init__.py match/screen.py tests/test_parsers.py tests/test_screen.py tests/conftest.py tests/fixtures/ofac_sdn_sample.xml tests/fixtures/canada_sema_sample.xml
```

- [ ] **Step 6: Trim what referred to it**

In `ingest/normalize.py`, replace the start of the module docstring

```python
"""Shared normalized record schema and name-normalization helpers.

Both the OFAC and Global Affairs Canada parsers emit ``NormalizedRecord`` objects,
so the loader and matcher never need to know which list a record came from.

Name normalization
```

with

```python
"""Name normalization for the UN cross-check (``tradecheck/layer1_un.py``).

Name normalization
```

replace the imports

```python
import re
import unicodedata
from dataclasses import dataclass, field, asdict
from typing import Any
```

with

```python
import re
import unicodedata
```

and delete everything from `@dataclass` / `class Alias:` to the end of the file (the `Alias` and `NormalizedRecord` dataclasses). The file now ends with the `return " ".join(tokens)` line of `normalize_name`.

Replace the whole of `ingest/__init__.py` with:

```python
"""TradeCheck ingestion package: fetch the UN list and normalize names."""
```

Replace the whole of `app/main.py` with this version, which drops the import of the deleted matcher. Task 10 adds the new endpoints.

```python
"""FastAPI app: serves the page and /health. The screening endpoints come back
with the new pipeline in ``tradecheck/``.

Run:  uvicorn app.main:app --reload
      open http://127.0.0.1:8000
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

STATIC_DIR = Path(__file__).resolve().parent / "static"

app = FastAPI(title="TradeCheck", description="Free US + Canada sanctions screening for small businesses.")


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Serve the static assets (index.html and anything added later).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
```

In `requirements.txt`, delete the line `duckdb>=1.1`.

In `.gitignore`, replace

```text
# TradeCheck data: downloaded source lists and the built store are not committed
data/raw/
data/*.duckdb
```

with

```text
# TradeCheck data: the downloaded UN list and the audit database are not committed
data/raw/
data/*.sqlite*

# API keys
.env
```

Create `.env.example`:

```text
# Copy to .env and fill in. Never commit .env (it is in .gitignore).
OPENSANCTIONS_API_KEY=
OPENROUTER_API_KEY=
TAVILY_API_KEY=
```

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest -v`
Expected: 10 passed (7 in `test_fetch.py`, 3 in `test_normalize.py`).

Run: `python -c "import app.main"`
Expected: no output, no error.

- [ ] **Step 8: Commit**

```bash
git add -A ingest app tests requirements.txt .gitignore .env.example
git commit -m "refactor: retire the OFAC/Canada pipeline and fetch the UN list" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Outbound HTTP: retries and the OpenRouter call

Every network call in the app goes through `http.request`, and both model calls go through `http.chat`. The test helpers and fixtures created here are shared by every later test file.

**Files:**
- Create: `tradecheck/__init__.py`, `tradecheck/http.py`, `tests/fakes.py`, `tests/conftest.py`
- Test: `tests/test_http.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `http.request(method: str, url: str, *, headers: dict | None = None, json: dict | None = None, params: dict | None = None, timeout: float = 15) -> requests.Response`. Retries a `requests.exceptions.ConnectionError` (including a connect timeout), HTTP 429 or 5xx up to 3 times, sleeping 1, 2 and 4 s. Returns the last response. Raises `ReadTimeout`, `SSLError` and `ProxyError` at once, and the last `ConnectionError` after the retries.
  - `http.chat(messages: list[dict], name: str, schema: dict, timeout: float = 40) -> str`. Returns the reply text. Raises `http.ModelError` on any failure, including a missing `OPENROUTER_API_KEY` (no request is sent).
  - Constants `http.MODEL = "qwen/qwen3.8-27b"`, `http.SEED = 42`, `http.CHAT_TIMEOUT_S = 40`.
  - `tests.fakes.FakeResponse(status: int = 200, body=None)` with `status_code`, `ok`, `reason`, `json()` and `raise_for_status()`; `tests.fakes.chat_reply(obj) -> FakeResponse` (an OpenRouter reply whose text is `obj` as JSON, or `obj` itself if it is a string).
  - Fixtures in `tests/conftest.py`: autouse `no_network` (any real request fails the test), autouse `fake_keys` (all three keys set to `"test-key"`), and `fake_http`, which returns `(routes, calls)`. `routes` maps a URL fragment to a `FakeResponse`, an exception, or a function of the request's keyword arguments returning either; `calls` collects `(method, url, kwargs)`.

- [ ] **Step 1: Write the test helpers**

Create `tests/fakes.py`:

```python
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
```

Create `tests/conftest.py`:

```python
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
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_http.py`:

```python
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
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_http.py -v`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'tradecheck'`.

- [ ] **Step 4: Write the module**

Create `tradecheck/__init__.py`:

```python
"""TradeCheck screening: one module per box in the workflow diagram."""
```

Create `tradecheck/http.py`:

```python
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
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_http.py -v`
Expected: 13 passed.

Run: `python -m pytest`
Expected: 23 passed.

- [ ] **Step 6: Commit**

```bash
git add tradecheck tests/fakes.py tests/conftest.py tests/test_http.py
git commit -m "feat: add HTTP retries and the OpenRouter call" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Audit log and review queue

**Files:**
- Create: `tradecheck/audit.py`
- Modify: `tests/conftest.py`
- Test: `tests/test_audit.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `audit.DB_PATH` (`data/tradecheck.sqlite`), `audit.init() -> None`, `audit.now() -> str` (ISO-8601 UTC, seconds).
  - `audit.log(screen_id: str, step: str, data: dict) -> None`; `audit.record(screen_id: str) -> list[dict]`, each `{"id", "at", "step", "data"}`, oldest first.
  - `audit.queue() -> list[dict]`, each `{"screen_id", "at", "question", "score"}`.
  - `audit.review(screen_id: str, decision: str, note: str, reviewer: str) -> dict` returning `{"status": "reviewed", "decision": decision}`. Raises `audit.NotFound` (no events), `audit.NotInReview` (the `layer2` band isn't review) or `audit.AlreadyReviewed` (second review).
  - `audit.cache_put(query_hash: str, candidates: list, fetched_at: str) -> None`; `audit.cache_get(query_hash: str) -> {"candidates", "fetched_at"} | None`.
  - Fixture: autouse `db` gives every test its own empty database.

- [ ] **Step 1: Give every test its own database**

In `tests/conftest.py`, replace `from tradecheck import http` with `from tradecheck import audit, http`, and add this fixture at the end of the file:

```python


@pytest.fixture(autouse=True)
def db(tmp_path, monkeypatch):
    """Each test gets its own empty audit database instead of data/tradecheck.sqlite."""
    monkeypatch.setattr(audit, "DB_PATH", tmp_path / "tradecheck.sqlite")
    audit.init()
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_audit.py`:

```python
"""The audit log: append-only triggers, the review queue, one review per case, the cache."""

import sqlite3

import pytest

from tradecheck import audit


def _screen(screen_id, band, question="Can we ship to Northwind Metals FZE?"):
    audit.log(screen_id, "input", {"question": question})
    audit.log(screen_id, "layer2", {"band": band, "score": 0.83})
    audit.log(screen_id, "final", {"status": "pending_review"})


@pytest.mark.parametrize("sql", ["UPDATE events SET step = 'final'", "DELETE FROM events"])
def test_events_cannot_be_changed_or_removed(sql):
    _screen("s1", "review")
    con = sqlite3.connect(audit.DB_PATH)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        con.execute(sql)
    con.close()
    assert len(audit.record("s1")) == 3


def test_record_returns_the_events_in_order():
    _screen("s1", "review")
    events = audit.record("s1")
    assert [e["step"] for e in events] == ["input", "layer2", "final"]
    assert events[0]["data"] == {"question": "Can we ship to Northwind Metals FZE?"}


def test_queue_lists_only_unreviewed_review_cases():
    _screen("a", "review", "Question A")
    _screen("b", "review", "Question B")
    _screen("c", "hit", "Question C")
    audit.review("a", "dismiss", "Different date of birth", "Analyst A")
    assert [(i["screen_id"], i["question"], i["score"]) for i in audit.queue()] == [("b", "Question B", 0.83)]


def test_review_writes_a_review_and_a_final_event():
    _screen("a", "review")
    assert audit.review("a", "confirm", "Same DOB and country", "Analyst A") == {
        "status": "reviewed", "decision": "confirm"}
    assert [(e["step"], e["data"]) for e in audit.record("a")][-2:] == [
        ("review", {"reviewer": "Analyst A", "decision": "confirm", "note": "Same DOB and country"}),
        ("final", {"status": "reviewed", "decision": "confirm"}),
    ]


def test_second_review_is_refused_and_writes_nothing():
    _screen("a", "review")
    audit.review("a", "confirm", "Same DOB", "Analyst A")
    with pytest.raises(audit.AlreadyReviewed):
        audit.review("a", "dismiss", "Changed my mind", "Analyst B")
    assert [e["step"] for e in audit.record("a")] == ["input", "layer2", "final", "review", "final"]


def test_review_of_an_unknown_or_non_review_case():
    _screen("h", "hit")
    with pytest.raises(audit.NotFound):
        audit.review("nope", "confirm", "note", "Analyst A")
    with pytest.raises(audit.NotInReview):
        audit.review("h", "confirm", "note", "Analyst A")


def test_cache_round_trip():
    assert audit.cache_get("abc") is None
    audit.cache_put("abc", [{"id": "NK-1", "score": 0.9}], "2026-10-08T20:00:00+00:00")
    assert audit.cache_get("abc") == {
        "candidates": [{"id": "NK-1", "score": 0.9}], "fetched_at": "2026-10-08T20:00:00+00:00"}
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_audit.py -v`
Expected: ERROR while loading `tests/conftest.py`: `ImportError: cannot import name 'audit' from 'tradecheck'`.

- [ ] **Step 4: Write the module**

Create `tradecheck/audit.py`:

```python
"""Append-only audit log, review queue and saved yente responses, in SQLite.

The database enforces the rules itself: triggers reject UPDATE and DELETE on
events, and a partial unique index allows one review per screen.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "tradecheck.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  screen_id TEXT NOT NULL,
  at        TEXT NOT NULL,   -- ISO-8601 UTC
  step      TEXT NOT NULL,   -- input | layer1 | layer2 | layer3 | review | final
  data      TEXT NOT NULL    -- JSON
);
CREATE INDEX IF NOT EXISTS events_screen ON events(screen_id);
CREATE UNIQUE INDEX IF NOT EXISTS one_review_per_screen ON events(screen_id) WHERE step = 'review';
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TABLE IF NOT EXISTS yente_cache (
  query_hash TEXT PRIMARY KEY, response TEXT NOT NULL, fetched_at TEXT NOT NULL
);
"""

# Review-band screens with no review yet. One query, no extra table.
QUEUE_SQL = """
SELECT l2.screen_id, l2.at, json_extract(inp.data, '$.question'), json_extract(l2.data, '$.score')
FROM events AS l2
JOIN events AS inp ON inp.screen_id = l2.screen_id AND inp.step = 'input'
WHERE l2.step = 'layer2' AND json_extract(l2.data, '$.band') = 'review'
  AND NOT EXISTS (SELECT 1 FROM events AS r WHERE r.screen_id = l2.screen_id AND r.step = 'review')
ORDER BY l2.id
"""


class NotFound(LookupError):
    """No events exist for this screen ID."""


class NotInReview(ValueError):
    """Only review-band cases can be reviewed."""


class AlreadyReviewed(ValueError):
    """The case already has its review."""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _db():
    con = sqlite3.connect(DB_PATH)
    try:
        with con:  # commit on success, roll back on error
            yield con
    finally:
        con.close()


def init() -> None:
    """Create the database file and its tables if they don't exist yet."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with _db() as con:
        con.executescript(SCHEMA)


def _insert(con: sqlite3.Connection, screen_id: str, step: str, data: dict) -> None:
    con.execute(
        "INSERT INTO events (screen_id, at, step, data) VALUES (?, ?, ?, ?)",
        (screen_id, now(), step, json.dumps(data, ensure_ascii=False)),
    )


def log(screen_id: str, step: str, data: dict) -> None:
    with _db() as con:
        _insert(con, screen_id, step, data)


def record(screen_id: str) -> list[dict]:
    """Every event for one screen, oldest first: the diagram's "one row per screen"."""
    with _db() as con:
        rows = con.execute(
            "SELECT id, at, step, data FROM events WHERE screen_id = ? ORDER BY id", (screen_id,)
        ).fetchall()
    return [{"id": i, "at": at, "step": step, "data": json.loads(data)} for i, at, step, data in rows]


def queue() -> list[dict]:
    with _db() as con:
        rows = con.execute(QUEUE_SQL).fetchall()
    return [{"screen_id": s, "at": at, "question": q, "score": score} for s, at, q, score in rows]


def review(screen_id: str, decision: str, note: str, reviewer: str) -> dict:
    """Record the analyst's decision and the new status. Returns the final event's data."""
    events = record(screen_id)
    if not events:
        raise NotFound(f"No screen with ID {screen_id}")
    band = next((e["data"].get("band") for e in events if e["step"] == "layer2"), None)
    if band != "review":
        raise NotInReview("Only cases in the review band can be reviewed")
    final = {"status": "reviewed", "decision": decision}
    try:
        with _db() as con:
            _insert(con, screen_id, "review", {"reviewer": reviewer, "decision": decision, "note": note})
            _insert(con, screen_id, "final", final)
    except sqlite3.IntegrityError as err:  # the one_review_per_screen index
        raise AlreadyReviewed("This case has already been reviewed") from err
    return final


def cache_put(query_hash: str, candidates: list, fetched_at: str) -> None:
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO yente_cache (query_hash, response, fetched_at) VALUES (?, ?, ?)",
            (query_hash, json.dumps(candidates, ensure_ascii=False), fetched_at),
        )


def cache_get(query_hash: str) -> dict | None:
    with _db() as con:
        row = con.execute(
            "SELECT response, fetched_at FROM yente_cache WHERE query_hash = ?", (query_hash,)
        ).fetchone()
    return None if row is None else {"candidates": json.loads(row[0]), "fetched_at": row[1]}
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_audit.py -v`
Expected: 8 passed.

Run: `python -m pytest`
Expected: 31 passed, and no `data/` folder appears in the repo.

- [ ] **Step 6: Commit**

```bash
git add tradecheck/audit.py tests/conftest.py tests/test_audit.py
git commit -m "feat: add the append-only audit log and review queue" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Input step: extract the party (model call 1)

**Files:**
- Create: `tradecheck/parse.py`
- Test: `tests/test_parse.py`

**Interfaces:**
- Consumes: `http.chat`, `http.ModelError` (Task 2).
- Produces:
  - `parse.extract(question: str) -> dict` with keys `schema` (`Person`, `Company` or `LegalEntity`), `name`, `country` (2 lowercase letters or `None`), `birth_date`, `registration_number`, `source` (`"model"` or `"fallback"`), `fallback_reason` (`None` or why the model failed) and `properties` (the yente properties, each value a one-item list).
  - Raises `parse.NoParty` with the message `parse.NO_PARTY` ("No party name found in the question") for a blank question or a null name.
  - `parse.PROMPT_VERSION = "extract-v1"`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_parse.py`:

```python
"""Model call 1 and the code that checks it. OpenRouter is faked."""

import pytest

from tests.fakes import FakeResponse, chat_reply
from tradecheck import parse


def _model(fake_http, answer):
    routes, calls = fake_http
    routes["openrouter.ai"] = answer
    return calls


def _fields(schema="Person", name="Khawa Panga Mandro", country=None, birth_date=None, registration_number=None):
    return {"schema": schema, "name": name, "country": country,
            "birth_date": birth_date, "registration_number": registration_number}


def test_person_fields_become_yente_properties(fake_http):
    _model(fake_http, chat_reply(_fields(name=" Khawa Panga Mandro ", country="CD", birth_date="1974-08-20")))
    q = parse.extract("Is Khawa Panga Mandro, born 1974-08-20 in Congo, on a list?")
    assert (q["source"], q["fallback_reason"]) == ("model", None)
    assert (q["schema"], q["name"], q["country"], q["birth_date"]) == ("Person", "Khawa Panga Mandro", "cd", "1974-08-20")
    assert q["properties"] == {"name": ["Khawa Panga Mandro"], "country": ["cd"], "birthDate": ["1974-08-20"]}


def test_company_uses_jurisdiction_and_registration_number(fake_http):
    _model(fake_http, chat_reply(_fields("Company", "Northwind Metals FZE", "ae", registration_number="DMCC-123")))
    q = parse.extract("Can we ship to Northwind Metals FZE (DMCC-123) in Dubai?")
    assert q["properties"] == {"name": ["Northwind Metals FZE"], "jurisdiction": ["ae"],
                               "registrationNumber": ["DMCC-123"]}


def test_bad_schema_country_and_date_are_dropped(fake_http):
    _model(fake_http, chat_reply(_fields("Vessel", "Northwind", "UAE", "20 Aug 1974", " ")))
    q = parse.extract("Northwind")
    assert (q["schema"], q["country"], q["birth_date"], q["registration_number"]) == ("LegalEntity", None, None, None)
    assert q["properties"] == {"name": ["Northwind"]}


@pytest.mark.parametrize("answer", [FakeResponse(500), FakeResponse(401), chat_reply("not json"), chat_reply([1, 2])])
def test_model_failure_screens_the_whole_question(fake_http, answer):
    _model(fake_http, answer)
    q = parse.extract("  Can we ship to Northwind Metals FZE?  ")
    assert q["source"] == "fallback" and q["fallback_reason"]
    assert q["schema"] == "LegalEntity"
    assert q["properties"] == {"name": ["Can we ship to Northwind Metals FZE?"]}


def test_null_name_means_no_party(fake_http):
    _model(fake_http, chat_reply(_fields("LegalEntity", None)))
    with pytest.raises(parse.NoParty, match="No party name found in the question"):
        parse.extract("What are the export rules for Mexico?")


def test_blank_question_makes_no_model_call(fake_http):
    calls = _model(fake_http, chat_reply(_fields()))
    with pytest.raises(parse.NoParty):
        parse.extract("   ")
    assert calls == []
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_parse.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'parse' from 'tradecheck'`.

- [ ] **Step 3: Write the module**

Create `tradecheck/parse.py`:

```python
"""Input step: turn a free-text question into the yente query (model call 1).

The model only extracts fields; code validates every one of them. If the model call
fails or its JSON is unusable, the whole question is screened as the name.
"""

from __future__ import annotations

import json
import re

from tradecheck import http

PROMPT_VERSION = "extract-v1"
SCHEMAS = ("Person", "Company", "LegalEntity")
NO_PARTY = "No party name found in the question"
_COUNTRY = re.compile(r"[a-z]{2}")
_DATE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")

SYSTEM = """You extract the counterparty from a question about a trade deal.
Return the one person or organisation the user wants to deal with:
- schema: "Person" for a human, "Company" for a business, "LegalEntity" if unsure.
- name: the party's name exactly as written, without the rest of the question.
- country: the party's country as an ISO 3166-1 alpha-2 code (e.g. "ae"), only if stated.
- birth_date: YYYY, YYYY-MM or YYYY-MM-DD, only if stated.
- registration_number: a company registration number, only if stated.
Use null for anything not stated. If the question names no party, name is null. Never guess."""

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "schema": {"type": "string", "enum": list(SCHEMAS)},
        "name": {"type": ["string", "null"]},
        "country": {"type": ["string", "null"]},
        "birth_date": {"type": ["string", "null"]},
        "registration_number": {"type": ["string", "null"]},
    },
    "required": ["schema", "name", "country", "birth_date", "registration_number"],
    "additionalProperties": False,
}


class NoParty(Exception):
    """The question names no party to screen (the API answers 422)."""


def extract(question: str) -> dict:
    """Return the cleaned fields, where they came from, and the yente properties:
    {"schema", "name", "country", "birth_date", "registration_number",
     "source": "model" | "fallback", "fallback_reason", "properties"}."""
    question = question.strip()
    if not question:
        raise NoParty(NO_PARTY)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    try:
        fields = _clean(json.loads(http.chat(messages, "query", JSON_SCHEMA)))
        source, reason = "model", None
    except (http.ModelError, ValueError) as err:
        fields = {"schema": "LegalEntity", "name": question, "country": None,
                  "birth_date": None, "registration_number": None}
        source, reason = "fallback", str(err)
    return {**fields, "source": source, "fallback_reason": reason, "properties": _properties(fields)}


def _clean(raw) -> dict:
    """Validate the model's fields. Raises NoParty when it found no name."""
    if not isinstance(raw, dict):
        raise ValueError("the model's answer is not a JSON object")
    name = _text(raw.get("name"))
    if not name:
        raise NoParty(NO_PARTY)
    country = _text(raw.get("country")).lower()
    birth_date = _text(raw.get("birth_date"))
    return {
        "schema": raw.get("schema") if raw.get("schema") in SCHEMAS else "LegalEntity",
        "name": name,
        "country": country if _COUNTRY.fullmatch(country) else None,
        "birth_date": birth_date if _DATE.fullmatch(birth_date) else None,
        "registration_number": _text(raw.get("registration_number")) or None,
    }


def _text(value) -> str:
    return value.strip() if isinstance(value, str) else ""


def _properties(fields: dict) -> dict:
    """The FollowTheMoney properties sent to yente. Each value is a one-item list."""
    extra = {
        "Person": {"country": fields["country"], "birthDate": fields["birth_date"]},
        "Company": {"jurisdiction": fields["country"], "registrationNumber": fields["registration_number"]},
        "LegalEntity": {"country": fields["country"]},
    }[fields["schema"]]
    return {"name": [fields["name"]], **{key: [value] for key, value in extra.items() if value}}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_parse.py -v`
Expected: 9 passed.

Run: `python -m pytest`
Expected: 40 passed.

- [ ] **Step 5: Commit**

```bash
git add tradecheck/parse.py tests/test_parse.py
git commit -m "feat: extract the party from the question (model call 1)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Layer 1a: the OpenSanctions matcher

**Files:**
- Create: `tradecheck/layer1_yente.py`
- Test: `tests/test_layer1_yente.py`

**Interfaces:**
- Consumes: `http.request` (Task 2); `audit.cache_get`, `audit.cache_put`, `audit.now` (Task 3); the `schema` and `properties` keys of the dict from `parse.extract` (Task 4).
- Produces:
  - `layer1_yente.match(query: dict) -> dict` returning `{"candidates": [full yente results, best first], "live": bool, "fetched_at": str, "query_hash": str}`. When the live call fails it returns the saved answer with `live: False` and the original `fetched_at`; with nothing saved it raises `layer1_yente.YenteUnavailable`, whose message is a readable reason such as "OpenSanctions answered HTTP 503".
  - `layer1_yente.entity(entity_id: str) -> dict | None` (the nested record; `None` on failure).
  - `layer1_yente.catalog_as_of() -> str | None`.
  - `layer1_yente.query_hash(query: dict) -> str`; constants `COLLECTION = "sanctions"`, `ALGORITHM = "logic-v2"`, `THRESHOLD = 0.7`, `LIMIT = 5`, `TIMEOUT_S = 15`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layer1_yente.py`:

```python
"""Layer 1a against a faked OpenSanctions API."""

import pytest

from tests.fakes import FakeResponse
from tradecheck import layer1_yente as yente

QUERY = {"schema": "Person", "name": "Khawa Panga Mandro", "properties": {"name": ["Khawa Panga Mandro"]}}


def _results(*scores):
    return FakeResponse(200, {"responses": {"q": {"results": [
        {"id": f"NK-{i}", "caption": f"Candidate {i}", "schema": "Person", "score": score,
         "datasets": ["un_sc_sanctions"]}
        for i, score in enumerate(scores)]}}})


def test_match_sends_the_pinned_query(fake_http):
    routes, calls = fake_http
    routes["/match/sanctions"] = _results(0.95)
    yente.match(QUERY)
    method, url, kwargs = calls[0]
    assert (method, url) == ("POST", "https://api.opensanctions.org/match/sanctions")
    assert kwargs["params"] == {"algorithm": "logic-v2", "threshold": 0.7, "limit": 5}
    assert kwargs["json"] == {"queries": {"q": {"schema": "Person", "properties": {"name": ["Khawa Panga Mandro"]}}}}
    assert kwargs["headers"] == {"Authorization": "ApiKey test-key"}
    assert kwargs["timeout"] == 15


def test_candidates_come_back_best_first_and_are_replayed_when_yente_fails(fake_http):
    routes, _ = fake_http
    routes["/match/sanctions"] = _results(0.72, 0.95)
    live = yente.match(QUERY)
    assert live["live"] is True
    assert [c["score"] for c in live["candidates"]] == [0.95, 0.72]

    routes["/match/sanctions"] = FakeResponse(503)
    saved = yente.match(QUERY)
    assert saved["live"] is False
    assert saved["candidates"] == live["candidates"]
    assert saved["fetched_at"] == live["fetched_at"]
    assert saved["query_hash"] == live["query_hash"]


@pytest.mark.parametrize("answer, reason", [
    (FakeResponse(401), "OpenSanctions answered HTTP 401"),
    (FakeResponse(200, {"responses": {}}), "unexpected answer from OpenSanctions"),
    (FakeResponse(200), "unexpected answer from OpenSanctions"),
])
def test_failure_with_nothing_saved_is_unavailable(fake_http, answer, reason):
    routes, _ = fake_http
    routes["/match/sanctions"] = answer
    with pytest.raises(yente.YenteUnavailable, match=reason):
        yente.match(QUERY)


def test_entity_lookup(fake_http):
    routes, calls = fake_http
    routes["/entities/"] = FakeResponse(200, {"id": "NK-1", "properties": {}})
    assert yente.entity("NK-1") == {"id": "NK-1", "properties": {}}
    assert calls[0][1] == "https://api.opensanctions.org/entities/NK-1"
    routes["/entities/"] = FakeResponse(500)
    assert yente.entity("NK-1") is None


def test_catalog_as_of_reads_the_sanctions_collection(fake_http):
    routes, _ = fake_http
    routes["/catalog"] = FakeResponse(200, {"datasets": [
        {"name": "peps", "updated_at": "2026-10-01T00:00:00"},
        {"name": "sanctions", "updated_at": "2026-10-08T06:00:00", "version": "20261008060000-abc"},
    ]})
    assert yente.catalog_as_of() == "2026-10-08T06:00:00"
    routes["/catalog"] = FakeResponse(503)
    assert yente.catalog_as_of() is None
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_layer1_yente.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'layer1_yente' from 'tradecheck'`.

- [ ] **Step 3: Write the module**

Create `tradecheck/layer1_yente.py`:

```python
"""Layer 1a: the hosted OpenSanctions matcher (yente).

The collection, algorithm and threshold are pinned, so the same query gets the same
answer. Every live answer is saved; when the live call fails, the saved answer for
the same query is used and marked ``live: false``.
"""

from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import quote

import requests

from tradecheck import audit, http

BASE_URL = "https://api.opensanctions.org"
COLLECTION = "sanctions"
ALGORITHM = "logic-v2"
THRESHOLD = 0.7
LIMIT = 5
TIMEOUT_S = 15


class YenteUnavailable(Exception):
    """The live match failed and nothing is saved for this query."""


def _headers() -> dict:
    return {"Authorization": f"ApiKey {os.environ.get('OPENSANCTIONS_API_KEY', '')}"}


def query_hash(query: dict) -> str:
    """SHA-256 of the canonical JSON of what is sent, plus the pinned settings."""
    sent = {"collection": COLLECTION, "algorithm": ALGORITHM, "threshold": THRESHOLD, "limit": LIMIT,
            "query": {"schema": query["schema"], "properties": query["properties"]}}
    return hashlib.sha256(json.dumps(sent, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def match(query: dict) -> dict:
    """Return {"candidates" (best first), "live", "fetched_at", "query_hash"}.
    Raises YenteUnavailable when the call fails and nothing is saved."""
    qhash = query_hash(query)
    try:
        resp = http.request(
            "POST", f"{BASE_URL}/match/{COLLECTION}",
            headers=_headers(),
            params={"algorithm": ALGORITHM, "threshold": THRESHOLD, "limit": LIMIT},
            json={"queries": {"q": {"schema": query["schema"], "properties": query["properties"]}}},
            timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        candidates = sorted(resp.json()["responses"]["q"]["results"], key=lambda c: c["score"], reverse=True)
    except (requests.RequestException, KeyError, TypeError) as err:
        saved = audit.cache_get(qhash)
        if saved is None:
            raise YenteUnavailable(_why(err)) from err
        return {**saved, "live": False, "query_hash": qhash}
    fetched_at = audit.now()
    audit.cache_put(qhash, candidates, fetched_at)
    return {"candidates": candidates, "live": True, "fetched_at": fetched_at, "query_hash": qhash}


def _why(err: Exception) -> str:
    if isinstance(err, requests.HTTPError) and err.response is not None:
        return f"OpenSanctions answered HTTP {err.response.status_code}"
    if isinstance(err, (ValueError, KeyError, TypeError)):
        return "unexpected answer from OpenSanctions"
    return f"no connection to OpenSanctions ({type(err).__name__})"


def entity(entity_id: str) -> dict | None:
    """The full record, with nested sanction entries and linked entities. None on failure."""
    try:
        resp = http.request("GET", f"{BASE_URL}/entities/{quote(entity_id, safe='')}",
                            headers=_headers(), timeout=TIMEOUT_S)
        resp.raise_for_status()
        record = resp.json()
    except requests.RequestException:
        return None
    return record if isinstance(record, dict) else None


def catalog_as_of() -> str | None:
    """When the sanctions collection was last updated, from GET /catalog. None if unknown."""
    try:
        resp = http.request("GET", f"{BASE_URL}/catalog", headers=_headers(), timeout=TIMEOUT_S)
        resp.raise_for_status()
        datasets = resp.json()["datasets"]
    except (requests.RequestException, KeyError, TypeError):
        return None
    for dataset in datasets:
        if isinstance(dataset, dict) and dataset.get("name") == COLLECTION:
            # yente's dataset model has all three fields; the live check confirms which is filled
            return dataset.get("updated_at") or dataset.get("last_export") or dataset.get("version")
    return None
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_layer1_yente.py -v`
Expected: 7 passed.

Run: `python -m pytest`
Expected: 47 passed.

- [ ] **Step 5: Commit**

```bash
git add tradecheck/layer1_yente.py tests/test_layer1_yente.py
git commit -m "feat: add the OpenSanctions matcher with saved-response fallback" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Layer 2: score bands

**Files:**
- Create: `tradecheck/layer2.py`
- Test: `tests/test_layer2.py`

**Interfaces:**
- Consumes: the `candidates` list of `layer1_yente.match` (Task 5) and the `status` and `reason` keys of `layer1_un.check` (Task 7; the tests use literal dicts).
- Produces:
  - `layer2.band(match: dict | None, un: dict, fallback: bool = False) -> dict` returning `{"band": "clear" | "review" | "hit" | "unknown", "score": float | None, "cutoffs": {"clear_below": 0.7, "hit_at": 0.9}, "reasons": [str]}`.
  - `layer2.CLEAR_BELOW = 0.70`, `layer2.HIT_AT = 0.90`, and the tables `layer2.LABEL` and `layer2.STATUS` keyed by band.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layer2.py`:

```python
"""Layer 2's band table. Pure functions, no fakes needed."""

import pytest

from tradecheck import layer2

AGREE = {"status": "agree", "best": None, "reason": "the UN check agrees with the matcher"}
DISAGREE = {"status": "disagree", "best": None, "reason": 'UN match "X" scored 1.00 but the matcher\'s top score is 0.00'}
UNAVAILABLE = {"status": "unavailable", "best": None, "reason": "UN list not loaded"}


def _match(*scores):
    return {"candidates": [{"id": f"NK-{i}", "score": s} for i, s in enumerate(scores)], "live": True}


@pytest.mark.parametrize("scores, expected", [
    ((0.95,), "hit"), ((0.90,), "hit"), ((0.89,), "review"), ((0.70,), "review"),
    ((0.69,), "clear"), ((), "clear"),
])
def test_band_table(scores, expected):
    assert layer2.band(_match(*scores), AGREE)["band"] == expected


def test_unknown_when_the_match_is_unavailable():
    result = layer2.band(None, AGREE)
    assert (result["band"], result["score"]) == ("unknown", None)


@pytest.mark.parametrize("un", [DISAGREE, UNAVAILABLE])
def test_a_un_doubt_raises_clear_to_review(un):
    result = layer2.band(_match(0.40), un)
    assert result["band"] == "review"
    assert result["reasons"][-1] == f"UN check {un['status']}: {un['reason']}"


def test_a_un_doubt_never_lowers_a_hit():
    assert layer2.band(_match(0.95), DISAGREE)["band"] == "hit"
    assert layer2.band(_match(0.80), DISAGREE)["band"] == "review"


def test_a_fallback_extraction_is_never_clear():
    result = layer2.band(_match(0.30), AGREE, fallback=True)
    assert result["band"] == "review"
    assert result["reasons"][-1] == "the fields could not be extracted, so the whole question was screened as a name"


def test_reasons_and_cutoffs():
    result = layer2.band(_match(0.83), AGREE)
    assert result["reasons"] == ["score 0.83 between 0.70 and 0.90"]
    assert result["cutoffs"] == {"clear_below": 0.70, "hit_at": 0.90}


def test_labels_and_statuses():
    assert layer2.LABEL == {"clear": "Clear*", "review": "Caution", "hit": "Avoid", "unknown": "Unknown"}
    assert layer2.STATUS == {"clear": "cleared", "review": "pending_review", "hit": "flagged", "unknown": "unknown"}
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_layer2.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'layer2' from 'tradecheck'`.

- [ ] **Step 3: Write the module**

Create `tradecheck/layer2.py`:

```python
"""Layer 2: fixed rules turn the matcher's top score and the UN check into a band.

No model here. The matcher decides; the model only explains (Layer 3).
"""

from __future__ import annotations

CLEAR_BELOW = 0.70  # placeholder cutoffs until a labelled test set exists
HIT_AT = 0.90
LABEL = {"clear": "Clear*", "review": "Caution", "hit": "Avoid", "unknown": "Unknown"}
STATUS = {"clear": "cleared", "review": "pending_review", "hit": "flagged", "unknown": "unknown"}


def band(match: dict | None, un: dict, fallback: bool = False) -> dict:
    """Return {"band", "score", "cutoffs", "reasons"}. ``match`` is None when yente was
    unavailable. ``fallback`` is True when the model could not extract the fields."""
    cutoffs = {"clear_below": CLEAR_BELOW, "hit_at": HIT_AT}
    if match is None:
        return {"band": "unknown", "score": None, "cutoffs": cutoffs,
                "reasons": ["the live match check failed and no saved result exists"]}
    score = match["candidates"][0]["score"] if match["candidates"] else 0.0
    if score >= HIT_AT:
        name, reasons = "hit", [f"score {score:.2f} at or above {HIT_AT:.2f}"]
    elif score >= CLEAR_BELOW:
        name, reasons = "review", [f"score {score:.2f} between {CLEAR_BELOW:.2f} and {HIT_AT:.2f}"]
    else:
        name, reasons = "clear", [f"score {score:.2f} below {CLEAR_BELOW:.2f}"]
    if un["status"] != "agree":
        reasons.append(f"UN check {un['status']}: {un['reason']}")
    if fallback:
        reasons.append("the fields could not be extracted, so the whole question was screened as a name")
    # A doubt only ever raises clear to review. It never lowers a hit.
    if name == "clear" and (un["status"] != "agree" or fallback):
        name = "review"
    return {"band": name, "score": score, "cutoffs": cutoffs, "reasons": reasons}
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_layer2.py -v`
Expected: 13 passed.

Run: `python -m pytest`
Expected: 60 passed.

- [ ] **Step 5: Commit**

```bash
git add tradecheck/layer2.py tests/test_layer2.py
git commit -m "feat: add Layer 2 score bands" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Layer 1b: the UN cross-check

**Files:**
- Create: `tradecheck/layer1_un.py`, `tests/fixtures/un_sample.xml`
- Test: `tests/test_layer1_un.py`

**Interfaces:**
- Consumes: `ingest.fetch.RAW_DIR` and `ingest.fetch.SOURCES["un_sc"]`, `ingest.normalize.normalize_name` (Task 1); `layer2.CLEAR_BELOW` (Task 6); the `name` key of the parsed query (Task 4); the `candidates` of a match (Task 5), or `None`.
- Produces:
  - `layer1_un.load(path=layer1_un.DEFAULT_PATH) -> int` (records loaded; 0 when the file is missing or broken).
  - `layer1_un.check(query: dict, match: dict | None) -> dict` returning `{"status": "agree" | "disagree" | "unavailable", "best": {"ref", "name", "matched_name", "quality", "score", "listed_on", "list_type"} | None, "reason": str, "list_date": str | None}`.
  - `layer1_un.LIST_URL` (the UN download URL) and `layer1_un.DEFAULT_PATH` (`data/raw/un_sc.xml`).

- [ ] **Step 1: Write the fixture**

Create `tests/fixtures/un_sample.xml`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<!-- Trimmed sample in the UN Security Council consolidated-list format, for offline tests.
     KHAWA PANGA MANDRO and his low-quality alias "Chief Kahwa" are on the public UN list
     (the README's Oct 3 sample); the reference number and date here are placeholders.
     TESTCO GLOBAL TRADING is synthetic. It covers the ENTITY path and every alias quality. -->
<CONSOLIDATED_LIST dateGenerated="2026-10-07T23:00:03.504Z">
  <INDIVIDUALS>
    <INDIVIDUAL>
      <DATAID>1</DATAID>
      <FIRST_NAME>KHAWA</FIRST_NAME>
      <SECOND_NAME>PANGA</SECOND_NAME>
      <THIRD_NAME>MANDRO</THIRD_NAME>
      <UN_LIST_TYPE>DRC</UN_LIST_TYPE>
      <REFERENCE_NUMBER>CDi.000</REFERENCE_NUMBER>
      <LISTED_ON>2005-11-01</LISTED_ON>
      <INDIVIDUAL_ALIAS>
        <QUALITY>Low</QUALITY>
        <ALIAS_NAME>Chief Kahwa</ALIAS_NAME>
      </INDIVIDUAL_ALIAS>
    </INDIVIDUAL>
  </INDIVIDUALS>
  <ENTITIES>
    <ENTITY>
      <DATAID>2</DATAID>
      <FIRST_NAME>TESTCO GLOBAL TRADING</FIRST_NAME>
      <UN_LIST_TYPE>DPRK</UN_LIST_TYPE>
      <REFERENCE_NUMBER>KPe.000</REFERENCE_NUMBER>
      <LISTED_ON>2016-03-02</LISTED_ON>
      <ENTITY_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>BLUE HARBOUR LINES</ALIAS_NAME></ENTITY_ALIAS>
      <ENTITY_ALIAS><QUALITY>Low</QUALITY><ALIAS_NAME>ORCHID CARGO</ALIAS_NAME></ENTITY_ALIAS>
      <ENTITY_ALIAS><QUALITY/><ALIAS_NAME>TESTCO SHIPPING</ALIAS_NAME></ENTITY_ALIAS>
      <ENTITY_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME/></ENTITY_ALIAS>
    </ENTITY>
  </ENTITIES>
</CONSOLIDATED_LIST>
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_layer1_un.py`:

```python
"""Layer 1b on a small UN XML fixture."""

from pathlib import Path

import pytest

from tradecheck import layer1_un

FIXTURE = Path(__file__).parent / "fixtures" / "un_sample.xml"
NO_CANDIDATES = {"candidates": []}


@pytest.fixture(autouse=True)
def loaded():
    assert layer1_un.load(FIXTURE) == 2


def _q(name):
    return {"name": name}


def _match(score, datasets=("un_sc_sanctions",)):
    return {"candidates": [{"id": "NK-1", "caption": "Listed Party", "score": score, "datasets": list(datasets)}]}


@pytest.mark.parametrize("name, quality, score", [
    ("KHAWA PANGA MANDRO", "primary", 1.0),
    ("Blue Harbour Lines", "Good", 0.85),
    ("Testco Shipping", "unrated", 0.85),
    ("Orchid Cargo", "Low", 0.6),
    ("Chief Kahwa", "Low", 0.6),
])
def test_alias_weights(name, quality, score):
    best = layer1_un.check(_q(name), _match(0.95))["best"]
    assert (best["quality"], best["score"]) == (quality, score)


def test_best_carries_the_listing():
    assert layer1_un.check(_q("Chief Kahwa"), _match(0.80))["best"] == {
        "ref": "CDi.000", "name": "KHAWA PANGA MANDRO", "matched_name": "Chief Kahwa", "quality": "Low",
        "score": 0.6, "listed_on": "2005-11-01", "list_type": "DRC"}


def test_agree_when_both_find_the_listing():
    un = layer1_un.check(_q("KHAWA PANGA MANDRO"), _match(0.97))
    assert (un["status"], un["list_date"]) == ("agree", "2026-10-07T23:00:03.504Z")


def test_a_low_alias_match_is_not_a_disagreement():
    # A Low alias tops out at 0.60, but the UN list does contain the name.
    assert layer1_un.check(_q("Chief Kahwa"), _match(0.80))["status"] == "agree"


@pytest.mark.parametrize("match", [NO_CANDIDATES, None, _match(0.40)])
def test_disagree_a_strong_un_match_the_matcher_missed(match):
    un = layer1_un.check(_q("KHAWA PANGA MANDRO"), match)
    assert un["status"] == "disagree"
    assert un["reason"].startswith('UN match "KHAWA PANGA MANDRO" scored 1.00')


def test_disagree_b_the_matcher_says_un_listed_but_the_un_file_has_no_such_name():
    un = layer1_un.check(_q("Northwind Metals"), _match(0.92))
    assert (un["status"], un["best"]) == ("disagree", None)


def test_agree_when_neither_finds_anything():
    un = layer1_un.check(_q("AgroDistribuidora del Bajío SA de CV"), NO_CANDIDATES)
    assert (un["status"], un["best"]) == ("agree", None)


def test_a_name_without_latin_letters_is_unavailable():
    un = layer1_un.check(_q("Хава Панга Мандро"), NO_CANDIDATES)
    assert (un["status"], un["reason"]) == ("unavailable", "the name has no Latin letters to compare")


@pytest.mark.parametrize("content", [None, b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVID"])
def test_a_missing_or_truncated_file_makes_every_check_unavailable(tmp_path, content):
    path = tmp_path / "un_sc.xml"
    if content is not None:
        path.write_bytes(content)
    assert layer1_un.load(path) == 0
    un = layer1_un.check(_q("KHAWA PANGA MANDRO"), NO_CANDIDATES)
    assert (un["status"], un["reason"]) == ("unavailable", "UN list not loaded")
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python -m pytest tests/test_layer1_un.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'layer1_un' from 'tradecheck'`.

- [ ] **Step 4: Write the module**

Create `tradecheck/layer1_un.py`:

```python
"""Layer 1b: cross-check the query against our own copy of the UN Security Council list.

yente already includes the UN list. This independent check catches the cases where
the two disagree (a missed listing, a delisting, data lag) and raises them for review.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree
from rapidfuzz import fuzz, process

from ingest.fetch import RAW_DIR, SOURCES
from ingest.normalize import normalize_name
from tradecheck.layer2 import CLEAR_BELOW

LIST_URL, _FILENAME = SOURCES["un_sc"]
DEFAULT_PATH = RAW_DIR / _FILENAME
WEIGHTS = {"primary": 1.0, "Good": 0.85, "Low": 0.60}  # the README's Oct 3 test on the UN sample
UNRATED = 0.85       # an alias with an empty QUALITY
STRONG_AT = 0.90     # only a primary name can reach this
MIN_SIMILARITY = 70  # WRatio a UN name needs before it counts as found

_names: list[dict] = []   # one entry per primary name or alias
_choices: list[str] = []  # the normalized names, in the same order
_list_date: str | None = None


def load(path: Path | str = DEFAULT_PATH) -> int:
    """Index every primary name and alias in the UN XML. Returns the number of records.
    A missing or broken file loads nothing, and every check is then unavailable."""
    global _list_date
    _names.clear()
    _choices.clear()
    _list_date = None
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.parse(str(path), parser).getroot()
    except (OSError, etree.XMLSyntaxError):
        return 0
    _list_date = root.get("dateGenerated")
    records = 0
    for rec in root.iter("INDIVIDUAL", "ENTITY"):
        records += 1
        name = " ".join(filter(None, (_text(rec, tag) for tag in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME"))))
        base = {"ref": _text(rec, "REFERENCE_NUMBER"), "name": name,
                "listed_on": _text(rec, "LISTED_ON"), "list_type": _text(rec, "UN_LIST_TYPE")}
        _add(base, name, "primary")
        for alias in rec.iter("INDIVIDUAL_ALIAS", "ENTITY_ALIAS"):
            _add(base, _text(alias, "ALIAS_NAME"), _text(alias, "QUALITY") or "unrated")
    return records


def _text(element, tag: str) -> str:
    return (element.findtext(tag) or "").strip()


def _add(base: dict, name: str, quality: str) -> None:
    normalized = normalize_name(name)
    if normalized:
        _names.append({**base, "matched_name": name, "quality": quality, "weight": WEIGHTS.get(quality, UNRATED)})
        _choices.append(normalized)


def check(query: dict, match: dict | None) -> dict:
    """Return {"status": "agree" | "disagree" | "unavailable", "best", "reason", "list_date"}.
    ``match`` is None when yente was unavailable; its top score then counts as 0."""
    wanted = normalize_name(query["name"])
    if not _names or not wanted:
        reason = "UN list not loaded" if not _names else "the name has no Latin letters to compare"
        return {"status": "unavailable", "best": None, "reason": reason, "list_date": _list_date}
    best = None
    for _, similarity, i in process.extract(wanted, _choices, scorer=fuzz.WRatio,
                                            score_cutoff=MIN_SIMILARITY, limit=None):
        score = round(similarity / 100 * _names[i]["weight"], 3)
        if best is None or score > best["score"]:
            n = _names[i]
            best = {"ref": n["ref"], "name": n["name"], "matched_name": n["matched_name"], "quality": n["quality"],
                    "score": score, "listed_on": n["listed_on"], "list_type": n["list_type"]}
    top = match["candidates"][0] if match and match["candidates"] else None
    top_score = top["score"] if top else 0.0
    if best and best["score"] >= STRONG_AT and top_score < CLEAR_BELOW:
        status = "disagree"
        reason = f'UN match "{best["matched_name"]}" scored {best["score"]:.2f} but the matcher\'s top score is {top_score:.2f}'
    elif top and top_score >= CLEAR_BELOW and "un_sc_sanctions" in top.get("datasets", []) and best is None:
        status = "disagree"
        reason = (f'the matcher lists "{top.get("caption")}" on the UN list, '
                  f"but no UN name is at least {MIN_SIMILARITY}% similar")
    else:
        status, reason = "agree", "the UN check agrees with the matcher"
    return {"status": status, "best": best, "reason": reason, "list_date": _list_date}
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `python -m pytest tests/test_layer1_un.py -v`
Expected: 16 passed.

Run: `python -m pytest`
Expected: 76 passed.

- [ ] **Step 6: Commit**

```bash
git add tradecheck/layer1_un.py tests/fixtures/un_sample.xml tests/test_layer1_un.py
git commit -m "feat: add the UN cross-check" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Layer 3: the cited report

**Files:**
- Create: `tradecheck/layer3.py`
- Test: `tests/test_layer3.py`

**Interfaces:**
- Consumes: `http.request`, `http.chat`, `http.ModelError`, `http.MODEL`, `http.SEED`, `http.CHAT_TIMEOUT_S` (Task 2); `layer1_yente.entity` (Task 5); `layer1_un.LIST_URL` and the `best` dict of a UN check (Task 7); the band dict from `layer2.band` (Task 6).
- Produces:
  - `layer3.report(query: dict, match: dict, un: dict, band: dict) -> dict` for review and hit cases with at least one candidate. It returns `{"summary": claim, "sections": {section: [claim]}, "sources": [{"key", "kind", "label", "url"}], "next_step": str, "check": "pass" | "template", "check_reason": str | None, "attempts": int, "model", "prompt_version", "seed", "steps": {"entity": "ok" | "unavailable", "news": "ok" | "unavailable"}}`, where a claim is `{"text": str, "cites": [str]}`.
  - `layer3.build_bundle(top, record, news, un) -> dict`, `layer3.check_citations(out, bundle) -> list[str]`, `layer3.template(query, bundle, top_id) -> dict`.
  - Constants `layer3.SECTIONS` (`who_matched`, `why_it_matched`, `differences`, `sanctions`, `news`), `layer3.NEXT_STEP` (keyed by band), `PROMPT_VERSION`, `MAX_RETRIES`, `DEADLINE_S`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_layer3.py`:

```python
"""Layer 3: the evidence bundle, the citation check, retries, the deadline and the
template. Every outside service is faked."""

import pytest

from tests.fakes import FakeResponse, chat_reply
from tradecheck import layer3

TOP = {"id": "NK-kpm", "caption": "Khawa Panga Mandro", "schema": "Person", "score": 0.95,
       "datasets": ["un_sc_sanctions", "us_ofac_sdn"],
       "explanations": {"name_literal_match": {"score": 1.0, "detail": "exact name"},
                        "country_mismatch": {"score": 1.0, "detail": None},
                        "dob_year_disjoint": {"score": 0.0, "detail": None}},
       "properties": {"name": ["Khawa Panga Mandro"], "birthDate": ["1974-08-20"], "country": ["cd"]}}
SANCTION = {"id": "ofac-s-1", "schema": "Sanction", "caption": "DRCONGO",
            "properties": {"program": ["DRCONGO"], "authority": ["Office of Foreign Assets Control"],
                           "startDate": ["2005-11-01"], "sourceUrl": ["https://sanctionssearch.ofac.treas.gov/"]}}
RECORD = {**TOP, "properties": {**TOP["properties"], "alias": ["Chief Kahwa"], "sanctions": [SANCTION]}}
NEWS = [{"title": "Militia leader named in report", "url": "https://news.example/a",
         "content": "...", "published_date": "2026-09-01"}]
UN = {"status": "agree", "reason": "the UN check agrees with the matcher", "list_date": None,
      "best": {"ref": "CDi.000", "name": "KHAWA PANGA MANDRO", "matched_name": "KHAWA PANGA MANDRO",
               "quality": "primary", "score": 1.0, "listed_on": "2005-11-01", "list_type": "DRC"}}
QUERY = {"schema": "Person", "name": "Khawa Panga Mandro", "country": "ug",
         "properties": {"name": ["Khawa Panga Mandro"], "country": ["ug"]}}
MATCH = {"candidates": [TOP], "live": True}
HIT = {"band": "hit", "score": 0.95}

GOOD = {"summary": {"text": "Khawa Panga Mandro matched with score 0.95.", "cites": ["NK-kpm"]},
        "who_matched": [{"text": "A person listed by OFAC and the UN.", "cites": ["NK-kpm", "UN:CDi.000"]}],
        "why_it_matched": [{"text": "The name is identical.", "cites": ["NK-kpm"]}],
        "differences": [{"text": "You gave Uganda; the listing shows DR Congo.", "cites": ["NK-kpm"]}],
        "sanctions": [{"text": "OFAC DRCONGO program since 2005-11-01.", "cites": ["ofac-s-1"]}],
        "news": [{"text": "Named in a September 2026 report.", "cites": ["https://news.example/a"]}]}
BAD = {**GOOD, "sanctions": [{"text": "An invented program.", "cites": ["made-up-key"]}]}


@pytest.fixture
def services(fake_http):
    """yente's entity lookup and Tavily answer. Tests set the model's replies with _model."""
    routes, _ = fake_http
    routes["/entities/"] = FakeResponse(200, RECORD)
    routes["api.tavily.com"] = FakeResponse(200, {"results": NEWS})
    return routes


def _model(routes, *replies):
    """The model gives each reply in turn (the last one repeats). Returns the requests sent."""
    replies, sent = list(replies), []

    def answer(kwargs):
        sent.append(kwargs)
        return replies.pop(0) if len(replies) > 1 else replies[0]

    routes["openrouter.ai"] = answer
    return sent


def _clock(monkeypatch, *ticks):
    """time.monotonic returns each tick in turn (the last one repeats)."""
    ticks = list(ticks)
    monkeypatch.setattr(layer3.time, "monotonic", lambda: ticks.pop(0) if len(ticks) > 1 else ticks[0])


def _bundle():
    return layer3.build_bundle(TOP, RECORD, NEWS, UN)


def test_bundle_has_one_key_per_piece_of_evidence():
    bundle = _bundle()
    assert list(bundle) == ["NK-kpm", "ofac-s-1", "https://news.example/a", "UN:CDi.000"]
    assert bundle["ofac-s-1"]["url"] == "https://sanctionssearch.ofac.treas.gov/"
    assert "sanctions" not in bundle["NK-kpm"]["properties"]


def test_only_http_links_reach_the_bundle():
    news = [{"title": "x", "url": "javascript:alert(1)"}, {"title": "y", "url": "https://ok.example/y"}]
    record = {**RECORD, "properties": {"sanctions": [{"id": "s-2", "properties": {"sourceUrl": ["javascript:alert(1)"]}}]}}
    bundle = layer3.build_bundle(TOP, record, news, {"best": None})
    assert list(bundle) == ["NK-kpm", "s-2", "https://ok.example/y"]
    assert bundle["s-2"]["url"] == "https://www.opensanctions.org/entities/NK-kpm/"


def test_citation_check_passes_a_good_report():
    assert layer3.check_citations(GOOD, _bundle()) == []


@pytest.mark.parametrize("report, failure", [
    (BAD, 'claim 1 in "sanctions" cites unknown key made-up-key'),
    ({**GOOD, "summary": {"text": "No citations.", "cites": []}}, 'claim 1 in "summary" cites nothing'),
    ({**GOOD, "who_matched": [{"text": " ", "cites": ["NK-kpm"]}]}, 'claim 1 in "who_matched" has no text'),
    ({k: v for k, v in GOOD.items() if k != "news"}, 'section "news" is missing'),
    (["not", "an", "object"], "the report is not a JSON object"),
])
def test_citation_check_failures(report, failure):
    assert failure in layer3.check_citations(report, _bundle())


def test_a_good_report_on_the_first_attempt(services):
    sent = _model(services, chat_reply(GOOD))
    rep = layer3.report(QUERY, MATCH, UN, HIT)
    assert (rep["check"], rep["check_reason"], rep["attempts"], len(sent)) == ("pass", None, 1, 1)
    assert rep["summary"] == GOOD["summary"]
    assert rep["sections"]["sanctions"] == GOOD["sanctions"]
    assert rep["next_step"] == "Hold and escalate. Don't proceed until reviewed."
    assert [s["key"] for s in rep["sources"]] == ["NK-kpm", "ofac-s-1", "https://news.example/a", "UN:CDi.000"]
    assert (rep["model"], rep["prompt_version"], rep["seed"]) == ("qwen/qwen3.8-27b", "report-v1", 42)
    assert rep["steps"] == {"entity": "ok", "news": "ok"}


def test_a_retry_sends_the_failures_back(services):
    sent = _model(services, chat_reply(BAD), chat_reply(GOOD))
    rep = layer3.report(QUERY, MATCH, UN, HIT)
    assert (rep["check"], rep["attempts"]) == ("pass", 2)
    assert 'claim 1 in "sanctions" cites unknown key made-up-key' in sent[1]["json"]["messages"][-1]["content"]


def test_three_retries_then_the_template(services):
    sent = _model(services, chat_reply(BAD))
    rep = layer3.report(QUERY, MATCH, UN, HIT)
    assert len(sent) == 4
    assert (rep["check"], rep["attempts"]) == ("template", 4)
    assert rep["check_reason"] == 'citation check failed: claim 1 in "sanctions" cites unknown key made-up-key'


def test_invalid_json_counts_as_a_failed_attempt(services):
    _model(services, chat_reply("{not json"), chat_reply(GOOD))
    assert layer3.report(QUERY, MATCH, UN, HIT)["attempts"] == 2


def test_a_model_error_goes_straight_to_the_template(services):
    sent = _model(services, FakeResponse(401, {"error": {"message": "No auth credentials found"}}))
    rep = layer3.report(QUERY, MATCH, UN, HIT)
    assert (rep["check"], len(sent)) == ("template", 1)
    assert rep["check_reason"] == "model unavailable: HTTP 401: No auth credentials found"


def test_the_deadline_stops_new_attempts(services, monkeypatch):
    sent = _model(services, chat_reply(BAD))
    _clock(monkeypatch, 0, 0, 61)  # report starts, attempt 1 starts, attempt 2 would start
    rep = layer3.report(QUERY, MATCH, UN, HIT)
    assert len(sent) == 1
    assert rep["check_reason"].startswith("deadline passed (claim 1")


def test_each_model_call_gets_only_the_time_left(services, monkeypatch):
    sent = _model(services, chat_reply(GOOD))
    _clock(monkeypatch, 0, 50)
    layer3.report(QUERY, MATCH, UN, HIT)
    assert sent[0]["timeout"] == 10


def test_tavily_failure_keeps_the_report_going(services):
    services["api.tavily.com"] = FakeResponse(432)
    _model(services, chat_reply({**GOOD, "news": []}))
    rep = layer3.report(QUERY, MATCH, UN, HIT)
    assert rep["steps"]["news"] == "unavailable"
    assert rep["check"] == "pass"


def test_entity_failure_falls_back_to_the_match_properties(services):
    services["/entities/"] = FakeResponse(500)
    _model(services, chat_reply(BAD))
    rep = layer3.report(QUERY, MATCH, UN, {"band": "review", "score": 0.8})
    assert rep["steps"]["entity"] == "unavailable"
    assert {"text": "Born: 1974-08-20.", "cites": ["NK-kpm"]} in rep["sections"]["who_matched"]
    assert rep["next_step"] == "An analyst should compare the details and confirm or dismiss this match."


@pytest.mark.parametrize("record, news", [(RECORD, NEWS), (None, [])])
def test_the_template_always_passes_its_own_check(record, news):
    bundle = layer3.build_bundle(TOP, record, news, UN)
    assert layer3.check_citations(layer3.template(QUERY, bundle, "NK-kpm"), bundle) == []


def test_template_content():
    t = layer3.template(QUERY, _bundle(), "NK-kpm")
    assert [c["text"] for c in t["why_it_matched"]] == ["Matcher feature name_literal_match scored 1.00: exact name."]
    assert [c["text"] for c in t["differences"]] == [
        "Matcher penalty country_mismatch scored 1.00.",
        "You gave country ug; the listing shows cd.",
    ]
    assert t["sanctions"][0] == {"text": "DRCONGO by Office of Foreign Assets Control since 2005-11-01.",
                                 "cites": ["ofac-s-1"]}
    assert t["news"] == [{"text": "Militia leader named in report (2026-09-01).", "cites": ["https://news.example/a"]}]


def test_template_with_only_a_name():
    query = {**QUERY, "properties": {"name": ["Khawa Panga Mandro"]}}
    top = {**TOP, "explanations": {}}
    t = layer3.template(query, layer3.build_bundle(top, None, [], {"best": None}), "NK-kpm")
    assert [c["text"] for c in t["differences"]] == ["Only a name was given, so no other details could be compared."]
    assert [c["text"] for c in t["sanctions"]] == ["Listed in: un_sc_sanctions, us_ofac_sdn."]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_layer3.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'layer3' from 'tradecheck'`.

- [ ] **Step 3: Write the module**

Create `tradecheck/layer3.py`:

```python
"""Layer 3, for review and hit cases only: gather the evidence, have the model write
the report, and check every citation in code.

The model never changes the band. If its report still fails the citation check
after MAX_RETRIES retries, or the deadline passes, code writes the report from the
evidence instead (the template), and that report always passes the check.
"""

from __future__ import annotations

import json
import os
import time

import requests

from tradecheck import http, layer1_un, layer1_yente

PROMPT_VERSION = "report-v1"
MAX_RETRIES = 3   # after the first attempt, so at most 4 model calls
DEADLINE_S = 60   # no new attempt starts after this
TAVILY_URL = "https://api.tavily.com/search"
TAVILY_TIMEOUT_S = 15
ENTITY_URL = "https://www.opensanctions.org/entities/{}/"
SECTIONS = ("who_matched", "why_it_matched", "differences", "sanctions", "news")
NEXT_STEP = {
    "review": "An analyst should compare the details and confirm or dismiss this match.",
    "hit": "Hold and escalate. Don't proceed until reviewed.",
}

SYSTEM = """You write the evidence report for one sanctions screening result.
The matcher has already decided the band. You explain the evidence; you never judge it.
Rules:
- Use only the evidence bundle. No outside knowledge.
- Every claim cites at least one bundle key in "cites", copied exactly.
- State facts and differences only. No verdicts, no recommendations, no legal advice.
Sections:
- summary: one or two sentences on who matched and how strongly.
- who_matched: who the listed party is (names, aliases, birth date, country).
- why_it_matched: which details of the query agree with the listing.
- differences: details of the query that differ from the listing or are missing from it.
- sanctions: programs, authorities and dates.
- news: one claim per relevant article, citing its URL.
Leave a section empty when the bundle has nothing for it."""

_CLAIM = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "cites": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "cites"],
    "additionalProperties": False,
}
REPORT_SCHEMA = {
    "type": "object",
    "properties": {"summary": _CLAIM, **{s: {"type": "array", "items": _CLAIM} for s in SECTIONS}},
    "required": ["summary", *SECTIONS],
    "additionalProperties": False,
}

# ponytail: penalty features are recognised by name; read coefficients from GET /algorithms if names drift
_PENALTY_WORDS = ("mismatch", "disjoint")
# A query property and the listing properties it is compared with.
_COMPARE = {"country": ("country", "nationality", "citizenship"), "birthDate": ("birthDate",),
            "jurisdiction": ("jurisdiction", "country"), "registrationNumber": ("registrationNumber",)}
_LABELS = {"country": "country", "birthDate": "birth date", "jurisdiction": "jurisdiction",
           "registrationNumber": "registration number"}


def report(query: dict, match: dict, un: dict, band: dict) -> dict:
    """The report for a review or hit case (the spec, section 5.5)."""
    start = time.monotonic()
    top = match["candidates"][0]
    record = layer1_yente.entity(top["id"])
    news, news_status = _news(top.get("caption", ""), query.get("country"))
    bundle = build_bundle(top, record, news, un)
    out, attempts, reason = _ask_model(query, band, bundle, news_status, start)
    if out is None:
        out = template(query, bundle, top["id"])
    claims = [out["summary"], *(claim for section in SECTIONS for claim in out[section])]
    cited = {key for claim in claims for key in claim["cites"]}
    return {
        "summary": out["summary"],
        "sections": {section: out[section] for section in SECTIONS},
        "sources": [{"key": key, "kind": item["kind"], "label": item["label"], "url": item["url"]}
                    for key, item in bundle.items() if key in cited],
        "next_step": NEXT_STEP[band["band"]],
        "check": "pass" if reason is None else "template",
        "check_reason": reason,
        "attempts": attempts,
        "model": http.MODEL,
        "prompt_version": PROMPT_VERSION,
        "seed": http.SEED,
        "steps": {"entity": "ok" if record else "unavailable", "news": news_status},
    }


def _news(caption: str, country: str | None) -> tuple[list[dict], str]:
    """Tavily news search. Returns (results, "ok" | "unavailable")."""
    query = " ".join(filter(None, (f'"{caption}"', country, "sanctions OR fraud OR investigation")))
    try:
        resp = http.request(
            "POST", TAVILY_URL,
            headers={"Authorization": f"Bearer {os.environ.get('TAVILY_API_KEY', '')}"},
            json={"query": query, "topic": "news", "search_depth": "basic", "max_results": 5},
            timeout=TAVILY_TIMEOUT_S,
        )
        resp.raise_for_status()
        results = resp.json()["results"]
    except (requests.RequestException, KeyError, TypeError):
        return [], "unavailable"
    return [r for r in results if isinstance(r, dict)], "ok"


def build_bundle(top: dict, record: dict | None, news: list[dict], un: dict) -> dict:
    """Every fact the report may use, keyed by the citation key that must cite it."""
    props = (record or top).get("properties") or {}
    bundle = {top["id"]: {
        "kind": "candidate",
        "label": top.get("caption") or top["id"],
        "url": ENTITY_URL.format(top["id"]),
        "caption": top.get("caption"),
        "schema": top.get("schema"),
        "score": top.get("score"),
        "datasets": top.get("datasets", []),
        "explanations": top.get("explanations", {}),
        "first_seen": top.get("first_seen"),
        "last_seen": top.get("last_seen"),
        "last_change": top.get("last_change"),
        "properties": {key: _texts(values) for key, values in props.items() if key != "sanctions"},
    }}
    for entry in props.get("sanctions") or []:
        if isinstance(entry, dict) and entry.get("id"):
            p = entry.get("properties") or {}
            bundle[entry["id"]] = {
                "kind": "sanction",
                "label": ", ".join(_texts(p.get("program"))) or entry.get("caption") or "Sanction",
                "url": _safe_url(next(iter(_texts(p.get("sourceUrl"))), None)) or ENTITY_URL.format(top["id"]),
                "properties": {key: _texts(values) for key, values in p.items() if key != "entity"},
            }
    for article in news:
        url = _safe_url(article.get("url"))
        if url:
            bundle[url] = {"kind": "news", "label": article.get("title") or url, "url": url,
                           "title": article.get("title"), "content": article.get("content"),
                           "published_date": article.get("published_date")}
    best = un.get("best")
    if best:
        bundle[f"UN:{best['ref']}"] = {"kind": "un", "label": f"UN list entry {best['ref']}",
                                       "url": layer1_un.LIST_URL, **best}
    return bundle


def _texts(values) -> list[str]:
    """A property's values as text. A nested entity becomes its caption."""
    if not isinstance(values, list):
        return []
    return [str(v.get("caption") or v.get("id", "")) if isinstance(v, dict) else str(v) for v in values]


def _safe_url(url) -> str | None:
    """Only http(s) links reach the page. javascript:, data: and the rest are dropped."""
    return url if isinstance(url, str) and url.startswith(("https://", "http://")) else None


def _ask_model(query: dict, band: dict, bundle: dict, news_status: str,
               start: float) -> tuple[dict | None, int, str | None]:
    """Model call 2, retried with the list of failures.
    Returns (report or None, attempts, why it failed or None)."""
    failures: list[str] = []
    attempts = 0
    while attempts <= MAX_RETRIES:
        left = DEADLINE_S - (time.monotonic() - start)
        if left <= 0:
            return None, attempts, "deadline passed" + (f" ({'; '.join(failures)})" if failures else "")
        attempts += 1
        try:
            text = http.chat(_messages(query, band, bundle, news_status, failures), "report", REPORT_SCHEMA,
                             timeout=min(http.CHAT_TIMEOUT_S, left))
        except http.ModelError as err:
            return None, attempts, f"model unavailable: {err}"
        try:
            out = json.loads(text)
        except ValueError:
            failures = ["the reply was not valid JSON"]
            continue
        failures = check_citations(out, bundle)
        if not failures:
            return out, attempts, None
    return None, attempts, "citation check failed: " + "; ".join(failures)


def _messages(query: dict, band: dict, bundle: dict, news_status: str, failures: list[str]) -> list[dict]:
    evidence = {
        "band": band["band"],
        "score": band["score"],
        "query": {"schema": query["schema"], "properties": query["properties"]},
        "news_search": news_status,
        "bundle": bundle,
    }
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}]
    if failures:
        messages.append({"role": "user", "content": "Your last report failed the citation check:\n- "
                         + "\n- ".join(failures) + "\nReturn the whole report again with every problem fixed."})
    return messages


def check_citations(out, bundle: dict) -> list[str]:
    """Every problem with a report's citations. An empty list means it passes."""
    if not isinstance(out, dict):
        return ["the report is not a JSON object"]
    failures = []
    claims = [("summary", 1, out.get("summary"))]
    for section in SECTIONS:
        items = out.get(section)
        if not isinstance(items, list):
            failures.append(f'section "{section}" is missing')
            continue
        claims += [(section, n, claim) for n, claim in enumerate(items, 1)]
    for section, n, claim in claims:
        where = f'claim {n} in "{section}"'
        if not isinstance(claim, dict) or not isinstance(claim.get("text"), str) or not claim["text"].strip():
            failures.append(f"{where} has no text")
            continue
        cites = claim.get("cites")
        if not isinstance(cites, list) or not cites:
            failures.append(f"{where} cites nothing")
            continue
        failures += [f"{where} cites unknown key {key}" for key in cites if not isinstance(key, str) or key not in bundle]
    return failures


def template(query: dict, bundle: dict, top_id: str) -> dict:
    """The report written by code from the bundle alone. Every claim cites its own
    keys, so it always passes check_citations."""
    c = bundle[top_id]
    p = c["properties"]
    cite = [top_id]
    datasets = ", ".join(c["datasets"]) or "no named dataset"
    summary = _claim(f"{c['caption']} ({c['schema']}) matched with score {c['score']:.2f}; listed in {datasets}.", cite)

    who = [_claim(f"Listed as {c['caption']} ({c['schema']}).", cite)]
    aliases = [a for a in dict.fromkeys(p.get("alias", []) + p.get("weakAlias", [])) if a != c["caption"]]
    if aliases:
        who.append(_claim("Also known as: " + ", ".join(aliases[:10]) + ".", cite))
    for prop, label in (("birthDate", "Born"), ("nationality", "Nationality"), ("country", "Country"),
                        ("jurisdiction", "Jurisdiction"), ("registrationNumber", "Registration number")):
        if p.get(prop):
            who.append(_claim(f"{label}: {', '.join(p[prop])}.", cite))

    features = [(name, r) for name, r in c["explanations"].items() if isinstance(r, dict) and (r.get("score") or 0) > 0]
    why = [_claim(_feature_line("Matcher feature", name, r), cite) for name, r in features if not _penalty(name)]
    why = why or [_claim(f"Overall match score {c['score']:.2f}.", cite)]
    differences = [_claim(_feature_line("Matcher penalty", name, r), cite) for name, r in features if _penalty(name)]
    differences += _field_differences(query, p, cite)
    if not differences:
        differences = [_claim("The details you gave agree with the listing." if len(query["properties"]) > 1
                              else "Only a name was given, so no other details could be compared.", cite)]

    sanctions = [_claim(_sanction_line(item), [key]) for key, item in bundle.items() if item["kind"] == "sanction"]
    sanctions += [_claim(f"UN Security Council list entry {item['ref']} ({item['list_type']}), listed "
                         f"{item['listed_on']}: matched {item['matched_name']} ({item['quality']}, "
                         f"score {item['score']:.2f}).", [key])
                  for key, item in bundle.items() if item["kind"] == "un"]
    sanctions = sanctions or [_claim(f"Listed in: {datasets}.", cite)]
    news = [_claim(f"{item['title'] or item['url']} ({item['published_date'] or 'undated'}).", [key])
            for key, item in bundle.items() if item["kind"] == "news"]
    return {"summary": summary, "who_matched": who, "why_it_matched": why,
            "differences": differences, "sanctions": sanctions, "news": news}


def _claim(text: str, cites: list[str]) -> dict:
    return {"text": text, "cites": cites}


def _penalty(feature: str) -> bool:
    return any(word in feature for word in _PENALTY_WORDS)


def _feature_line(prefix: str, name: str, result: dict) -> str:
    detail = f": {result['detail']}" if result.get("detail") else ""
    return f"{prefix} {name} scored {result['score']:.2f}{detail}."


def _field_differences(query: dict, listed: dict, cite: list[str]) -> list[dict]:
    """Each detail the user gave that the listing lacks or contradicts."""
    claims = []
    for prop, values in query["properties"].items():
        if prop == "name":
            continue
        asked, label = values[0], _LABELS.get(prop, prop)
        shown = sorted({v for other in _COMPARE.get(prop, (prop,)) for v in listed.get(other, []) if v})
        if not shown:
            claims.append(_claim(f"You gave {label} {asked}; the listing has no {label}.", cite))
        elif not any(v.lower().startswith(asked.lower()) or asked.lower().startswith(v.lower()) for v in shown):
            claims.append(_claim(f"You gave {label} {asked}; the listing shows {', '.join(shown)}.", cite))
    return claims


def _sanction_line(item: dict) -> str:
    p = item["properties"]
    line = item["label"]
    if p.get("authority"):
        line += " by " + ", ".join(p["authority"])
    if p.get("startDate"):
        line += " since " + ", ".join(p["startDate"])
    return line + "."
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_layer3.py -v`
Expected: 21 passed.

Run: `python -m pytest`
Expected: 97 passed.

- [ ] **Step 5: Commit**

```bash
git add tradecheck/layer3.py tests/test_layer3.py
git commit -m "feat: add the Layer 3 cited report with retries and a template fallback" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 9: The pipeline and its command line

**Files:**
- Create: `tradecheck/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything from Tasks 2 to 8.
- Produces:
  - `pipeline.start(un_path=layer1_un.DEFAULT_PATH) -> {"un_records": int, "as_of": str | None}`. Call it once before the first screen.
  - `pipeline.screen(question: str) -> dict`, the `POST /screen` response: `screen_id`, `question`, `parsed`, `band`, `label`, `score`, `reasons`, `status`, `candidates` (each `{"id", "caption", "score", "datasets"}`), `un_check`, `live`, `fetched_at`, `report`, `as_of`, `checked_at`, `disclaimer`. It writes the `input`, `layer1`, `layer2`, `layer3` (review and hit with a candidate only) and `final` events, and raises `parse.NoParty` after logging the input.
  - `pipeline.DISCLAIMER`.
  - Command line: `python -m tradecheck.pipeline [--full] QUESTION [QUESTION ...]`, which loads `.env`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
"""End to end with every outside service faked: each band, the saved-result fallback,
and the audit events each screen writes."""

import sqlite3
from pathlib import Path

import pytest

from tests.fakes import FakeResponse, chat_reply
from tradecheck import audit, parse, pipeline

FIXTURE = Path(__file__).parent / "fixtures" / "un_sample.xml"
REPORT = {"summary": {"text": "Khawa Panga Mandro matched.", "cites": ["NK-kpm"]},
          "who_matched": [], "why_it_matched": [], "differences": [], "sanctions": [], "news": []}


def _candidate(score):
    return {"id": "NK-kpm", "caption": "Khawa Panga Mandro", "schema": "Person", "score": score,
            "datasets": ["un_sc_sanctions"], "explanations": {}, "properties": {"name": ["Khawa Panga Mandro"]}}


@pytest.fixture
def world(fake_http):
    """Fake OpenSanctions, OpenRouter and Tavily. Tests set the extracted name and the match results."""
    routes, calls = fake_http
    state = {"name": "Khawa Panga Mandro", "results": []}

    def model(kwargs):
        if kwargs["json"]["response_format"]["json_schema"]["name"] == "query":
            return chat_reply({"schema": "Person", "name": state["name"], "country": None,
                               "birth_date": None, "registration_number": None})
        return chat_reply(REPORT)

    routes["/catalog"] = FakeResponse(200, {"datasets": [{"name": "sanctions", "updated_at": "2026-10-08T06:00:00"}]})
    routes["/match/sanctions"] = lambda kwargs: FakeResponse(200, {"responses": {"q": {"results": state["results"]}}})
    routes["/entities/"] = FakeResponse(500)
    routes["api.tavily.com"] = FakeResponse(200, {"results": []})
    routes["openrouter.ai"] = model
    assert pipeline.start(FIXTURE) == {"un_records": 2, "as_of": "2026-10-08T06:00:00"}
    return state, routes, calls


def _report_calls(calls):
    return [c for c in calls if "openrouter.ai" in c[1]
            and c[2]["json"]["response_format"]["json_schema"]["name"] == "report"]


def _steps(res):
    return [e["step"] for e in audit.record(res["screen_id"])]


def test_hit(world):
    state, _, _ = world
    state["results"] = [_candidate(0.97)]
    res = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    assert (res["band"], res["label"], res["status"], res["live"]) == ("hit", "Avoid", "flagged", True)
    assert res["report"]["check"] == "pass"
    assert res["as_of"] == "2026-10-08T06:00:00"
    assert res["disclaimer"] == "Not legal advice. Screening reflects the listed sources as of the dates shown."
    assert _steps(res) == ["input", "layer1", "layer2", "layer3", "final"]
    layer1 = audit.record(res["screen_id"])[1]["data"]
    assert (layer1["algorithm"], layer1["threshold"], layer1["candidates"][0]["id"]) == ("logic-v2", 0.7, "NK-kpm")


def test_review_goes_to_the_queue_and_can_be_dismissed(world):
    state, _, _ = world
    state["name"], state["results"] = "Chief Kahwa", [_candidate(0.80)]
    res = pipeline.screen("Can we sell to Chief Kahwa?")
    assert (res["band"], res["label"], res["status"]) == ("review", "Caution", "pending_review")
    assert res["un_check"]["status"] == "agree"
    assert [i["screen_id"] for i in audit.queue()] == [res["screen_id"]]
    audit.review(res["screen_id"], "dismiss", "Different person", "Analyst A")
    assert audit.queue() == []
    assert audit.record(res["screen_id"])[-1]["data"] == {"status": "reviewed", "decision": "dismiss"}


def test_clear_makes_no_report_call(world):
    state, _, calls = world
    state["name"] = "AgroDistribuidora del Bajío SA de CV"
    res = pipeline.screen("Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico?")
    assert (res["band"], res["label"], res["status"]) == ("clear", "Clear*", "cleared")
    assert _report_calls(calls) == []
    assert (res["report"]["check"], res["report"]["summary"]["cites"]) == ("fixed", ["sanctions", "UN"])
    assert "as of 2026-10-08T06:00:00" in res["report"]["summary"]["text"]
    assert _steps(res) == ["input", "layer1", "layer2", "final"]


def test_unknown_when_yente_is_down_and_nothing_is_saved(world):
    _, routes, calls = world
    routes["/match/sanctions"] = FakeResponse(503)
    res = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    assert (res["band"], res["label"], res["status"], res["live"]) == ("unknown", "Unknown", "unknown", False)
    assert res["report"]["summary"]["text"] == "Live check failed: OpenSanctions answered HTTP 503. Not clear. Retry."
    assert _report_calls(calls) == []
    assert audit.record(res["screen_id"])[1]["data"]["error"] == "OpenSanctions answered HTTP 503"


def test_the_saved_result_is_used_when_yente_goes_down(world):
    state, routes, _ = world
    state["results"] = [_candidate(0.97)]
    first = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    routes["/match/sanctions"] = FakeResponse(503)
    second = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    assert (second["band"], second["live"], second["fetched_at"]) == ("hit", False, first["fetched_at"])


def test_a_review_with_no_matcher_candidate_gets_a_fixed_report(world):
    state, _, calls = world
    state["name"] = "KHAWA PANGA MANDRO"  # the UN list has it; the matcher returns nothing
    res = pipeline.screen("Is KHAWA PANGA MANDRO sanctioned?")
    assert (res["band"], res["un_check"]["status"]) == ("review", "disagree")
    assert (res["report"]["check"], _report_calls(calls)) == ("fixed", [])
    assert [i["screen_id"] for i in audit.queue()] == [res["screen_id"]]


def test_a_fallback_extraction_is_not_clear(world):
    _, routes, _ = world
    routes["openrouter.ai"] = FakeResponse(503)
    res = pipeline.screen("Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico?")
    assert (res["parsed"]["source"], res["band"]) == ("fallback", "review")


def test_no_party_logs_the_input_and_raises(world):
    state, _, _ = world
    state["name"] = None
    with pytest.raises(parse.NoParty):
        pipeline.screen("What are the export rules for Mexico?")
    con = sqlite3.connect(audit.DB_PATH)
    rows = con.execute("SELECT step, json_extract(data, '$.error') FROM events").fetchall()
    con.close()
    assert rows == [("input", "No party name found in the question")]
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: collection ERROR, `ImportError: cannot import name 'pipeline' from 'tradecheck'`.

- [ ] **Step 3: Write the module**

Create `tradecheck/pipeline.py`:

```python
"""Run one question through every layer and write the audit trail.

Also a command line for the live check (it reads the keys from .env):
    python -m tradecheck.pipeline "Is KHAWA PANGA MANDRO sanctioned?" "Chief Kahwa"
    python -m tradecheck.pipeline --full "Chief Kahwa"     # the whole result as JSON
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from tradecheck import audit, http, layer1_un, layer1_yente, layer2, layer3, parse

DISCLAIMER = "Not legal advice. Screening reflects the listed sources as of the dates shown."
COLLECTION_SOURCE = {"key": "sanctions", "kind": "collection", "label": "OpenSanctions sanctions collection",
                     "url": "https://www.opensanctions.org/datasets/sanctions/"}
UN_SOURCE = {"key": "UN", "kind": "un_list", "label": "UN Security Council consolidated list",
             "url": layer1_un.LIST_URL}

AS_OF: str | None = None  # when the sanctions collection was last updated; set by start()


def start(un_path: Path | str = layer1_un.DEFAULT_PATH) -> dict:
    """Load what every screen needs. Call once, before the first screen."""
    global AS_OF
    audit.init()
    records = layer1_un.load(un_path)
    AS_OF = layer1_yente.catalog_as_of()
    return {"un_records": records, "as_of": AS_OF}


def screen(question: str) -> dict:
    """Screen one question and return the API response. Raises parse.NoParty when the
    question names no party."""
    screen_id = str(uuid.uuid4())
    checked_at = audit.now()
    model = {"model": http.MODEL, "prompt_version": parse.PROMPT_VERSION}
    try:
        query = parse.extract(question)
    except parse.NoParty as err:
        audit.log(screen_id, "input", {"question": question, "error": str(err), **model})
        raise
    audit.log(screen_id, "input", {"question": question, "parsed": query, "source": query["source"], **model})

    try:
        match, error = layer1_yente.match(query), None
    except layer1_yente.YenteUnavailable as err:
        match, error = None, str(err)
    un = layer1_un.check(query, match)
    live = bool(match and match["live"])
    fetched_at = match["fetched_at"] if match else None
    candidates = [{"id": c["id"], "caption": c.get("caption"), "score": c["score"], "datasets": c.get("datasets", [])}
                  for c in (match["candidates"] if match else [])]
    audit.log(screen_id, "layer1", {
        "collection": layer1_yente.COLLECTION, "algorithm": layer1_yente.ALGORITHM,
        "threshold": layer1_yente.THRESHOLD, "candidates": candidates, "live": live, "fetched_at": fetched_at,
        "query_hash": match["query_hash"] if match else None, "error": error, "un": un,
    })

    band = layer2.band(match, un, fallback=query["source"] == "fallback")
    audit.log(screen_id, "layer2", band)

    if band["band"] in ("review", "hit") and candidates:
        report = layer3.report(query, match, un, band)
        audit.log(screen_id, "layer3", report)
    else:
        report = _fixed_report(band, error)

    status = layer2.STATUS[band["band"]]
    audit.log(screen_id, "final", {"status": status})
    return {
        "screen_id": screen_id,
        "question": question,
        "parsed": query,
        "band": band["band"],
        "label": layer2.LABEL[band["band"]],
        "score": band["score"],
        "reasons": band["reasons"],
        "status": status,
        "candidates": candidates,
        "un_check": un,
        "live": live,
        "fetched_at": fetched_at,
        "report": report,
        "as_of": AS_OF,
        "checked_at": checked_at,
        "disclaimer": DISCLAIMER,
    }


def _fixed_report(band: dict, error: str | None) -> dict:
    """The report when Layer 3 doesn't run: clear, unknown, or a review raised with no
    matcher candidate to explain. No model call."""
    name = band["band"]
    if name == "unknown":
        summary, sources = {"text": f"Live check failed: {error}. Not clear. Retry.", "cites": []}, []
    elif name == "clear":
        summary = {"text": f"No candidate reached {layer2.CLEAR_BELOW:.2f} in the OpenSanctions sanctions "
                           f"collection (US, Canada, EU, UK, UN and more) as of {AS_OF or 'the time checked'}, "
                           "and the UN check agrees.", "cites": ["sanctions", "UN"]}
        sources = [COLLECTION_SOURCE, UN_SOURCE]
    else:
        summary = {"text": f"No matcher candidate reached {layer2.CLEAR_BELOW:.2f}, but this needs review: "
                           + "; ".join(band["reasons"]) + ".", "cites": ["sanctions", "UN"]}
        sources = [COLLECTION_SOURCE, UN_SOURCE]
    return {"summary": summary, "sections": {section: [] for section in layer3.SECTIONS}, "sources": sources,
            "next_step": layer3.NEXT_STEP.get(name), "check": "fixed", "check_reason": None,
            "attempts": 0, "steps": {}}


def main(argv: list[str] | None = None) -> int:
    from dotenv import load_dotenv  # comes with uvicorn[standard]

    ap = argparse.ArgumentParser(description="Screen questions through every TradeCheck layer.")
    ap.add_argument("questions", nargs="+", help="free-text questions or bare names")
    ap.add_argument("--full", action="store_true", help="print each whole result as JSON")
    args = ap.parse_args(argv)
    load_dotenv()
    info = start()
    print(f"UN records: {info['un_records']}  sanctions data as of: {info['as_of']}", file=sys.stderr)
    for question in args.questions:
        began = time.monotonic()
        try:
            res = screen(question)
        except parse.NoParty as err:
            print(f"{question!r}: {err}", file=sys.stderr)
            continue
        if args.full:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        top = res["candidates"][0]["caption"] if res["candidates"] else "-"
        score = "-" if res["score"] is None else f"{res['score']:.2f}"
        print(f"{res['label']:8} score={score}  top={top}  UN={res['un_check']['status']}  "
              f"parsed={res['parsed']['source']}  live={res['live']}  "
              f"report={res['report']['check']}/{res['report']['attempts']}  "
              f"{time.monotonic() - began:.1f}s  {res['screen_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_pipeline.py -v`
Expected: 8 passed.

Run: `python -m pytest`
Expected: 105 passed.

Run: `python -m tradecheck.pipeline --help`
Expected: usage text listing `questions` and `--full`.

- [ ] **Step 5: Commit**

```bash
git add tradecheck/pipeline.py tests/test_pipeline.py
git commit -m "feat: run every layer in one pipeline with a command line" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 10: The API

**Files:**
- Modify: `app/main.py` (replace the whole file)
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `pipeline.start`, `pipeline.screen` (Task 9); `parse.NoParty`, `parse.NO_PARTY` (Task 4); `audit.queue`, `audit.review`, `audit.record` and the three review exceptions (Task 3).
- Produces: `POST /screen {"question"}`, `GET /queue`, `POST /review/{screen_id} {"decision": "confirm" | "dismiss", "note", "reviewer"}`, `GET /audit/{screen_id}`, `GET /health`, and `/` serving the page. Errors: 422 for no party, a blank question or a missing note or reviewer; 404 for an unknown screen; 400 for a case not in review; 409 for a second review. The page in Task 11 calls these endpoints.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_app.py`:

```python
"""The API's status codes. Endpoints are called as plain functions, so no HTTP client is needed."""

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app import main
from tradecheck import audit, parse

REQ = main.ReviewRequest(decision="confirm", note="Same date of birth", reviewer="Analyst A")


def _status(call, *args):
    with pytest.raises(HTTPException) as exc:
        call(*args)
    return exc.value.status_code


def _case(screen_id, band):
    audit.log(screen_id, "input", {"question": "Can we sell to Chief Kahwa?"})
    audit.log(screen_id, "layer2", {"band": band, "score": 0.8})


def test_review_status_codes():
    _case("rev", "review")
    _case("hit", "hit")
    assert _status(main.review_endpoint, "nope", REQ) == 404
    assert _status(main.review_endpoint, "hit", REQ) == 400
    assert main.review_endpoint("rev", REQ) == {"status": "reviewed", "decision": "confirm"}
    assert _status(main.review_endpoint, "rev", REQ) == 409


@pytest.mark.parametrize("fields", [
    {"decision": "confirm", "note": "   ", "reviewer": "Analyst A"},
    {"decision": "confirm", "note": "Same DOB", "reviewer": ""},
    {"decision": "approve", "note": "Same DOB", "reviewer": "Analyst A"},
])
def test_review_needs_a_decision_a_note_and_a_reviewer(fields):
    with pytest.raises(ValidationError):  # FastAPI answers 422
        main.ReviewRequest(**fields)


def test_a_question_without_a_party_is_422(monkeypatch):
    def no_party(question):
        raise parse.NoParty(parse.NO_PARTY)

    monkeypatch.setattr(main.pipeline, "screen", no_party)
    with pytest.raises(HTTPException) as exc:
        main.screen_endpoint(main.ScreenRequest(question="What are the export rules?"))
    assert (exc.value.status_code, exc.value.detail) == (422, "No party name found in the question")


def test_a_blank_question_is_rejected():
    with pytest.raises(ValidationError):
        main.ScreenRequest(question="  ")


def test_the_audit_record_of_an_unknown_screen_is_404():
    assert _status(main.audit_endpoint, "nope") == 404
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python -m pytest tests/test_app.py -v`
Expected: collection ERROR, `AttributeError: module 'app.main' has no attribute 'ReviewRequest'`.

- [ ] **Step 3: Write the app**

Replace the whole of `app/main.py` with:

```python
"""FastAPI app: screen a question, work the review queue, read a screen's audit record.

Run:  uvicorn app.main:app --env-file .env
      open http://127.0.0.1:8000
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, StringConstraints

from tradecheck import audit, parse, pipeline

STATIC_DIR = Path(__file__).resolve().parent / "static"
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


@asynccontextmanager
async def lifespan(app: FastAPI):
    pipeline.start()  # audit tables, the UN list, the data date
    yield


app = FastAPI(title="TradeCheck", lifespan=lifespan,
              description="Sanctions screening with the source and date behind every answer.")


class ScreenRequest(BaseModel):
    question: Text


class ReviewRequest(BaseModel):
    decision: Literal["confirm", "dismiss"]
    note: Text
    reviewer: Text


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/screen")
def screen_endpoint(req: ScreenRequest) -> dict:
    try:
        return pipeline.screen(req.question)
    except parse.NoParty as err:
        raise HTTPException(status_code=422, detail=str(err)) from err


@app.get("/queue")
def queue_endpoint() -> list[dict]:
    return audit.queue()


@app.post("/review/{screen_id}")
def review_endpoint(screen_id: str, req: ReviewRequest) -> dict:
    try:
        return audit.review(screen_id, req.decision, req.note, req.reviewer)
    except audit.NotFound as err:
        raise HTTPException(status_code=404, detail=str(err)) from err
    except audit.NotInReview as err:
        raise HTTPException(status_code=400, detail=str(err)) from err
    except audit.AlreadyReviewed as err:
        raise HTTPException(status_code=409, detail=str(err)) from err


@app.get("/audit/{screen_id}")
def audit_endpoint(screen_id: str) -> list[dict]:
    events = audit.record(screen_id)
    if not events:
        raise HTTPException(status_code=404, detail=f"No screen with ID {screen_id}")
    return events


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


# Serve the static assets (index.html and anything added later).
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python -m pytest tests/test_app.py -v`
Expected: 7 passed.

Run: `python -m pytest`
Expected: 112 passed.

- [ ] **Step 5: Commit**

```bash
git add app/main.py tests/test_app.py
git commit -m "feat: add the screen, queue, review and audit endpoints" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 11: The page

**Files:**
- Modify: `app/static/index.html` (replace the whole file)

**Interfaces:**
- Consumes: the endpoints and response shapes from Task 10, the report shape from Task 8 and the fixed reports from Task 9.
- Produces: the demo UI. It keeps the existing colour tokens, dark mode and `esc()` escaping, and adds `safeUrl()` so only `http(s)` links render.

- [ ] **Step 1: Write the page**

Replace the whole of `app/static/index.html` with:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>TradeCheck</title>
  <style>
    :root {
      --bg: #f6f7f9; --card: #ffffff; --ink: #111418; --muted: #5b6672;
      --border: #e2e6eb; --accent: #1d4ed8;
      --avoid-bg: #fdecec; --avoid-ink: #a01a1a; --avoid-bar: #d64545;
      --caution-bg: #fdf4e3; --caution-ink: #8a5a00; --caution-bar: #e0a100;
      --clear-bg: #e9f7ef; --clear-ink: #116b3a; --clear-bar: #2f9e5f;
      --unknown-bg: #eef0f2; --unknown-ink: #4a545f; --unknown-bar: #8893a0;
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0f1419; --card: #171d24; --ink: #e9edf1; --muted: #9aa6b2;
        --border: #262e38; --accent: #6ea0ff;
        --avoid-bg: #2a1718; --avoid-ink: #ff9b9b; --avoid-bar: #d64545;
        --caution-bg: #2a2314; --caution-ink: #f0c66b; --caution-bar: #e0a100;
        --clear-bg: #15241c; --clear-ink: #86e0ab; --clear-bar: #2f9e5f;
        --unknown-bg: #1b2129; --unknown-ink: #aab4bf; --unknown-bar: #8893a0;
      }
    }
    * { box-sizing: border-box; }
    body {
      margin: 0; background: var(--bg); color: var(--ink);
      font: 16px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    .wrap { max-width: 860px; margin: 0 auto; padding: 32px 16px 64px; }
    h1 { font-size: 26px; margin: 0 0 4px; letter-spacing: -0.3px; }
    .tagline { color: var(--muted); margin: 0 0 24px; font-size: 15px; }
    form#f { display: flex; gap: 8px; margin-bottom: 8px; }
    input[type=text], textarea {
      width: 100%; padding: 12px 14px; font: inherit; border: 1px solid var(--border);
      border-radius: 10px; background: var(--card); color: var(--ink);
    }
    form#f input { flex: 1; }
    input[type=text]:focus, textarea:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
    button {
      padding: 12px 18px; font-size: 16px; font-weight: 600; border: 0; cursor: pointer;
      border-radius: 10px; background: var(--accent); color: #fff;
    }
    button.secondary { background: var(--card); color: var(--ink); border: 1px solid var(--border); }
    button:disabled { opacity: .6; cursor: default; }
    a { color: var(--accent); }
    .hint { color: var(--muted); font-size: 13px; margin: 0 0 12px; }
    .note { color: var(--caution-ink); font-size: 13px; }
    .err { color: var(--avoid-ink); }
    .verdict {
      border-radius: 14px; padding: 18px 18px 18px 22px; margin: 20px 0 16px;
      border: 1px solid var(--border); position: relative; overflow: hidden;
    }
    .verdict::before { content: ""; position: absolute; left: 0; top: 0; bottom: 0; width: 6px; }
    .verdict.avoid   { background: var(--avoid-bg); }   .verdict.avoid::before   { background: var(--avoid-bar); }
    .verdict.caution { background: var(--caution-bg); } .verdict.caution::before { background: var(--caution-bar); }
    .verdict.clear   { background: var(--clear-bg); }   .verdict.clear::before   { background: var(--clear-bar); }
    .verdict.unknown { background: var(--unknown-bg); } .verdict.unknown::before { background: var(--unknown-bar); }
    .verdict h2 { margin: 0 0 4px; font-size: 20px; }
    .verdict.avoid h2 { color: var(--avoid-ink); }
    .verdict.caution h2 { color: var(--caution-ink); }
    .verdict.clear h2 { color: var(--clear-ink); }
    .verdict.unknown h2 { color: var(--unknown-ink); }
    .verdict p { margin: 4px 0 0; font-size: 14px; }
    .badge { font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); vertical-align: middle; }
    .card { background: var(--card); border: 1px solid var(--border); border-radius: 12px; padding: 14px 16px; margin: 10px 0; }
    .card h3 { margin: 0 0 8px; font-size: 16px; }
    .card h4 { margin: 12px 0 4px; font-size: 14px; }
    dl.kv { display: grid; grid-template-columns: 150px 1fr; gap: 2px 12px; margin: 0; font-size: 14px; }
    dl.kv dt { color: var(--muted); } dl.kv dd { margin: 0; overflow-wrap: anywhere; }
    .claims { margin: 0; padding-left: 20px; font-size: 14px; }
    .cite { font-size: 12px; text-decoration: none; }
    .grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
    .queue { list-style: none; padding: 0; margin: 0; }
    .queue li { display: flex; justify-content: space-between; align-items: center; gap: 10px; padding: 8px 0; border-top: 1px solid var(--border); font-size: 14px; }
    .queue button { padding: 6px 12px; font-size: 14px; }
    #rv p { margin: 0 0 8px; }
    .meta { color: var(--muted); font-size: 13px; margin-top: 18px; border-top: 1px solid var(--border); padding-top: 12px; }
    .meta code { font-size: 12px; }
    footer { color: var(--muted); font-size: 12px; margin-top: 28px; }
    @media (max-width: 640px) {
      .grid2 { grid-template-columns: 1fr; }
      dl.kv { grid-template-columns: 110px 1fr; }
    }
  </style>
</head>
<body>
  <div class="wrap">
    <h1>TradeCheck</h1>
    <p class="tagline">Sanctions screening across the US, Canada, EU, UK and UN lists &mdash; with the source and date behind every answer.</p>

    <form id="f">
      <input id="q" type="text" placeholder="e.g. Can we ship to Northwind Metals FZE in Dubai?" autocomplete="off" autofocus />
      <button id="go" type="submit">Screen</button>
    </form>
    <p class="hint">The matcher decides; the model only explains. Missing data shows as <em>Unknown</em>, never green.</p>

    <div id="out" aria-live="polite"></div>

    <section class="card" aria-labelledby="queue-title">
      <h3 id="queue-title">Review queue</h3>
      <div id="queue"><p class="hint">Loading&hellip;</p></div>
      <div id="case"></div>
    </section>

    <footer>Sanctions data: OpenSanctions, CC BY-NC 4.0. Not legal advice.</footer>
  </div>

  <script>
    const $ = id => document.getElementById(id);
    const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
    // Only http(s) links become hrefs; anything else (javascript:, data:) goes nowhere.
    const safeUrl = u => /^https?:\/\//i.test(String(u ?? "")) ? String(u) : "#";
    const link = (url, text) => `<a href="${esc(safeUrl(url))}" target="_blank" rel="noopener">${text}</a>`;
    const num = n => typeof n === "number" ? n.toFixed(2) : "—";
    const cap = s => s ? s[0].toUpperCase() + s.slice(1) : "";
    const CLASS = { "Avoid": "avoid", "Caution": "caution", "Clear*": "clear", "Unknown": "unknown" };
    const LINE = {
      hit: "Strong sanctions match.",
      review: "Possible match, flagged for human review.",
      clear: "No match across the lists checked.",
      unknown: "The live check failed. Treat this as Unknown, not clear.",
    };
    const SECTIONS = [["who_matched", "Who matched"], ["why_it_matched", "Why it matched"],
                      ["differences", "Differences"], ["sanctions", "Sanctions"], ["news", "News"]];

    async function api(path, body) {
      const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
      const r = await fetch(path, opts);
      const data = await r.json().catch(() => ({}));
      if (!r.ok) {
        const d = data.detail;
        throw new Error(Array.isArray(d) ? d.map(x => `${(x.loc || []).slice(-1)[0]}: ${x.msg}`).join("; ") : (d || "HTTP " + r.status));
      }
      return data;
    }

    function claimsHtml(claims, sources) {
      const index = Object.fromEntries(sources.map((s, i) => [s.key, i]));
      return claims.map(c => `<li>${esc(c.text)}${(c.cites || []).map(key => {
        const s = sources[index[key]];
        return s ? ` <a class="cite" href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener" title="${esc(s.label)}">[${index[key] + 1}]</a>` : "";
      }).join("")}</li>`).join("");
    }

    function reportHtml(rep) {
      const sources = rep.sources || [];
      let html = `<div class="card"><h3>Report</h3><ul class="claims">${claimsHtml([rep.summary], sources)}</ul>`;
      for (const [key, title] of SECTIONS) {
        const claims = (rep.sections || {})[key] || [];
        if (key === "news" && (rep.steps || {}).news === "unavailable") {
          html += `<h4>${title}</h4><p class="hint">News search unavailable.</p>`;
        } else if (claims.length) {
          html += `<h4>${title}</h4><ul class="claims">${claimsHtml(claims, sources)}</ul>`;
        }
      }
      if (rep.next_step) html += `<p><strong>Next step:</strong> ${esc(rep.next_step)}</p>`;
      if (rep.check === "template") {
        html += `<p class="note">The model's report didn't pass its checks (${esc(rep.check_reason)}), so code wrote this report from the evidence.</p>`;
      }
      if (sources.length) {
        html += `<h4>Sources</h4><ol class="claims">${sources.map(s => `<li>${link(s.url, esc(s.label))}</li>`).join("")}</ol>`;
      }
      return html + "</div>";
    }

    function fieldsHtml(p) {
      const rows = [["Type", p.schema], ["Name", p.name], ["Country", p.country], ["Birth date", p.birth_date],
                    ["Registration no.", p.registration_number]].filter(([, v]) => v);
      let html = `<dl class="kv">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${esc(v)}</dd>`).join("")}</dl>`;
      if (p.source === "fallback") {
        html += `<p class="note">The fields could not be extracted (${esc(p.fallback_reason)}), so the whole question was screened as a name.</p>`;
      }
      return html;
    }

    function render(res) {
      const un = res.un_check;
      let html = `<div class="verdict ${CLASS[res.label] || "unknown"}">
          <h2>${esc(res.label)}${res.status === "unknown" ? "" : ` <span class="badge">${esc(res.status.replace("_", " "))}</span>`}</h2>
          <p>${esc(LINE[res.band])} Score: ${num(res.score)}.</p>
          <p class="hint">${res.reasons.map(esc).join(" · ")}</p>
        </div>`;
      if (!res.live && res.fetched_at) {
        html += `<p class="note">Live check failed; showing the saved result from ${esc(res.fetched_at)}.</p>`;
      }
      html += `<div class="card"><h3>What we screened</h3>${fieldsHtml(res.parsed)}</div>`;
      if (res.candidates.length) {
        html += `<div class="card"><h3>Matcher candidates</h3><ul class="claims">${res.candidates.map(c =>
          `<li>${link("https://www.opensanctions.org/entities/" + encodeURIComponent(c.id) + "/", esc(c.caption))} · score ${num(c.score)} · ${esc(c.datasets.join(", "))}</li>`).join("")}</ul></div>`;
      }
      html += `<div class="card"><h3>UN cross-check: ${esc(un.status)}</h3><p class="hint">${esc(cap(un.reason))}.${
        un.best ? ` Best UN name: ${esc(un.best.matched_name)} (${esc(un.best.quality)}, ${num(un.best.score)}).` : ""}</p></div>`;
      html += reportHtml(res.report);
      html += `<div class="meta">
          Sanctions data as of <code>${esc(res.as_of || "unknown")}</code> · UN list from <code>${esc(un.list_date || "not loaded")}</code><br>
          Checked at <code>${esc(res.checked_at)}</code> · <a href="/audit/${encodeURIComponent(res.screen_id)}" target="_blank" rel="noopener">View audit record</a><br>
          ${esc(res.disclaimer)}</div>`;
      $("out").innerHTML = html;
    }

    async function loadQueue() {
      try {
        const items = await api("/queue");
        $("queue").innerHTML = items.length
          ? `<ul class="queue">${items.map(i => `<li><span>${esc(i.question)} · score ${num(i.score)}</span>
               <button class="secondary" type="button" data-id="${esc(i.screen_id)}">Open</button></li>`).join("")}</ul>`
          : `<p class="hint">No cases waiting for review.</p>`;
      } catch (err) {
        $("queue").innerHTML = `<p class="err">Couldn't load the queue: ${esc(err.message)}</p>`;
      }
    }

    async function openCase(id) {
      const ev = {};
      for (const e of await api(`/audit/${encodeURIComponent(id)}`)) ev[e.step] = e.data;
      const top = (ev.layer1.candidates || [])[0];
      const topHtml = top
        ? `<dl class="kv"><dt>Name</dt><dd>${esc(top.caption)}</dd><dt>ID</dt><dd>${esc(top.id)}</dd>
             <dt>Score</dt><dd>${num(top.score)}</dd><dt>Datasets</dt><dd>${esc(top.datasets.join(", "))}</dd></dl>`
        : `<p class="hint">The matcher returned no candidate.</p>`;
      $("case").innerHTML = `
        <div class="grid2">
          <div class="card"><h3>The query</h3>${fieldsHtml(ev.input.parsed)}</div>
          <div class="card"><h3>Top candidate</h3>${topHtml}</div>
        </div>
        <p class="hint">In review because: ${ev.layer2.reasons.map(esc).join(" · ")}</p>
        ${ev.layer3 ? reportHtml(ev.layer3) : ""}
        <form id="rv" class="card">
          <h3>Decision</h3>
          <p><input id="reviewer" type="text" placeholder="Your name" required /></p>
          <p><textarea id="note" rows="3" placeholder="Why? e.g. different date of birth" required></textarea></p>
          <button type="submit" data-decision="confirm">Confirm match</button>
          <button type="submit" class="secondary" data-decision="dismiss">Dismiss as false positive</button>
          <p id="rvmsg" class="hint"></p>
        </form>`;
      $("rv").addEventListener("submit", async e => {
        e.preventDefault();
        try {
          const res = await api(`/review/${encodeURIComponent(id)}`, {
            decision: e.submitter.dataset.decision, note: $("note").value, reviewer: $("reviewer").value,
          });
          $("case").innerHTML = `<p class="hint">Recorded: ${esc(res.decision)}, status ${esc(res.status)}.
            <a href="/audit/${encodeURIComponent(id)}" target="_blank" rel="noopener">View audit record</a></p>`;
          loadQueue();
        } catch (err) {
          $("rvmsg").innerHTML = `<span class="err">${esc(err.message)}</span>`;
        }
      });
    }

    $("queue").addEventListener("click", e => {
      const id = e.target.dataset && e.target.dataset.id;
      if (id) openCase(id).catch(err => { $("case").innerHTML = `<p class="err">${esc(err.message)}</p>`; });
    });

    $("f").addEventListener("submit", async e => {
      e.preventDefault();
      const question = $("q").value.trim();
      if (!question) return;
      $("go").disabled = true;
      $("out").innerHTML = "<p class='hint'>Screening&hellip; a review or hit case can take up to a minute while the report is written.</p>";
      try {
        render(await api("/screen", { question }));
        loadQueue();
      } catch (err) {
        $("out").innerHTML = `<p class="err">Error: ${esc(err.message)}</p>`;
      } finally {
        $("go").disabled = false;
      }
    });

    loadQueue();
  </script>
</body>
</html>
```

- [ ] **Step 2: Check it in a browser**

Run: `uvicorn app.main:app --env-file .env` (if `.env` doesn't exist yet, run `uvicorn app.main:app`).
Open http://127.0.0.1:8000 and check:
- The page loads with the question box, the review queue panel ("No cases waiting for review.") and the footer "Sanctions data: OpenSanctions, CC BY-NC 4.0. Not legal advice."
- Screen "Is KHAWA PANGA MANDRO sanctioned?": a result card appears with the label, the "What we screened" fields, the UN cross-check card, the report and the meta line (data as of, UN list date, checked at, "View audit record", disclaimer). Without keys the card is Unknown and shows the note that the fields could not be extracted.
- "View audit record" opens the screen's events as JSON.
- The browser console shows no errors.
- At a phone width (375 px) the cards stack and nothing scrolls sideways.

The full walk-through, with every band and a review decision, is Task 13.

Stop uvicorn with Ctrl+C.

- [ ] **Step 3: Commit**

```bash
git add app/static/index.html
git commit -m "feat: rebuild the page for cited reports and the review queue" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 12: Run instructions and the README

**Files:**
- Modify: `RUN.md` (replace the whole file), `README.md` (four edits)

**Interfaces:**
- Consumes: the commands from Tasks 1, 9 and 10.
- Produces: the steps Task 13 follows.

- [ ] **Step 1: Rewrite RUN.md**

Replace the whole of `RUN.md` with:

````markdown
# Running TradeCheck

A question moves through three layers, as in the team's workflow diagram:

1. **Layer 1:** the hosted OpenSanctions matcher (yente) scores candidates, and our own copy of the UN Security Council list cross-checks it.
2. **Layer 2:** fixed cutoffs turn the top score into a band: clear below 0.70, review from 0.70, hit from 0.90. A failed match check is Unknown, never clear.
3. **Layer 3:** for review and hit cases only, a model writes a report from the evidence, and code checks every citation before the report is shown.

Review cases wait in a review queue, and every step is written to an append-only audit log. Design: [`docs/superpowers/specs/2026-10-08-tradecheck-architecture-design.md`](docs/superpowers/specs/2026-10-08-tradecheck-architecture-design.md).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill in the three keys in `.env`. Never commit it (it is in `.gitignore`), and never paste a key into an issue, a chat or a log.

| Key | Used for |
| --- | --- |
| `OPENSANCTIONS_API_KEY` | Layer 1 match and Layer 3 full records (the team's free key) |
| `OPENROUTER_API_KEY` | Model calls: extracting the party and writing the report |
| `TAVILY_API_KEY` | News search in Layer 3 reports |

## 1. Download the UN list

```bash
python -m ingest.fetch
```

This writes `data/raw/un_sc.xml` and `data/raw/manifest.json`, which records the URL, `fetched_at`, byte count and SHA-256. Without the file the UN check is unavailable, and no result can be Clear\*.

## 2. Screen from the command line

```bash
python -m tradecheck.pipeline "Is KHAWA PANGA MANDRO sanctioned?" "Can we sell to Chief Kahwa?" "Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico?"
```

The command reads `.env` itself. Each line shows the label, score, top candidate, UN check, where the fields came from (`model` or `fallback`), whether the match was live, the report check and attempts, the time taken and the screen ID. Add `--full` to print each whole result as JSON.

## 3. Run the web app

```bash
uvicorn app.main:app --env-file .env
```

Open http://127.0.0.1:8000. Restart uvicorn after you edit `.env`.

## Tests (offline)

```bash
python -m pytest
```

No test reaches the network: every outside service is faked.

## Demo cases

Filled in during the live check.

| Label | Question | Score | Time | Notes |
| --- | --- | --- | --- | --- |
| Avoid | | | | |
| Caution | | | | |
| Clear\* | | | | |

## Troubleshooting

### `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`

Python reached the server but couldn't verify its certificate. When every download or call
fails this way at once, the cause is almost always your network, VPN or antivirus inspecting
HTTPS: it re-signs traffic with its own root certificate, which your operating system (and so
your browser) trusts but Python's default bundle (`certifi`) doesn't.

1. Run `pip install -r requirements.txt`. It installs `truststore`, so TradeCheck verifies
   against your operating system's certificates, the same ones your browser uses. Then
   try again.
2. Still failing? Check whether it's interception. Does
   `python -c "import requests; requests.get('https://www.google.com')"` fail the same way?
   Does the browser's padlock show the certificate issued by Zscaler, Netskope, Fortinet, an
   antivirus or your university, rather than a public authority such as DigiCert or Entrust?
   If so, ask IT for that root certificate and point `REQUESTS_CA_BUNDLE` at a PEM bundle
   containing it plus the public roots. Or use another network (home Wi-Fi, a phone hotspot).

Never set `verify=False`: verification is what stops a tampered sanctions list or answer from
reaching the screen.

### OpenRouter answers `No endpoints found that can handle the requested parameters`

`provider.require_parameters` sends the request only to providers that support every setting,
including strict JSON output and `seed`. If no provider does, delete the `"seed": SEED,` line in
`chat()` in `tradecheck/http.py`, note the change here, and run the live check again.

### Every result is Unknown, or the fields always fall back

Check that all three keys are filled in in `.env`, then restart uvicorn. The audit record
(`/audit/<screen_id>`) says why: the `layer1` event's `error`, or the `input` event's
`parsed.fallback_reason`.

### `Tunnel connection failed: 403` (cloud sessions)

The session's network policy blocks the hosts. Allow `api.opensanctions.org`, `openrouter.ai`,
`api.tavily.com`, `scsanctions.un.org` and `unsolprodfiles.blob.core.windows.net` (where the UN
redirects the download), or run on your own machine.

## Data sources

| Source | Used for | Licence |
| --- | --- | --- |
| OpenSanctions `sanctions` collection (hosted API) | Layer 1 match, full records, data date | CC BY-NC 4.0: non-commercial use only |
| UN Security Council consolidated list | UN cross-check | Public government data |
| Tavily news search | News in review and hit reports | Tavily's terms of service |
````

- [ ] **Step 2: Update the README**

In `README.md`:

1. Replace everything from the heading `## Build Session 2 plan` up to (not including) `## Team` with:

````markdown
## How it works (Build Session 3)

**Decided Oct 8:** the team's workflow diagram ("How a question moves through TradeCheck") is the plan. It replaces the Oct 7 US + Canada direct-pull pipeline. One call to the hosted OpenSanctions matcher covers the US, Canada, EU, UK and UN lists, and our own copy of the UN list cross-checks it. OpenSanctions data is CC BY-NC 4.0: fine for the datathon, and a paid licence is needed before any commercial use. Design: [the spec](docs/superpowers/specs/2026-10-08-tradecheck-architecture-design.md). Commands: [`RUN.md`](RUN.md).

```
question ─► model extracts the party ─► Layer 1: OpenSanctions match + UN cross-check
         ─► Layer 2: bands (clear < 0.70 ≤ review < 0.90 ≤ hit; failed check = Unknown)
         ─► Layer 3, review and hit only: full record + news + model report, citations checked in code
         ─► review: analyst queue, then reviewed · hit: flagged · every step: append-only audit log
```

- **The matcher decides; the model explains.** Model output never changes a score, band, label or status.
- **Missing data is never clear.** A failed match check is Unknown. An unavailable UN check or a failed extraction blocks Clear\*.
- **Every claim cites evidence**, and code checks each citation before the report is shown.
- **The audit log is append-only**, enforced by database triggers.

**Stack:** Python, FastAPI, SQLite, rapidfuzz and one static HTML page, with OpenSanctions (hosted yente), OpenRouter (`qwen/qwen3.8-27b`) and Tavily.

### Open risks

- **Licence:** OpenSanctions data is non-commercial (CC BY-NC 4.0). Commercial use needs a paid licence.
- **Attribution:** CC BY-NC requires crediting OpenSanctions. The credit is in the UI footer and this README.
- **Placeholder cutoffs:** 0.70 and 0.90 are starting points, not tested numbers. Tune them on a labelled test set.
- **Third parties see the names:** counterparty names go to OpenSanctions, OpenRouter and Tavily. That's fine for the demo; disclose it before real users.
- **Staleness:** OpenSanctions refreshes four times a day, and our UN copy is fetched by hand. The UN cross-check flags the gap instead of hiding it.

````

2. Replace the paragraph under `## Repo status` with:

```markdown
Build Session 1 (Oct 5): framing and this README. Build Session 2 (Oct 7): a US + Canada direct-pull pipeline, since replaced. Build Session 3 (Oct 9): the three-layer flow above, with an OpenSanctions match plus UN cross-check, score bands, cited model reports, a review queue and an append-only audit log, tested offline with `python -m pytest`. See [`RUN.md`](RUN.md).
```

3. Replace the heading `## Demo script (3 cases, each live in under 5 seconds)` with `## Demo script (3 cases, live)`. Layer 3 reports take longer than 5 seconds; the measured times go in RUN.md.

4. In the "Data evidence" table, in the OpenSanctions row, replace the Access cell `Free bulk download, no key` with `Hosted matcher API (team key); bulk download is free`, and the Status cell `To ingest in Build Session 2` with `Layer 1 since Build Session 3, through the hosted matcher (yente)`.

- [ ] **Step 3: Check the links and commands**

Run: `python -m pytest`
Expected: 112 passed.

Open `RUN.md` and `README.md` in the editor's Markdown preview: the links to the spec and to `RUN.md` resolve, and every command matches the ones in Tasks 1, 9 and 10.

- [ ] **Step 4: Commit**

```bash
git add RUN.md README.md
git commit -m "docs: rewrite RUN.md and the README build section for the new flow" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 13: Live check and demo cases (needs the team's keys)

This is the spec's section 9. It needs the three keys in `.env`, typed in by the team. If you are an agent, stop here and ask the user to fill in `.env`; never read, print or ask for the key values.

**Files:**
- Modify: `RUN.md` (the "Demo cases" table), and `tradecheck/layer1_yente.py` or `tradecheck/layer3.py` only if Step 4 finds a field name that differs.

**Interfaces:**
- Consumes: the whole app.
- Produces: three demo cases that land in Avoid, Caution and Clear\*, saved yente responses for them in `data/tradecheck.sqlite`, and measured times.

- [ ] **Step 1: Confirm the keys are present, without showing them**

Run:

```bash
python -c "from dotenv import dotenv_values; v = dotenv_values('.env'); print({k: bool(v.get(k)) for k in ('OPENSANCTIONS_API_KEY', 'OPENROUTER_API_KEY', 'TAVILY_API_KEY')})"
```

Expected: `{'OPENSANCTIONS_API_KEY': True, 'OPENROUTER_API_KEY': True, 'TAVILY_API_KEY': True}`

- [ ] **Step 2: Download the UN list**

Run: `python -m ingest.fetch`
Expected: `-> un_sc: https://scsanctions.un.org/resources/xml/en/consolidated.xml`, then an `ok` line with a size of roughly 2 MB and the fetch time. `data/raw/manifest.json` has a `un_sc` entry with a `sha256`.

- [ ] **Step 3: Screen the three starting cases**

Run:

```bash
python -m tradecheck.pipeline "Is KHAWA PANGA MANDRO sanctioned?" "Can we sell to Chief Kahwa?" "Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico?"
```

Expected: the first line shows a `UN records:` count that isn't 0 and a `sanctions data as of:` value that isn't `None`. Then one line per question: `Avoid`, `Caution` and `Clear*`, each with `parsed=model`, `live=True` and `UN=agree`. Review and hit lines show `report=pass/1` (or `pass/2`); a `template` result means the model's citations kept failing, so look at the report with `--full`.

If a line shows `parsed=fallback`, OpenRouter failed: see "OpenRouter answers No endpoints found" in RUN.md. If a line shows `Unknown`, see "Every result is Unknown" in RUN.md.

- [ ] **Step 4: Confirm the two response shapes the spec left open**

Run: `python -m tradecheck.pipeline --full "Is KHAWA PANGA MANDRO sanctioned?"`
Check:
- `as_of` is filled in. If it's `null`, find the field that holds the date with the command below, and put it first in `catalog_as_of()` in `tradecheck/layer1_yente.py`:

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); from tradecheck import http, layer1_yente as y; r = http.request('GET', y.BASE_URL + '/catalog', headers=y._headers()); print([{k: d.get(k) for k in ('name', 'version', 'updated_at', 'last_export')} for d in r.json()['datasets'] if d.get('name') == 'sanctions'])"
```

- `report.sources` has at least one entry with `"kind": "sanction"`. If it has none, the nested sanction entries live under a different property name. List the full record's property names with the command below, and change `"sanctions"` in `build_bundle()` in `tradecheck/layer3.py` to the right name:

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); from tradecheck import audit, parse, layer1_yente as y; audit.init(); top = y.match(parse.extract('Is KHAWA PANGA MANDRO sanctioned?'))['candidates'][0]; print(top['id'], sorted(y.entity(top['id'])['properties']))"
```

If you changed either file, run `python -m pytest` (expected: 112 passed) and repeat Step 3.

- [ ] **Step 5: Pick reliable cases and time them**

If a starting case lands in the wrong band, try other names until each band has a reliable case, about 10 names in all. A primary name from `data/raw/un_sc.xml` makes a good hit, one of its `Low` aliases a good review case, and a made-up business a good clear case. Run each chosen question twice: the band and score must be the same both times. Note the time each line reports and any `HTTP 429` errors (rate limiting on the free key).

- [ ] **Step 6: Check that one failing service doesn't break the demo**

A key set in the shell wins over `.env`, so an invalid key simulates a failing service without editing `.env`. In Git Bash:

```bash
OPENSANCTIONS_API_KEY=invalid python -m tradecheck.pipeline "Is KHAWA PANGA MANDRO sanctioned?"
OPENROUTER_API_KEY=invalid python -m tradecheck.pipeline "Is KHAWA PANGA MANDRO sanctioned?"
```

In PowerShell, set `$env:OPENSANCTIONS_API_KEY = "invalid"` before the command and run `Remove-Item Env:OPENSANCTIONS_API_KEY` after it; do the same for `OPENROUTER_API_KEY`.

Expected: with OpenSanctions failing, `Avoid` with `live=False`, from the response saved in Step 3. (If it shows `Unknown`, the model extracted different fields than in Step 3, so no saved response matched; compare `parsed` in the two audit records.) With OpenRouter failing, `parsed=fallback`, a label of `Avoid` or `Caution` but never `Clear*`, and a `template` or `fixed` report.

- [ ] **Step 7: Walk through the page**

Run: `uvicorn app.main:app --env-file .env`, open http://127.0.0.1:8000, and screen the three chosen questions, typed exactly as in Step 5.
Check:
- Each card shows the expected label, the report with numbered citations whose links open the OpenSanctions entity page, the official source or the article, and the sources list.
- The review case appears in the review queue. Open it: the query and the top candidate sit side by side, with the report and the reasons it's in review.
- Submitting with a blank note shows an error. Then enter a reviewer name and a note, and choose Dismiss or Confirm: "Recorded: ..." appears and the queue empties.
- "View audit record" for that case lists `input`, `layer1`, `layer2`, `layer3`, `final`, `review` and a second `final` with status `reviewed`.

Before the demo, clear the queue by reviewing each live-check case with the note "live check, not a real case". Don't delete `data/tradecheck.sqlite`: it also holds the saved responses the demo falls back to.

- [ ] **Step 8: Record the demo cases**

Fill in the "Demo cases" table in `RUN.md` with each chosen question exactly as typed, its score, its time and any notes (for example, rate limits or a changed field name). If the cases differ from the README's "Demo script", update that section too.

```bash
git add RUN.md README.md tradecheck
git commit -m "docs: record the live-check demo cases" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
