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
# ponytail: no index covers step or the JSON band, so this reads every event; fine at demo
# size, add an index on (step, json_extract(data, '$.band')) if the log grows large.
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
    """The current UTC time as ISO-8601, to the second. Every timestamp in the log uses it."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def _db():
    """A short-lived connection for one unit of work."""
    # ponytail: a fresh connection per call (5-7 per screen) keeps FastAPI's worker threads
    # from sharing one; reuse a connection per screen if connecting ever shows up in a profile.
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
    # Always a plain INSERT, never INSERT OR REPLACE: REPLACE would delete a clashing row
    # (an earlier review) without firing the no-delete trigger.
    con.execute(
        "INSERT INTO events (screen_id, at, step, data) VALUES (?, ?, ?, ?)",
        (screen_id, now(), step, json.dumps(data, ensure_ascii=False)),
    )


def log(screen_id: str, step: str, data: dict) -> None:
    """Append one event. Each step writes its own after it runs; nothing is changed later."""
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
    """The cases waiting for an analyst, oldest first."""
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
    # Two analysts can both pass the checks above at once. The one_review_per_screen index
    # is what stops the second: its insert fails here, and its transaction writes nothing.
    try:
        with _db() as con:
            _insert(con, screen_id, "review", {"reviewer": reviewer, "decision": decision, "note": note})
            _insert(con, screen_id, "final", final)
    except sqlite3.IntegrityError as err:  # the one_review_per_screen index
        raise AlreadyReviewed("This case has already been reviewed") from err
    return final


def saved_extraction(question: str) -> dict | None:
    """The fields the model last extracted for this exact question, or None."""
    # ponytail: scans every input event; only the fallback path runs it, so that's fine for now.
    with _db() as con:
        row = con.execute(
            "SELECT json_extract(data, '$.parsed') FROM events WHERE step = 'input' "
            "AND json_extract(data, '$.question') = ? AND json_extract(data, '$.source') = 'model' "
            "ORDER BY id DESC LIMIT 1", (question,)
        ).fetchone()
    return None if row is None else json.loads(row[0])


def cache_put(query_hash: str, candidates: list, fetched_at: str) -> None:
    """Save the latest yente answer for a query. REPLACE is fine here: yente_cache is a
    cache, not the audit log, so only the newest answer matters."""
    with _db() as con:
        con.execute(
            "INSERT OR REPLACE INTO yente_cache (query_hash, response, fetched_at) VALUES (?, ?, ?)",
            (query_hash, json.dumps(candidates, ensure_ascii=False), fetched_at),
        )


def cache_get(query_hash: str) -> dict | None:
    """The saved yente answer for a query, {"candidates", "fetched_at"}, or None."""
    with _db() as con:
        row = con.execute(
            "SELECT response, fetched_at FROM yente_cache WHERE query_hash = ?", (query_hash,)
        ).fetchone()
    return None if row is None else {"candidates": json.loads(row[0]), "fetched_at": row[1]}
