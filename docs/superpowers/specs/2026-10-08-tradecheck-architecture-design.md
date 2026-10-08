# TradeCheck screening architecture: design spec

- **Date:** 2026-10-08
- **Branch:** `dhruv/architecture-oct-08-2026`
- **Source plan:** the workflow diagram "How a question moves through TradeCheck" (`tradecheck-workflow.html`, shared by Dhruv)
- **Status:** design approved in chat, section by section. This document is pending spec review.

## 1. Goal

Build a working demo of the diagram's flow for Build Session 3 (2026-10-09, 6-8 PM, Vancouver).

What the team decided:

- The diagram is the plan. Code on other branches (`dhruv/data-pulled-07-10-2026`, `fix-canada-parser`) is not a constraint, and the old OFAC/Canada/DuckDB pipeline on `main` is replaced.
- Layer 1 uses the hosted OpenSanctions matcher (yente) with the team's free API key.
- The model is `qwen/qwen3.8-27b` on OpenRouter (the team has credits).
- Layer 3 uses plain HTTP calls, not LangChain. Its job is to read everything the APIs return and write a well-structured report for the user. It must retry up to 3 times.
- News search ("adverse media") uses Tavily.
- Code is laid out as one module per diagram box. Two helpers from `main` are reused: `ingest/fetch.py` and `ingest/normalize.py`.

Assumptions accepted during design:

- The full diagram is the target. The ownership walk stays a stretch goal, as the diagram marks it.
- A fourth band, **unknown**, is added. The diagram has no path for a failed check, and the README promises that missing data never shows as clear.
- User-facing labels keep the README's words: hit = Avoid, review = Caution, clear = Clear\*, unknown = Unknown.
- The analyst in the demo is the presenter, working on the same page. There is no login.
- Extracted fields are shown on every result. There is no separate confirm step.
- UI is FastAPI plus one static HTML page.

### Success criteria for the demo

1. A question typed in the UI returns a band, label, status and report, live against OpenSanctions.
2. Three demo cases land in clear, review and hit (picked during the live check in section 9).
3. A review case can be confirmed or dismissed from the review queue, and the decision appears in that screen's audit record.
4. Every screen has a complete audit record covering every step it ran.
5. A clear result makes no Layer 3 model call.
6. The offline test suite passes.
7. The demo survives one external service failing, through saved responses and fallbacks.

## 2. Rules that hold everywhere

1. **The matcher decides; the model explains.** Model output never changes a score, band, label or status.
2. **Missing data is never clear.** A failed match check gives unknown. An unavailable UN check blocks clear.
3. **Every claim in a report cites evidence**, and code verifies each citation before the report is shown.
4. **The audit log is append-only**, enforced by database triggers rather than by application code alone.
5. **Same input, same answer, as far as possible:** the collection, algorithm and threshold are pinned; model calls use temperature 0 and a fixed seed; the model ID and prompt version are logged.

## 3. External services

| Service | Used for | Call | Auth | Cost and limits |
|---|---|---|---|---|
| OpenSanctions (hosted yente) | Layer 1 match; Layer 3 full record; as-of date | `POST https://api.opensanctions.org/match/sanctions?algorithm=logic-v2&threshold=0.7&limit=5` with body `{"queries": {"q": <query>}}`; `GET /entities/{id}`; `GET /catalog` | Header `Authorization: ApiKey <key>` | Free key. Rate limits are not published. |
| OpenRouter | Model call 1 (extract fields) and model call 2 (report) | `POST https://openrouter.ai/api/v1/chat/completions`, model `qwen/qwen3.8-27b` | Header `Authorization: Bearer <key>` | $0.425 per million input tokens, $2.55 per million output tokens. |
| Tavily | News search | `POST https://api.tavily.com/search` with `topic: "news"`, `search_depth: "basic"`, `max_results: 5` | Header `Authorization: Bearer <key>` | 1,000 free searches a month; a basic search costs 1 credit. |
| UN Security Council list | UN cross-check | `https://scsanctions.un.org/resources/xml/en/consolidated.xml`, downloaded ahead of time | None | Free government data. |

