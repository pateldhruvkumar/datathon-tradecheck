"""End to end with every outside service faked: each band, the saved-result fallback,
and the audit events each screen writes."""

import io
import sqlite3
import sys
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
    assert res["cutoffs"] == {"clear_below": 0.70, "hit_at": 0.90}
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
    # The fixed report is logged too, so the audit record holds the text the user saw.
    assert _steps(res) == ["input", "layer1", "layer2", "layer3", "final"]
    assert audit.record(res["screen_id"])[3]["data"] == res["report"]


def test_a_replayed_clear_is_dated_by_its_saved_result(world, monkeypatch):
    # The data date read at startup describes the live collection. A result replayed from
    # the cache is only as recent as the saved answer, so it must not claim the newer date.
    state, routes, _ = world
    state["name"] = "AgroDistribuidora del Bajío SA de CV"
    monkeypatch.setattr(audit, "now", lambda: "2026-10-08T20:00:00+00:00")
    pipeline.screen("Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico?")
    routes["/match/sanctions"] = FakeResponse(503)
    res = pipeline.screen("Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico?")
    assert (res["band"], res["live"], res["as_of"]) == ("clear", False, None)
    assert res["report"]["summary"]["text"] == (
        "No candidate reached 0.70 in the OpenSanctions sanctions collection (US, Canada, EU, UK, "
        "UN and more) as of the saved result from 2026-10-08T20:00:00+00:00, and the UN check agrees.")


def test_unknown_when_yente_is_down_and_nothing_is_saved(world):
    _, routes, calls = world
    routes["/match/sanctions"] = FakeResponse(503)
    res = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    assert (res["band"], res["label"], res["status"], res["live"]) == ("unknown", "Unknown", "unknown", False)
    assert res["report"]["summary"]["text"] == "Live check failed: OpenSanctions answered HTTP 503. Not clear. Retry."
    assert _report_calls(calls) == []
    assert audit.record(res["screen_id"])[1]["data"]["error"] == "OpenSanctions answered HTTP 503"
    assert (res["as_of"], _steps(res)) == (None, ["input", "layer1", "layer2", "layer3", "final"])


def test_the_saved_result_is_used_when_yente_goes_down(world):
    state, routes, _ = world
    state["results"] = [_candidate(0.97)]
    first = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    routes["/match/sanctions"] = FakeResponse(503)
    second = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    assert (second["band"], second["live"], second["fetched_at"]) == ("hit", False, first["fetched_at"])


def test_a_full_outage_replays_the_saved_result(world):
    # At the venue the whole network can drop. The model's saved fields for the same
    # question give the same query, so the saved yente response still matches.
    state, routes, _ = world
    state["results"] = [_candidate(0.97)]
    first = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    routes["openrouter.ai"] = FakeResponse(503)
    routes["/match/sanctions"] = FakeResponse(503)
    second = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    assert (second["parsed"]["source"], second["parsed"]["name"]) == ("saved", "Khawa Panga Mandro")
    assert second["parsed"]["fallback_reason"].startswith("HTTP 503")
    assert (second["band"], second["live"], second["fetched_at"]) == ("hit", False, first["fetched_at"])


def test_a_review_with_no_matcher_candidate_gets_a_fixed_report(world):
    state, _, calls = world
    state["name"] = "KHAWA PANGA MANDRO"  # the UN list has it; the matcher returns nothing
    res = pipeline.screen("Is KHAWA PANGA MANDRO sanctioned?")
    assert (res["band"], res["un_check"]["status"]) == ("review", "disagree")
    assert (res["report"]["check"], _report_calls(calls)) == ("fixed", [])
    assert [i["screen_id"] for i in audit.queue()] == [res["screen_id"]]
    # Opening the case from the queue reads the report from the audit record.
    assert [e["data"] for e in audit.record(res["screen_id"]) if e["step"] == "layer3"] == [res["report"]]


def test_a_layer3_error_keeps_the_band(world, monkeypatch):
    # The report explains the decision; a bug in it must not hide the decision.
    state, _, _ = world
    state["results"] = [_candidate(0.97)]

    def broken(*args):
        raise KeyError("caption")

    monkeypatch.setattr(pipeline.layer3, "report", broken)
    res = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    assert (res["band"], res["label"], res["status"]) == ("hit", "Avoid", "flagged")
    assert (res["report"]["check"], res["report"]["check_reason"]) == ("error", "KeyError: 'caption'")
    assert res["report"]["next_step"] == "Hold and escalate. Don't proceed until reviewed."
    assert _steps(res) == ["input", "layer1", "layer2", "layer3", "final"]


def test_a_fallback_extraction_is_not_clear(world):
    _, routes, _ = world
    routes["openrouter.ai"] = FakeResponse(503)
    res = pipeline.screen("Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico?")
    assert (res["parsed"]["source"], res["band"]) == ("fallback", "review")


def test_no_party_still_closes_the_record_and_raises(world):
    # Every screen ends with a final event, even one with nothing to screen.
    state, _, _ = world
    state["name"] = None
    with pytest.raises(parse.NoParty):
        pipeline.screen("What are the export rules for Mexico?")
    con = sqlite3.connect(audit.DB_PATH)
    rows = con.execute("SELECT step, json_extract(data, '$.error'), json_extract(data, '$.status') FROM events").fetchall()
    con.close()
    assert rows == [("input", "No party name found in the question", None), ("final", None, "not_screened")]


def test_the_command_line_prints_non_latin_names_on_a_windows_console(world, monkeypatch):
    # Windows gives Python a cp1252 stdout when output isn't an interactive console
    # (Git Bash, a pipe), and listed names and aliases are often Cyrillic or Arabic.
    state, _, _ = world
    state["name"], state["results"] = "Хава Панга Мандро", [_candidate(0.97)]
    start = pipeline.start
    monkeypatch.setattr(pipeline, "start", lambda: start(FIXTURE))
    monkeypatch.setattr("dotenv.load_dotenv", lambda *args, **kwargs: None)
    out = io.BytesIO()
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(out, encoding="cp1252"))
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    assert pipeline.main(["--full", "Is Хава Панга Мандро sanctioned?"]) == 0
    sys.stdout.flush()
    assert "Хава Панга Мандро" in out.getvalue().decode("utf-8")


def test_topics_say_what_kind_of_listing_a_candidate_is(world):
    state, _, _ = world
    candidate = _candidate(0.97)
    candidate["properties"]["topics"] = ["sanction", "role.pep", "crime"]
    state["results"] = [candidate, _candidate(0.75)]  # the second has no topics at all
    res = pipeline.screen("Is Khawa Panga Mandro sanctioned?")
    assert [c["topics"] for c in res["candidates"]] == [["sanction", "role.pep", "crime"], []]
    assert res["match_error"] is None


def test_a_failed_match_says_why_so_the_page_can_explain_it(world):
    state, routes, _ = world
    routes["/match/sanctions"] = FakeResponse(402, {"detail": "Your organization has run out of API credits."})
    state["name"] = "Northwind Metals"
    res = pipeline.screen("Can we ship to Northwind Metals?")
    assert (res["band"], res["live"], res["candidates"]) == ("unknown", False, [])
    assert res["match_error"] == "OpenSanctions answered HTTP 402"
