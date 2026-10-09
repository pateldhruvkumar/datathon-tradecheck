"""Input step: turn a free-text question into the yente query (model call 1).

The model only extracts fields; code validates every one of them. If the model call
fails or its JSON is unusable, the whole question is screened as the name.
"""

from __future__ import annotations

import json
import re

from tradecheck import http

PROMPT_VERSION = "extract-v1"
SCHEMAS = ("Person", "Company", "LegalEntity")
NO_PARTY = "No party name found in the question"
_COUNTRY = re.compile(r"[a-z]{2}")              # ISO 3166-1 alpha-2, after lowercasing
_DATE = re.compile(r"\d{4}(-\d{2}(-\d{2})?)?")  # YYYY, YYYY-MM or YYYY-MM-DD

SYSTEM = """You extract the counterparty from a question about a trade deal.
Return the one person or organisation the user wants to deal with:
- schema: "Person" for a human, "Company" for a business, "LegalEntity" if unsure.
- name: the party's name exactly as written, without the rest of the question.
- country: the party's country as an ISO 3166-1 alpha-2 code (e.g. "ae"), only if stated.
- birth_date: YYYY, YYYY-MM or YYYY-MM-DD, only if stated.
- registration_number: a company registration number, only if stated.
Use null for anything not stated. If the question names no party, name is null. Never guess."""

JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "schema": {"type": "string", "enum": list(SCHEMAS)},
        "name": {"type": ["string", "null"]},
        "country": {"type": ["string", "null"]},
        "birth_date": {"type": ["string", "null"]},
        "registration_number": {"type": ["string", "null"]},
    },
    "required": ["schema", "name", "country", "birth_date", "registration_number"],
    "additionalProperties": False,
}


class NoParty(Exception):
    """The question names no party to screen (the API answers 422)."""


def extract(question: str) -> dict:
    """Return the cleaned fields, where they came from, and the yente properties:
    {"schema", "name", "country", "birth_date", "registration_number",
     "source": "model" | "fallback", "fallback_reason", "properties"}."""
    question = question.strip()
    if not question:
        raise NoParty(NO_PARTY)
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    try:
        fields = _clean(json.loads(http.chat(messages, "query", JSON_SCHEMA)))
        source, reason = "model", None
    # A failed call or unusable JSON falls back to screening the whole question. NoParty is
    # deliberately not caught: a question that names nobody should fail, not be screened.
    except (http.ModelError, ValueError) as err:
        fields = {"schema": "LegalEntity", "name": question, "country": None,
                  "birth_date": None, "registration_number": None}
        source, reason = "fallback", str(err)
    return {**fields, "source": source, "fallback_reason": reason, "properties": _properties(fields)}


def _clean(raw) -> dict:
    """Validate the model's fields. Raises NoParty when it found no name."""
    if not isinstance(raw, dict):
        raise ValueError("the model's answer is not a JSON object")
    name = _text(raw.get("name"))
    if not name:
        raise NoParty(NO_PARTY)
    country = _text(raw.get("country")).lower()
    birth_date = _text(raw.get("birth_date"))
    return {
        "schema": raw.get("schema") if raw.get("schema") in SCHEMAS else "LegalEntity",
        "name": name,
        "country": country if _COUNTRY.fullmatch(country) else None,
        "birth_date": birth_date if _DATE.fullmatch(birth_date) else None,
        "registration_number": _text(raw.get("registration_number")) or None,
    }


def _text(value) -> str:
    """The trimmed string, or "" for null or anything that isn't text."""
    return value.strip() if isinstance(value, str) else ""


def _properties(fields: dict) -> dict:
    """The FollowTheMoney properties sent to yente. Each value is a one-item list, and
    empty fields are left out so yente only compares what the user actually gave."""
    extra = {
        "Person": {"country": fields["country"], "birthDate": fields["birth_date"]},
        "Company": {"jurisdiction": fields["country"], "registrationNumber": fields["registration_number"]},
        "LegalEntity": {"country": fields["country"]},
    }[fields["schema"]]
    return {"name": [fields["name"]], **{key: [value] for key, value in extra.items() if value}}
