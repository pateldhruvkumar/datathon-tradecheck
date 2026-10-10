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


def _unset(monkeypatch, *names):
    """Unset variables so monkeypatch also removes whatever the test loads into them."""
    for name in names:
        monkeypatch.setenv(name, "x")  # records "was unset" so teardown deletes it again
        monkeypatch.delenv(name)


def test_load_env_reads_the_keys_without_overriding_the_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENSANCTIONS_API_KEY=from-file\nOPENROUTER_API_KEY=from-file\n")
    _unset(monkeypatch, "OPENSANCTIONS_API_KEY", "TAVILY_API_KEY")
    monkeypatch.setenv("OPENROUTER_API_KEY", "already-set")
    missing = main.load_env(env_file)
    assert main.os.environ["OPENSANCTIONS_API_KEY"] == "from-file"
    assert main.os.environ["OPENROUTER_API_KEY"] == "already-set"  # the real environment wins
    assert missing == ["TAVILY_API_KEY"]


def test_load_env_with_no_file_names_every_missing_key(tmp_path, monkeypatch):
    _unset(monkeypatch, *main.KEYS)
    assert main.load_env(tmp_path / "missing.env") == list(main.KEYS)
