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
