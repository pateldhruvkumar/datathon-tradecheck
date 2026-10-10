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
