"""Model call 1 and the code that checks it. OpenRouter is faked."""

import pytest

from tests.fakes import FakeResponse, chat_reply
from tradecheck import parse


def _model(fake_http, answer):
    routes, calls = fake_http
    routes["openrouter.ai"] = answer
    return calls


def _fields(schema="Person", name="Khawa Panga Mandro", country=None, birth_date=None, registration_number=None):
    return {"schema": schema, "name": name, "country": country,
            "birth_date": birth_date, "registration_number": registration_number}


def test_person_fields_become_yente_properties(fake_http):
    _model(fake_http, chat_reply(_fields(name=" Khawa Panga Mandro ", country="CD", birth_date="1974-08-20")))
    q = parse.extract("Is Khawa Panga Mandro, born 1974-08-20 in Congo, on a list?")
    assert (q["source"], q["fallback_reason"]) == ("model", None)
    assert (q["schema"], q["name"], q["country"], q["birth_date"]) == ("Person", "Khawa Panga Mandro", "cd", "1974-08-20")
    assert q["properties"] == {"name": ["Khawa Panga Mandro"], "country": ["cd"], "birthDate": ["1974-08-20"]}


def test_company_uses_jurisdiction_and_registration_number(fake_http):
    _model(fake_http, chat_reply(_fields("Company", "Northwind Metals FZE", "ae", registration_number="DMCC-123")))
    q = parse.extract("Can we ship to Northwind Metals FZE (DMCC-123) in Dubai?")
    assert q["properties"] == {"name": ["Northwind Metals FZE"], "jurisdiction": ["ae"],
                               "registrationNumber": ["DMCC-123"]}


def test_bad_schema_country_and_date_are_dropped(fake_http):
    _model(fake_http, chat_reply(_fields("Vessel", "Northwind", "UAE", "20 Aug 1974", " ")))
    q = parse.extract("Northwind")
    assert (q["schema"], q["country"], q["birth_date"], q["registration_number"]) == ("LegalEntity", None, None, None)
    assert q["properties"] == {"name": ["Northwind"]}


@pytest.mark.parametrize("answer", [FakeResponse(500), FakeResponse(401), chat_reply("not json"), chat_reply([1, 2])])
def test_model_failure_screens_the_whole_question(fake_http, answer):
    _model(fake_http, answer)
    q = parse.extract("  Can we ship to Northwind Metals FZE?  ")
    assert q["source"] == "fallback" and q["fallback_reason"]
    assert q["schema"] == "LegalEntity"
    assert q["properties"] == {"name": ["Can we ship to Northwind Metals FZE?"]}


def test_null_name_means_no_party(fake_http):
    _model(fake_http, chat_reply(_fields("LegalEntity", None)))
    with pytest.raises(parse.NoParty, match="No party name found in the question"):
        parse.extract("What are the export rules for Mexico?")


def test_blank_question_makes_no_model_call(fake_http):
    calls = _model(fake_http, chat_reply(_fields()))
    with pytest.raises(parse.NoParty):
        parse.extract("   ")
    assert calls == []
