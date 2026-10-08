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