The `sanctions` collection already includes `us_ofac_sdn`, `us_ofac_cons`, `ca_dfatd_sema_sanctions`, `eu_fsf`, `gb_fcdo_sanctions` and `un_sc_sanctions`, and OpenSanctions refreshes it four times a day. One match call covers all five jurisdictions the README names.

OpenRouter request settings for both model calls:

- `temperature: 0`, `seed: 42`
- `response_format: {"type": "json_schema", "json_schema": {"name": ..., "strict": true, "schema": ...}}`
- `provider: {"require_parameters": true}`, so the request only goes to providers that honour the JSON schema and seed
- `reasoning: {"effort": "low"}`, to keep the delay down. Measure it in the live check.

## 4. Module layout

```
tradecheck/
  __init__.py
  http.py          request() with 3 retries, backoff 1/2/4 s, OS trust store
  parse.py         Input: question -> query (model call 1)
  layer1_yente.py  match(), entity(), catalog_as_of(); saved-response fallback
  layer1_un.py     load() the UN XML at startup; check() -> agree/disagree/unavailable
  layer2.py        band(): clear / review / hit / unknown
  layer3.py        report(): record + news + evidence bundle + model call 2 + citation check
  audit.py         SQLite append-only events, review queue, saved yente responses
  pipeline.py      screen(question): runs the layers; also a command line
ingest/
  fetch.py         reused; its source list becomes just the UN XML (key "un_sc")
  normalize.py     reused for name cleanup; the unused record dataclasses are removed
app/
  main.py          POST /screen, GET /queue, POST /review/{id}, GET /audit/{id}, GET /health
  static/index.html  question box, result report, review queue panel
.env.example       key names only, no values
```

Deleted: `ingest/parse_ofac.py`, `ingest/parse_canada.py`, `ingest/load.py`, `match/`, `tests/test_parsers.py`, `tests/test_screen.py`, the OFAC and Canada XML fixtures, and `duckdb` from `requirements.txt`. `tests/test_fetch.py` is adapted to the `un_sc` source, and `tests/test_normalize.py` stays. `RUN.md` and the README's build section are rewritten for the new flow.

No new dependencies: `requests`, `rapidfuzz`, `lxml`, `fastapi`, `uvicorn[standard]` (which brings `python-dotenv` for `--env-file`), `truststore` and `pytest` are already in `requirements.txt`. The audit store uses the standard library's `sqlite3`.

## 5. Data flow and contracts

```
question
 -> parse.extract(question)        -> query
 -> layer1_yente.match(query)      -> match
 -> layer1_un.check(query, match)  -> un
 -> layer2.band(match, un)         -> band
 -> layer3.report(query, match, un, band)   (review and hit only) -> report
 -> audit.log(screen_id, step, data)        after every step
```

Every module exposes plain functions that take and return plain dicts, so results go straight into JSON for the audit log and the API.

### 5.1 Input: `parse.extract(question) -> query`

Model call 1 returns JSON matching:

```json
{"schema": "Person | Company | LegalEntity", "name": "string or null",
 "country": "string or null", "birth_date": "string or null", "registration_number": "string or null"}
```

Code validates the result:

- `name` must be non-empty after trimming. If the model returns `null`, there is no party to screen: the API answers `422 No party name found in the question`, and the input event is logged with that error.
- `schema` must be one of the three values; anything else becomes `LegalEntity`.
- `country` must be a 2-letter code. It is lowercased; anything else is dropped.
- `birth_date` must look like `YYYY`, `YYYY-MM` or `YYYY-MM-DD`; anything else is dropped.

Code then builds the yente query:

| schema | properties sent |
|---|---|
| Person | `name`, `country`, `birthDate` |
| Company | `name`, `jurisdiction`, `registrationNumber` |
| LegalEntity | `name`, `country` |

