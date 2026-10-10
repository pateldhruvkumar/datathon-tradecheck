# Handoff: integrating the TradeCheck UI and backend

Branch: `mansi/web_ui-oct-09-2026` (written 2026-10-10). Read this before picking the work up.

## Summary

There was never a separate UI to merge. The new single-page UI (`app/static/index.html`)
and the backend (`tradecheck/` package, `app/main.py`) both live on `origin/main` and were
built together by Dhruv in PRs #1-#3. Mansi's local `main` was 28 commits behind and the
local `mansi/web_ui` branch had no commits of its own.

This branch is `origin/main` (fast-forwarded to `a24b70b`) plus one small change set
(see "What this branch changes").

## Branch survey (all remotes fetched on 2026-10-10)

| Branch | State | Action |
| --- | --- | --- |
| `origin/main` | Contains everything below, merged PRs #1-#3 | Base of this branch |
| `origin/dhruv/architecture-oct-08-2026` | Fully merged into main | None |
| `origin/dhruv/data-pulled-07-10-2026` | Same commit as the old local main (`d10a5a2`) | None |
| `origin/fix-canada-parser` | Fixes the OFAC/Canada pipeline that main has since **deleted** | Obsolete, do not merge |

Anything not on a remote could not be found inside the repo. A search of the wider
home folder for another copy of the UI was declined, so if a UI exists outside this
repo it has **not** been looked at.

## What this branch changes (on top of origin/main)

1. `ingest/normalize.py`: `fold_accents` now uses `anyascii`, so non-Latin names are
   transliterated (Людмила -> Lyudmila) instead of being dropped. This was Mansi's
   uncommitted local change, re-applied onto the slimmed-down file.
