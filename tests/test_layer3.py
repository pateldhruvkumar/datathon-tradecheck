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
    assert (rep["model"], rep["prompt_version"], rep["seed"]) == ("qwen/qwen3.8-27b", "report-v3", 42)
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
    assert {"text": "Born: 1974-08-20; country: cd.", "cites": ["NK-kpm"]} in rep["sections"]["who_matched"]
    assert rep["next_step"] == "An analyst should compare the details and confirm or dismiss this match."


def test_a_replayed_match_skips_the_entity_lookup(services, fake_http):
    # OpenSanctions failed moments ago, so asking it for the full record would only add
    # more failed calls and backoff. The saved match's properties are used instead.
    _, calls = fake_http
    _model(services, chat_reply({**GOOD, "sanctions": []}))  # no sanction entries without the record
    rep = layer3.report(QUERY, {**MATCH, "live": False}, UN, HIT)
    assert (rep["steps"]["entity"], rep["check"]) == ("skipped", "pass")
    assert not [url for _, url, _ in calls if "/entities/" in url]


@pytest.mark.parametrize("record, news", [(RECORD, NEWS), (None, [])])
def test_the_template_always_passes_its_own_check(record, news):
    bundle = layer3.build_bundle(TOP, record, news, UN)
    assert layer3.check_citations(layer3.template(QUERY, bundle, "NK-kpm"), bundle) == []


def test_template_content():
    t = layer3.template(QUERY, _bundle(), "NK-kpm")
    assert t["summary"]["text"] == "Khawa Panga Mandro (Person) matched with score 0.95 and is on 2 lists."
    assert [c["text"] for c in t["who_matched"]] == [
        "Listed as Khawa Panga Mandro (Person), also known as Chief Kahwa.",
        "Born: 1974-08-20; country: cd.",
    ]
    # Plain words, and the UN record as its own line citing only the UN entry.
    assert t["why_it_matched"] == [
        {"text": "The name you gave is the same as a listed name, letter for letter.", "cites": ["NK-kpm"]},
        {"text": "The UN Security Council list has KHAWA PANGA MANDRO as its main name under entry CDi.000, "
                 "which the UN check scored 1.00 against the name you gave.", "cites": ["UN:CDi.000"]},
    ]
    # The country_mismatch penalty is left out: the country line already says it, with both values.
    assert [c["text"] for c in t["differences"]] == ["You gave country ug; the listing shows cd."]
    assert t["sanctions"][0] == {"text": "DRCONGO by Office of Foreign Assets Control since 2005-11-01.",
                                 "cites": ["ofac-s-1"]}
    assert t["news"] == [{"text": "Militia leader named in report (2026-09-01).", "cites": ["https://news.example/a"]}]


def test_template_with_only_a_name():
    query = {**QUERY, "properties": {"name": ["Khawa Panga Mandro"]}}
    top = {**TOP, "explanations": {}}
    t = layer3.template(query, layer3.build_bundle(top, None, [], {"best": None}), "NK-kpm")
    assert [c["text"] for c in t["why_it_matched"]] == ["The name you gave was compared with the listed names of Khawa Panga Mandro."]
    assert [c["text"] for c in t["differences"]] == [
        "Only a name was given, so the listing's birth date and country could not be checked against it."]
    assert [c["text"] for c in t["sanctions"]] == ["Listed in: un_sc_sanctions, us_ofac_sdn."]


def test_template_gives_every_identity_and_sanction_detail_the_record_has():
    sanction = {"id": "eu-s-1", "properties": {
        "program": ["COD"], "authority": ["EU Council"], "startDate": ["2005-11-01"],
        "provisions": ["Asset freeze", "Travel ban"], "authorityId": ["EU.1234.56"],
        "status": ["Active"], "reason": ["Former President of PUSIC."]}}
    record = {**TOP, "properties": {**TOP["properties"], "birthPlace": ["Bunia"], "gender": ["male"],
                                    "passportNumber": ["OB0123456"], "sanctions": [sanction]}}
    t = layer3.template(QUERY, layer3.build_bundle(TOP, record, [], {"best": None}), "NK-kpm")
    assert [c["text"] for c in t["who_matched"]][1:] == [
        "Born: 1974-08-20; birthplace: Bunia; country: cd.", "Gender: male.", "Passport number: OB0123456."]
    assert t["sanctions"] == [{"text": "COD by EU Council since 2005-11-01; measures: Asset freeze, Travel ban; "
                                       "reference: EU.1234.56; status: Active; reason: Former President of PUSIC.",
                               "cites": ["eu-s-1"]}]