Each value is sent as a one-item list. The returned query dict also carries `source: "model"`.

**Fallback:** if the model call fails after its transport retries, or returns unusable JSON, the raw question text is screened as the name, with `schema: "LegalEntity"` and `source: "fallback"`. The UI says the fields could not be extracted.

### 5.2 Layer 1a: `layer1_yente`

Constants: `COLLECTION = "sanctions"`, `ALGORITHM = "logic-v2"`, `THRESHOLD = 0.7`, `LIMIT = 5`.

`match(query)` returns:

```json
{"candidates": [<full yente result objects: id, caption, schema, score, match,
                 datasets, explanations, properties, last_change>],
 "live": true, "fetched_at": "<ISO-8601 UTC>", "query_hash": "<sha256>"}
```

- `query_hash` is the SHA-256 of the canonical JSON (sorted keys) of the query plus the constants.
- Every successful response is saved in the `yente_cache` table.
- If the live call fails after retries, the saved response is returned with `live: false` and its original `fetched_at`.
- If there is no saved response either, `match` raises `YenteUnavailable`, which Layer 2 turns into unknown.

`entity(id)` calls `GET /entities/{id}` for the full record, including nested sanction entries and linked entities. If it fails, Layer 3 continues using the properties from the match result.

`catalog_as_of()` calls `GET /catalog` once at startup and reads the `sanctions` collection's version or last-updated field. That field name is confirmed in the live check. If it is unavailable, the UI shows only the "checked at" time.

### 5.3 Layer 1b: `layer1_un`

`load(path)` parses every `INDIVIDUAL` and `ENTITY` in the UN XML. For each it keeps the reference number, the primary name (name parts joined), the aliases with their `QUALITY`, `LISTED_ON` and the list type. Names are cleaned with `ingest.normalize.normalize_name`. It runs once at app startup; if the file is missing, every check returns `unavailable`.

`check(query, match)` returns:

```json
{"status": "agree | disagree | unavailable",
 "best": {"ref": "...", "name": "...", "matched_name": "...", "quality": "primary | Good | Low", "score": 0.0},
 "reason": "..."}
```

`best` is `null` when nothing scores. `match` may be `None` (yente unavailable); the check still runs and is logged, treating yente's top score as 0. The band is unknown in that case anyway. Scoring:

- `score = fuzz.WRatio(query_norm, name_norm) / 100 * weight`
- Weights: primary name 1.0, `Good` alias 0.85, `Low` alias 0.60, missing quality 0.85. These come from the README's Oct 3 test on the UN sample.

Disagreement rules:

- **Strong UN match:** `best.score >= 0.90`.
- **Disagree (a):** a strong UN match exists, but yente's top score is below 0.70 (0 when there are no candidates). yente may have missed a UN listing.
- **Disagree (b):** yente's top candidate scores 0.70 or higher and lists `un_sc_sanctions` in its `datasets`, but the UN check's best score is below 0.70. This suggests a delisting or a data lag.
- **Agree:** everything else.

### 5.4 Layer 2: `layer2.band(match, un) -> band`

The pipeline catches `YenteUnavailable` and passes `match=None`. Rules are checked in this order:

```
match is None (yente unavailable)      -> unknown
score = top candidate score, or 0 if there are no candidates
score >= HIT_AT (0.90)                 -> hit
CLEAR_BELOW (0.70) <= score < HIT_AT   -> review
score < CLEAR_BELOW                    -> clear
then: un.status is not "agree" and band is clear -> review
```

A UN disagreement can only raise clear to review. It never lowers a hit.

Returns `{"band", "score", "cutoffs": {"clear_below": 0.70, "hit_at": 0.90}, "reasons": [...]}`. Reasons are readable strings, for example `score 0.83 between 0.70 and 0.90` or `UN check disagrees: UN match "X" scored 0.93 but is not in the yente results`.

Mapping to the user-facing values:

| band | label | status |
|---|---|---|
| clear | Clear\* | `cleared` |
| review | Caution | `pending_review`, then `reviewed` after the analyst decides |
| hit | Avoid | `flagged` |
| unknown | Unknown | `unknown` |

### 5.5 Layer 3: `layer3.report(query, match, un, band) -> report`

Runs for review and hit only. Constants: `MODEL = "qwen/qwen3.8-27b"`, `PROMPT_VERSION = "report-v1"`, `SEED = 42`, `MAX_RETRIES = 3`, `DEADLINE_S = 60`.

Steps:

1. **Full record:** `entity(top.id)`.
2. **News search:** Tavily, `topic: "news"`, `max_results: 5`, with the plain-text query `"<caption>" <country> sanctions OR fraud OR investigation`. The country is left out when unknown.
3. **Evidence bundle.** Every item has a citation key:
   - `NK-...` (the candidate): names, aliases, date of birth, country, datasets, programs, listing dates, source URLs, score and per-feature explanations.
   - The ID of each nested sanction entry: program, authority, start date, source URL.
   - The article URL for each news result: title, excerpt (`content`), `published_date`.
   - `UN:<ref>` for the UN record, if the UN check matched one.
4. **Model call 2.** The system prompt says: use only the bundle; every claim must cite at least one bundle key; state facts and differences only; no verdicts, no recommendations, no legal advice. The bundle and the band are sent as JSON. The model returns:

```json
{
  "summary":        {"text": "...", "cites": ["NK-..."]},
  "who_matched":    [{"text": "...", "cites": ["NK-..."]}],
  "why_it_matched": [{"text": "...", "cites": ["NK-..."]}],
  "differences":    [{"text": "...", "cites": ["NK-..."]}],
  "sanctions":      [{"text": "...", "cites": ["NK-..."]}],
  "news":           [{"text": "...", "cites": ["https://..."]}]
}
```

5. **Citation check (code):** every claim, including the summary, cites at least one key, and every cited key exists in the bundle.
6. **Retries:** if the JSON is invalid or the citation check fails, call the model again and include the list of failures (for example `claim 3 in "sanctions" cites unknown key X`). Up to 3 retries, so at most 4 attempts. These are separate from the transport retries in `http.py`.
7. **Deadline:** once Layer 3 has run for `DEADLINE_S` seconds, no new attempt starts.
8. **Template fallback:** if all attempts fail, or the deadline passes, code builds the same sections from the bundle: name, schema and datasets; the features that matched; the features that don't match; programs and dates; news headlines with URLs. Each line carries its own citation keys, so the template always passes the citation check.

The returned report adds fields the model never writes: `sources` (every cited item with its URL), `next_step`, `check` (`pass`, or `template` with the reason), `attempts`, `model`, `prompt_version`.

`next_step` is fixed text per band:

- review: "An analyst should compare the details and confirm or dismiss this match."
- hit: "Hold and escalate. Don't proceed until reviewed."

The pipeline also builds reports for the other two bands without any model call:

- **clear:** no candidate reached 0.70 in the `sanctions` collection (US, Canada, EU, UK, UN and more) as of the data date, and the UN check agrees.
- **unknown:** "Live check failed: <reason>. Not clear. Retry."

### 5.6 `POST /screen` response

```json
{
  "screen_id": "uuid4",
  "question": "Can we ship to Northwind Metals FZE in Dubai?",
  "parsed": {"schema": "Company", "name": "Northwind Metals FZE", "country": "ae", "source": "model"},
  "band": "review", "label": "Caution", "score": 0.83,
  "status": "pending_review",
  "candidates": [{"id": "NK-...", "caption": "...", "score": 0.83, "datasets": ["..."]}],
  "un_check": {"status": "agree", "best": null},
  "live": true,
  "report": {"summary": {}, "sections": {}, "sources": [], "check": "pass", "attempts": 1},
  "as_of": "...", "checked_at": "...",
  "disclaimer": "Not legal advice. Screening reflects the listed sources as of the dates shown."
}
```