2. `requirements.txt`: adds `anyascii>=0.3`.
3. `tests/test_layer1_un.py`: one test encoded the old limitation ("a Cyrillic name is
   unavailable"). With transliteration the UN check now correctly matches
   `Хава Панга Мандро` to the UN-listed KHAWA PANGA MANDRO and reports `disagree`
   (the matcher had nothing). The test was replaced by two: Cyrillic is matched, and a
   name with no letters at all (`!!! ???`) is still `unavailable`.

Dhruv should confirm he is happy with item 1 and 3: it changes documented behaviour of
the UN cross-check for non-Latin names.

## Verified

- Offline test suite: `python -m pytest` -> **121 passed** (fakes only, no network).
- `python -m ingest.fetch` downloaded the UN list (`data/raw/un_sc.xml`, 2,186,496 bytes).
- App started cleanly with `uvicorn app.main:app --env-file .env` and, by API:
  - `GET /health` -> 200 `{"status":"ok"}`
  - `GET /queue` -> 200 `[]`
  - `GET /` -> 200, 22,426 bytes, identical size to `app/static/index.html`
  - `POST /screen` with a blank question -> 422 (validation)
  - `POST /screen` with "What is the weather today?" -> 422 "No party name found in the question"
  - `GET /audit/nope` -> 404; `POST /review/nope` -> 404; invalid `decision` -> 422

## NOT verified (still to do)

- **Live screens.** The three demo cases in `RUN.md` (Avoid / Caution / Clear\*) were not
  run through `/screen` in this session, so the OpenSanctions, OpenRouter and Tavily
  calls, the Layer 3 report and the saved-response fallback were not exercised here.
- **Review flow.** `/review/{id}` was only tested for its error paths. A real Caution
  case going to `/queue`, being confirmed/dismissed, and showing up in `/audit/{id}` has
  not been walked through.
- **Visual inspection.** No browser run of the UI was done: layout, the report view,
  the review queue panel, the Enter-key behaviour, and the band colours still need an
  eyeball pass (desktop and a narrow window).

## Gotchas

- Port 8000 was already in use on Mansi's laptop by an unknown process answering as
  TradeCheck, possibly an old instance. It was left alone; this branch was tested on
  port 8010. Check what is on 8000 before you trust it.
- `.env` is gitignored. Copy `.env.example` to `.env` and fill in the three keys
  (`OPENSANCTIONS_API_KEY`, `OPENROUTER_API_KEY`, `TAVILY_API_KEY`). A previous
  `app/.env` exists on Mansi's laptop only; `app/main.py` expects the file at the repo
  root (`--env-file .env`).
- `data/` is gitignored. `data/raw/` on Mansi's laptop still holds old OFAC and Canada
  files from the retired pipeline, and `data/tradecheck.duckdb` is a leftover; neither
  is used by the current code. Audit data is `data/*.sqlite`.
- Each live screen spends API credits (see `RUN.md`).
- A git stash `mansi-local-anyascii` exists in Mansi's local clone as a backup of the
  original uncommitted changes; it is not needed now that they are re-applied.
- `.refact/` (an editor tool's folder) is untracked and deliberately not committed.

## Suggested next steps

1. Pull the branch, `pip install -r requirements.txt`, create `.env`, run the tests.
2. `python -m ingest.fetch`, start the app, and run the three demo questions from `RUN.md`
   in the browser and via `curl`, checking each against the table there.
3. Work a Caution case through the review queue and confirm the audit log records it.
4. Do the visual pass listed above, then open a PR into `main`.

---

## Part 2: UI redesign (`design/preview.html`) and how to integrate it

**Status: designed, not implemented.** `app/static/index.html` is still Dhruv's page. The
redesign exists only as a static mock, now saved in the repo as
[`design/preview.html`](design/preview.html) (kept outside `app/static` so the server does
not serve it). Open it directly in a browser to see the target look.

### Why it can't be dropped in

`preview.html` was built against the **old** backend (the OFAC/Canada pipeline that `main`
deleted). It talks to a contract that no longer exists:

| | `design/preview.html` | Current backend |
| --- | --- | --- |
| Request to `POST /screen` | `{ "name": ... }` | `{ "question": ... }` |
| Result fields | `verdict`, `hits[]`, `lists_screened`, `query` | `band`, `label`, `score`, `reasons`, `status`, `candidates`, `un_check`, `report`, `parsed`, `live`, `fetched_at`, `as_of`, `checked_at`, `screen_id`, `disclaimer` |
| `GET /stats` (list counts) | used by the loader and the "voyage" strip | does not exist |
| Review queue, `/review`, `/audit` | absent | part of the app |
| Data | hard-coded `MOCK` object and a "Preview using saved results" banner | live |

### Design decisions (what to keep, change, drop)

Keep the visual language of the preview, and keep Dhruv's behaviour and safety rules.

**Keep from the preview (look and feel):**
- Palette and type: iris accent, Fraunces (headings) and Figtree (body) via Google Fonts,
  the CSS variables in `:root`, card/shadow style.
- Header/brand, hero with the animated trade-route SVG, the large ask bar.
- The verdict as a rotated **stamp** inside a `.record` card, coloured by band.
- Candidates as expandable match cards with a score meter.
- "One name / Several names" tabs; keyboard: `/` focuses search, `Esc` clears.
- `prefers-reduced-motion` and print styles.

**Keep from Dhruv's `index.html` (behaviour, do not regress):**
- Request body is `{question}`. The ask bar placeholder is a question
  ("Can we ship to Northwind Metals FZE in Dubai?").
- Colour is keyed by `band`, not `label` (map `hit -> avoid`, `review -> review`,
  `clear -> clear`, `unknown -> unknown`) so renaming a label cannot turn the card grey.
  Reuse `CLASS`, `LINE`, `DATASETS`, `GROUPS`, `listsHtml`, `candidateHtml`, `claimsHtml`,
  `reportHtml`, `fieldsHtml`, `api` verbatim; they are tested by hand against the API.
- `esc()` on every server string before `innerHTML`; `safeUrl()` so only http(s) become hrefs.
- Missing data shows as **Unknown**, never green. A replayed result shows the "Live check
  failed; showing the saved result from ..." note. `as_of` is only shown when present.
- The review decision uses plain buttons, **not a form**, so Enter in a field can never submit
  an irreversible decision. Keep that.
- Show every audit link: `/audit/{screen_id}`.
- Show the three note cases: `parsed.source` of `fallback` or `saved`, `report.check` of
  `error` or `template`, and `steps.news == "unavailable"`.

**Change:**
- "Name" input becomes a question box; the model extracts the party. Show the extracted
  fields (`parsed`: schema, name, country, birth_date, registration_number) as the
  "What we screened" read-back under the ask bar, replacing the preview's client-side token
  read-back (which depended on old normalization).
- The "voyage" strip (ship sailing past one buoy per list) is repurposed from "lists" to the
  **three layers**: Matcher -> UN check -> Report. While a screen is in flight the ship
  advances on a timer and is labelled as indicative (a screen takes 4-40 s, there is no
  progress feed). When the result arrives, set each buoy from the real response:
  matcher = band and score (and "saved result" when `live` is false), UN check =
  `un_check.status` (agree / disagree / unavailable), report = `report.check`
  (`pass`, `template`, `error`, `fixed`).
- Batch mode ("Several names"): call `POST /screen` **sequentially**, one line per question.
  Each call spends credits and can take up to ~40 s, so cap the list (suggest 10) and say so
  in the UI. Clicking a row shows its full result.
- Score meter ticks at **0.70** (review) and **0.90** (hit). These are
  `layer2.CLEAR_BELOW` and `layer2.HIT_AT`; read them from `band.cutoffs` if exposed,
  otherwise keep in sync by hand.
- Footer: Dhruv's wording (OpenSanctions CC BY-NC 4.0, not legal advice). The preview's
  "US and Canadian lists only" footer is no longer true; the matcher covers many lists.
- Sample-question chips use the three demo questions in `RUN.md`, not the mock data.

**Drop:**
- The `MOCK` / `STATS` objects, the mock banner and every `fetch("/stats")`.
- The boot loader that waited for `/stats` (there is nothing to wait for; `/health` is
  instant). If a loader is still wanted, tie it to the in-flight `/screen` only.
- The copy-to-clipboard of old `hits[]`; rebuild it from the new result if wanted.

**Add (missing from the preview, required for one app):**
- Review queue panel: `GET /queue`, "Open" -> `GET /audit/{id}` to rebuild the case (query,
  top candidate, reason, report), then `POST /review/{id}` with `decision`, `note`,
  `reviewer`. Show 400 / 404 / 409 and 422 messages as `api()` already does.
- Refresh the queue after every screen and after every decision.

### Dark mode

Dhruv's page follows `prefers-color-scheme: dark`; the preview is light only (several
colours are hard-coded, e.g. `#efeef6`, `#ebeaf5`). Decide whether to keep dark mode. If so,
move those hard-coded colours into variables first.

### Suggested way to build it

1. Start from `design/preview.html` for markup and CSS, and paste Dhruv's script helpers in.
2. Replace its data layer with `api("/screen", {question})`, `api("/queue")`, `api("/audit/..")`,
   `api("/review/..")`.
3. Test **without credits**: run the offline tests; for the UI, start uvicorn with
   `pipeline.screen` monkey-patched or with saved responses (see `tests/fakes.py` and
   `tests/conftest.py`) so no live call is made. Check Avoid, Caution, Clear\*, Unknown,
   a replayed result, a failed report and an empty and a non-empty queue.
4. Check desktop (1024 px) and phone (375 px) widths, keyboard-only use, and Enter in the
   review note field not submitting.
5. Keep `tests/test_app.py` green; the page is plain static HTML so no test changes are needed
   unless endpoints change.

### Open questions for Dhruv

- Is it fine for the UN check to transliterate non-Latin names (Part 1, item 1)?
- Should `/screen` also return the cutoffs (`band.cutoffs`) so the UI need not hard-code 0.70
  and 0.90? Small change in `tradecheck/pipeline.py`.
- Is a `/stats`-style endpoint wanted (UN list date, record count, `as_of`)? The UI already
  gets `as_of` and `un_check.list_date` per screen, so probably not.

---

## Part 3: update, Dhruv's newer UI is now merged (read this before Part 2)

After Part 2 was written, `origin/dhruv/ui-fix-oct-09-2026` (commit `b6ff11f`, "rebuild the
page as a plain answer for business owners") turned up. It was **not** in `main`, so the
branch was missing the latest UI. It is now merged into this branch (merge `f8ad1fb`); no
conflicts, 121 tests pass.

What changed because of it:

- `app/static/index.html` is now Dhruv's rebuilt page (about 34 KB), **not** the 22 KB page
  Part 2 describes. It is the live UI and the one to extend. It keeps `api()`, the
  `CLASS`/`LINE`/`SECTIONS`/`DATASETS`/`GROUPS` tables, `listsHtml`, `candidateHtml`,
  `reportHtml`, `fieldsHtml`, `loadQueue` and `openCase`, and adds `headline`,
  `closenessHtml`, `tilesHtml` (the jurisdiction tiles), `datesText`, a "What to do next"
  list, a "Proof for your records" block, the "four answers you can get" explainer and a
  `STEPS` table. It still follows `prefers-color-scheme: dark`.
- `POST /screen` now also returns **`cutoffs`** (`clear_below`, `hit_at`) from
  `tradecheck/pipeline.py`. This answers the open question in Part 2: do not hard-code 0.70
  and 0.90 in the score meter; read `res.cutoffs`. The new page already does.
- Part 2's "Keep from Dhruv's index.html" list still applies in spirit, but the file names
  above are the current ones. Re-read the new page before porting; where Part 2 and the
  code disagree, trust the code.

### Where the work stands now

1. **Backend and Dhruv's UI are integrated and in sync** on this branch (all remotes fetched
   on 2026-10-10; `origin/main` is `a24b70b` and has nothing newer).
2. **The `design/preview.html` redesign is still not implemented.** Decision needed: either
   (a) restyle Dhruv's new page with the preview's look (palette, fonts, stamp, route map)
   and keep his information design, which is the smaller and safer change, or (b) rebuild
   the layout from the preview. Option (a) is recommended: Dhruv's page now carries product
   decisions (the "four answers", next steps, proof block) that the preview does not have.
3. Still unverified: live `/screen` runs for the three demo cases, the review flow end to
   end, and a visual pass of the **new** page in a browser (desktop and phone width).
   Earlier visual checks were of the older page.

### Branches to know about

| Branch | Note |
| --- | --- |
| `origin/main` | `a24b70b`, base of this work |
| `origin/dhruv/ui-fix-oct-09-2026` | merged here; not yet in `main` (open a PR from this branch or Dhruv's) |
| `origin/mansi/web_ui-oct-09-2026` | this branch |
| `origin/fix-canada-parser` | obsolete, targets the deleted OFAC/Canada pipeline |
