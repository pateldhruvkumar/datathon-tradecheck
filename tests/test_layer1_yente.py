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