`report.summary` is the summary claim, and `report.sections` holds the five claim lists keyed by name (`who_matched`, `why_it_matched`, `differences`, `sanctions`, `news`).

When `live` is false, the UI shows "Live check failed; showing the saved result from <fetched_at>".

## 6. Audit log and review queue

SQLite file `data/tradecheck.sqlite`, created on app startup. `.gitignore` currently ignores `data/raw/` and `data/*.duckdb`; replace the DuckDB line with `data/*.sqlite`.

```sql
CREATE TABLE IF NOT EXISTS events (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  screen_id TEXT NOT NULL,
  at        TEXT NOT NULL,   -- ISO-8601 UTC
  step      TEXT NOT NULL,   -- input | layer1 | layer2 | layer3 | review | final
  data      TEXT NOT NULL    -- JSON
);
CREATE INDEX IF NOT EXISTS events_screen ON events(screen_id);
CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
  BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
  BEGIN SELECT RAISE(ABORT, 'audit log is append-only'); END;
CREATE TABLE IF NOT EXISTS yente_cache (
  query_hash TEXT PRIMARY KEY, response TEXT NOT NULL, fetched_at TEXT NOT NULL
);
```

What each step's event holds (the diagram's audit column):

| step | data |
|---|---|
| input | question, parsed fields, `source`, model ID, prompt version |
| layer1 | collection, algorithm, threshold, candidates (id, caption, score, datasets), `live`, `fetched_at`, UN result |
| layer2 | band, cutoffs, reasons |
| layer3 | steps run, sources, model ID, prompt version, seed, attempts, check result, report |
| review | reviewer, decision (`confirm` or `dismiss`), note |
| final | status, timestamp |

Functions: `log(screen_id, step, data)`, `record(screen_id)`, `queue()`, `review(screen_id, decision, note, reviewer)`, plus the cache read/write used by `layer1_yente`.

- The diagram's "one row per screen" is `GET /audit/{screen_id}`, which returns that screen's events in order.
- Every screen ends with a `final` event. A review adds a second `final` event; the latest `final` event is the current status, so nothing is ever overwritten.
- **Review queue:** screens whose `layer2` event has band `review` and that have no `review` event yet. This is one SQL query using `json_extract`, with no extra table.
- **`POST /review/{id}`** takes `{"decision": "confirm" | "dismiss", "note": "...", "reviewer": "..."}`. The note and reviewer are required. It writes a `review` event and a `final` event with status `reviewed`, and the decision says whether the match was confirmed (escalate) or dismissed (false positive).

API errors:

| Case | Response |
|---|---|
| No party name in the question | 422 |
| Unknown screen ID | 404 |
| Review on a case that isn't in review | 400 |
| Second review of the same case | 409 |
| Missing note or reviewer | 422 |

## 7. UI (`app/static/index.html`)

One page with no framework, following the existing page's pattern of escaping all text before inserting it.

- **Question box:** a free-text question.
- **Result card:**
  - the label and status stamp, the extracted fields (with a note when the fallback was used) and the score
  - the report sections; each citation links to `https://www.opensanctions.org/entities/<id>/` for entity keys, or to the article URL for news keys
  - the next step, sources, as-of date, checked-at time, disclaimer and a "View audit record" link
- **Review queue panel:** lists pending cases. Opening one shows the query and the top candidate side by side with the report, plus Confirm and Dismiss buttons, a note field and a reviewer-name field.
- **Footer:** "Sanctions data: OpenSanctions, CC BY-NC 4.0. Not legal advice."

## 8. Errors, retries and keys

`http.request(method, url, *, headers, json, params, timeout)`:

- Retries up to 3 times after the first attempt, waiting 1, 2 and 4 seconds.
- Retries only on a timeout, a connection error, HTTP 429 or HTTP 5xx. Any other 4xx error is returned at once.
- Calls `truststore.inject_into_ssl()` once when available, the same approach as `ingest/fetch.py`, so networks that inspect HTTPS still verify. Certificate verification is never turned off.

