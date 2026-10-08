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