Timeouts per call: yente 15 s, Tavily 15 s, OpenRouter 40 s.

What happens when each service fails:

| Failure | Behaviour |
|---|---|
| OpenSanctions match | Saved response, labelled with its date; with none saved, the band is unknown |
| OpenSanctions entity lookup | Report built from the match result's properties |
| OpenRouter during extraction | Raw question screened as the name (`source: fallback`) |
| OpenRouter during the report | Template report |
| Tavily | News section says "News search unavailable"; the report continues |
| UN XML missing | UN status unavailable, so the result cannot be clear |

Keys:

- They live in `.env`: `OPENSANCTIONS_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`.
- `.env` is added to `.gitignore`, and `.env.example` lists the names with no values.
- The app runs with `uvicorn app.main:app --env-file .env`.
- Keys are never logged or written to the audit log. The audit log records the model ID, not request headers.

## 9. Live check before the demo (first build step)

Needs the keys in `.env`; the team types them in.

1. `python -m ingest.fetch --only un_sc` downloads the UN XML into `data/raw/`, with its SHA-256 recorded in `manifest.json`.
2. Run about 10 names through `python -m tradecheck.pipeline` against the live services. Starting names: `KHAWA PANGA MANDRO` (expected hit), `Chief Kahwa` (expected review), `AgroDistribuidora del Bajío SA de CV` (expected clear). Add names until each band has a reliable case.
3. Confirm the `/catalog` field used for the as-of date and the shape of the nested `/entities` record.
4. Record the delay for each band, and note any rate limiting on the free key.
5. These runs fill `yente_cache`, so the demo cases can be replayed if the venue network fails.

## 10. Testing

pytest, fully offline: `http.request` is replaced by a fake that returns recorded responses.

| Test file | What it proves |
|---|---|
| `test_layer2.py` | The full band table; a UN disagreement only raises clear to review; unknown when the match is unavailable |
| `test_layer1_un.py` | A small UN XML fixture with a `Low` alias: alias weights; disagree cases (a) and (b); unavailable when there is no file |
| `test_layer3.py` | Citation check pass and fail; exactly 3 retries, then the template; the template passes its own check |
| `test_http.py` | 3 retries on 503; no retry on 401 |
| `test_audit.py` | UPDATE and DELETE are rejected by the triggers; the queue skips reviewed cases; a second review is refused |
| `test_parse.py` | Validation drops a bad country and date; fallback on a model failure; null name gives the no-party error |
| `test_pipeline.py` | End to end with fake services for clear, review, hit and unknown, including the audit events written |
| `test_fetch.py` | Adapted to the `un_sc` source |
| `test_normalize.py` | Unchanged |

## 11. Out of scope for the demo

- **Ownership walk (OFAC 50% Rule):** stretch goal, built only if time remains.
- **Tuning the cutoffs from a labelled test set:** 0.70 and 0.90 stay as placeholders until one exists.
- **A LangChain agent loop:** not needed while Layer 3 runs in a fixed order.
- **Self-hosting yente:** the flow would not change; only the base URL would.
- **Logins, multiple users, and exported records** such as a PDF receipt.
- **Commercial use:** OpenSanctions data is CC BY-NC 4.0, and a paid licence is needed before any.

## 12. Risks

| Risk | Mitigation |
|---|---|
| Free OpenSanctions key rate limits are unknown | Saved responses; demo cases run beforehand to fill the cache |
| Venue network inspects HTTPS | `truststore`, as in `ingest/fetch.py` |
| Reasoning model is slow | Low reasoning effort, 60 s Layer 3 deadline, template fallback |
| Demo cases don't land in the intended bands | The live check picks the cases |
| Counterparty names are sent to OpenSanctions, OpenRouter and Tavily | Acceptable for the demo; must be disclosed before real users |
| The UN XML is fetched once while yente updates four times a day | Disagreement rule (b) surfaces the lag as a review case instead of hiding it |
