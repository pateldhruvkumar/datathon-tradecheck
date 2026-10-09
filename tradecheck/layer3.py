"""Layer 3, for review and hit cases only: gather the evidence, have the model write
the report, and check every citation in code.

The model never changes the band. If its report still fails the citation check
after MAX_RETRIES retries, or the deadline passes, code writes the report from the
evidence instead (the template), and that report always passes the check.
"""

from __future__ import annotations

import json
import os
import time

import requests

from tradecheck import http, layer1_un, layer1_yente

PROMPT_VERSION = "report-v3"
MAX_RETRIES = 3   # after the first attempt, so at most 4 model calls
DEADLINE_S = 60   # no new attempt starts after this
TAVILY_URL = "https://api.tavily.com/search"
TAVILY_TIMEOUT_S = 15
ENTITY_URL = "https://www.opensanctions.org/entities/{}/"
SECTIONS = ("who_matched", "why_it_matched", "differences", "sanctions", "news")
NEXT_STEP = {
    "review": "An analyst should compare the details and confirm or dismiss this match.",
    "hit": "Hold and escalate. Don't proceed until reviewed.",
}

SYSTEM = """You write the evidence report for one sanctions screening result.
The matcher has already decided the band. You explain the evidence; you never judge it.
Rules:
- Use only the evidence bundle. No outside knowledge, not even to make a sentence fuller.
- Every claim cites at least one bundle key in "cites", copied exactly. Cite only the keys whose evidence shows that claim.
- State facts and differences only. No verdicts, no recommendations, no legal advice.
- Say each fact once in the whole report. A fact that belongs to one section stays out of the others.
- Write each claim as one full, plain sentence for a compliance reviewer. Join related facts in one sentence
  instead of splitting them, for example a name with its aliases, or a birth date with the birthplace and nationality.
- Never use matcher feature names such as name_literal_match. Say in plain words what they found.
Sections:
- summary: one sentence on who matched, how strongly (give the score) and how many authorities list them.
  No role, aliases or reference numbers.
- who_matched: who the listed party is: names and aliases; birth date, birthplace and nationality;
  gender, title and position; addresses; ID and passport numbers; role and the listing's notes.
  No scores and no match reasons.
- why_it_matched: one claim per detail of the query that agrees with the listing, in plain words,
  citing the record that shows it. When the UN record has the same name, give it its own claim citing
  the UN key. No score; the summary gives it.
- differences: each detail of the query that contradicts the listing, and each detail the listing holds
  that the query lacks. Name those details, but don't repeat their values from who_matched.
- sanctions: one claim per main authority with whichever of these the bundle has: program, measures
  (such as asset freeze, travel ban, blocking), legal basis, reference number, listing date, last change
  and status. Put the lists that only implement the UN regime together in one claim.
- news: one claim per relevant article, citing its URL, saying what the article reports.
Leave a section empty when the bundle has nothing for it.
At most 8 claims per section."""

_CLAIM = {
    "type": "object",
    "properties": {"text": {"type": "string"}, "cites": {"type": "array", "items": {"type": "string"}}},
    "required": ["text", "cites"],
    "additionalProperties": False,
}
REPORT_SCHEMA = {
    "type": "object",
    "properties": {"summary": _CLAIM, **{s: {"type": "array", "items": _CLAIM} for s in SECTIONS}},
    "required": ["summary", *SECTIONS],
    "additionalProperties": False,
}

# ponytail: penalty features are recognised by name; read coefficients from GET /algorithms if names drift
_PENALTY_WORDS = ("mismatch", "disjoint")
# A query property and the listing properties it is compared with.
_COMPARE = {"country": ("country", "nationality", "citizenship"), "birthDate": ("birthDate",),
            "jurisdiction": ("jurisdiction", "country"), "registrationNumber": ("registrationNumber",)}
_LABELS = {"country": "country", "birthDate": "birth date", "jurisdiction": "jurisdiction",
           "registrationNumber": "registration number"}
# The identity lines of the template's who_matched, one line per group, each joining the
# properties the record has, e.g. "Born: 1973-08-20; birthplace: Bunia; nationality: cd."
_IDENTITY = (
    (("birthDate", "born"), ("birthPlace", "birthplace"), ("nationality", "nationality"), ("country", "country")),
    (("gender", "gender"), ("title", "title"), ("position", "position")),
    (("address", "address"),),
    (("idNumber", "ID number"), ("passportNumber", "passport number"),
     ("registrationNumber", "registration number"), ("jurisdiction", "jurisdiction")),
    (("notes", "notes"),),
)
# The listing details a name-only query can't be checked against, named in differences.
_UNCHECKED = (("birthDate", "birth date"), ("birthPlace", "birthplace"), ("nationality", "nationality"),
              ("country", "country"), ("address", "address"), ("idNumber", "ID number"),
              ("passportNumber", "passport number"), ("registrationNumber", "registration number"))
# The matcher's features in plain words, for a reader who has never seen the matcher.
# A feature not listed here keeps the raw line, e.g. "Matcher check x scored 0.80."
# ponytail: hand-written for the features seen so far; add a line when a new one shows up.
_PLAIN = {
    "name_literal_match": "The name you gave is the same as a listed name, letter for letter",
    "person_name_jaro_winkler": "The name you gave is spelled very close to a listed name",
    "person_name_phonetic_match": "The name you gave sounds the same as a listed name",
    "name_fingerprint_levenshtein": "The name you gave matches a listed name once word order and small spelling differences are set aside",
    "weak_alias_match": "The name you gave matches a weak alias on the listing",
    "identifier_match": "An identifier you gave matches one on the listing",
    "address_entity_match": "The address you gave matches the listed address",
    "last_name_mismatch": "The last name you gave differs from the listed last name",
    "gender_mismatch": "The gender you gave differs from the listed gender",
}
# Penalties about a detail the user gave. _field_differences already writes a line for that
# detail with both values, so the penalty line would only say the same thing again.
_PENALTY_FIELD = {"country_mismatch": "country", "dob_year_disjoint": "birthDate",
                  "dob_day_disjoint": "birthDate", "orgid_disjoint": "registrationNumber"}


def report(query: dict, match: dict, un: dict, band: dict) -> dict:
    """The report for a review or hit case (the spec, section 5.5)."""
    start = time.monotonic()  # the DEADLINE_S clock includes the two lookups below
    top = match["candidates"][0]
    if match["live"]:
        record = layer1_yente.entity(top["id"])  # the full record, with nested sanction entries
        entity_step = "ok" if record else "unavailable"
    else:
        # The match was replayed from the cache because OpenSanctions just failed, so this
        # lookup would almost surely fail too, after its own retries. Use the match instead.
        record, entity_step = None, "skipped"
    news, news_status = _news(top.get("caption", ""), query.get("country"))
    bundle = build_bundle(top, record, news, un)
    out, attempts, reason = _ask_model(query, band, bundle, news_status, start)
    if out is None:
        out = template(query, bundle, top["id"])  # code writes it, so it always passes the check
    claims = [out["summary"], *(claim for section in SECTIONS for claim in out[section])]
    cited = {key for claim in claims for key in claim["cites"]}
    return {
        "summary": out["summary"],
        "sections": {section: out[section] for section in SECTIONS},
        "sources": [{"key": key, "kind": item["kind"], "label": item["label"], "url": item["url"]}
                    for key, item in bundle.items() if key in cited],
        "next_step": NEXT_STEP[band["band"]],
        "check": "pass" if reason is None else "template",
        "check_reason": reason,
        "attempts": attempts,
        "model": http.MODEL,
        "prompt_version": PROMPT_VERSION,
        "seed": http.SEED,
        "steps": {"entity": entity_step, "news": news_status},
    }


def _news(caption: str, country: str | None) -> tuple[list[dict], str]:
    """Tavily news search, one basic search (1 credit). Returns (results, "ok" | "unavailable")."""
    query = " ".join(filter(None, (f'"{caption}"', country, "sanctions OR fraud OR investigation")))
    try:
        resp = http.request(
            "POST", TAVILY_URL,
            headers={"Authorization": f"Bearer {os.environ.get('TAVILY_API_KEY', '')}"},
            json={"query": query, "topic": "news", "search_depth": "basic", "max_results": 5},
            timeout=TAVILY_TIMEOUT_S,
        )
        resp.raise_for_status()
        results = resp.json()["results"]
    except (requests.RequestException, KeyError, TypeError):
        return [], "unavailable"
    return [r for r in results if isinstance(r, dict)], "ok"


def build_bundle(top: dict, record: dict | None, news: list[dict], un: dict) -> dict:
    """Every fact the report may use, keyed by the citation key that must cite it."""
    # The full record when we have it; otherwise the properties that came with the match.
    props = (record or top).get("properties") or {}
    # The candidate itself, cited by its OpenSanctions ID.
    bundle = {top["id"]: {
        "kind": "candidate",
        "label": top.get("caption") or top["id"],
        "url": ENTITY_URL.format(top["id"]),
        "caption": top.get("caption"),
        "schema": top.get("schema"),
        "score": top.get("score"),
        "datasets": top.get("datasets", []),
        "explanations": top.get("explanations", {}),
        "first_seen": top.get("first_seen"),
        "last_seen": top.get("last_seen"),
        "last_change": top.get("last_change"),
        "properties": {key: _texts(values) for key, values in props.items() if key != "sanctions"},
    }}
    # Each nested sanction entry, cited by its own ID. Only the full record has these.
    for entry in props.get("sanctions") or []:
        if isinstance(entry, dict) and entry.get("id"):
            p = entry.get("properties") or {}
            bundle[entry["id"]] = {
                "kind": "sanction",
                "label": ", ".join(_texts(p.get("program"))) or entry.get("caption") or "Sanction",
                "url": _safe_url(next(iter(_texts(p.get("sourceUrl"))), None)) or ENTITY_URL.format(top["id"]),
                "properties": {key: _texts(values) for key, values in p.items() if key != "entity"},
            }
    # News articles, cited by their URL. An article without an http(s) link is left out.
    for article in news:
        url = _safe_url(article.get("url"))
        if url:
            bundle[url] = {"kind": "news", "label": article.get("title") or url, "url": url,
                           "title": article.get("title"), "content": article.get("content"),
                           "published_date": article.get("published_date")}
    # The UN record, when the UN check found one.
    best = un.get("best")
    if best:
        bundle[f"UN:{best['ref']}"] = {"kind": "un", "label": f"UN list entry {best['ref']}",
                                       "url": layer1_un.LIST_URL, **best}
    return bundle


def _texts(values) -> list[str]:
    """A property's values as text. A nested entity becomes its caption."""
    if not isinstance(values, list):
        return []
    return [str(v.get("caption") or v.get("id", "")) if isinstance(v, dict) else str(v) for v in values]


def _safe_url(url) -> str | None:
    """Only http(s) links reach the page. javascript:, data: and the rest are dropped."""
    return url if isinstance(url, str) and url.startswith(("https://", "http://")) else None


def _ask_model(query: dict, band: dict, bundle: dict, news_status: str,
               start: float) -> tuple[dict | None, int, str | None]:
    """Model call 2, retried with the list of failures.
    Returns (report or None, attempts, why it failed or None)."""
    messages = _messages(query, band, bundle, news_status)  # the same on every attempt, so build it once
    failures: list[str] = []
    attempts = 0
    while attempts <= MAX_RETRIES:
        left = DEADLINE_S - (time.monotonic() - start)
        if left <= 0:
            return None, attempts, "deadline passed" + (f" ({'; '.join(failures)})" if failures else "")
        attempts += 1
        try:
            text = http.chat(messages + _retry_note(failures), "report", REPORT_SCHEMA,
                             timeout=min(http.CHAT_TIMEOUT_S, left))
        except http.ModelError as err:
            # The transport has already retried this call, so another attempt would most
            # likely fail the same way. Go straight to the template.
            return None, attempts, f"model unavailable: {err}"
        try:
            out = json.loads(text)
        except ValueError:
            failures = ["the reply was not valid JSON"]
            continue
        failures = check_citations(out, bundle)
        if not failures:
            return out, attempts, None
    return None, attempts, "citation check failed: " + "; ".join(failures)


def _messages(query: dict, band: dict, bundle: dict, news_status: str) -> list[dict]:
    """The system prompt, plus everything the model may use as one JSON message."""
    evidence = {
        "band": band["band"],
        "score": band["score"],
        "query": layer1_yente.payload(query),  # exactly what the matcher was asked
        "news_search": news_status,
        "bundle": bundle,
    }
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}]


def _retry_note(failures: list[str]) -> list[dict]:
    """After a failed attempt, one extra message listing what to fix. Nothing on the first try."""
    if not failures:
        return []
    return [{"role": "user", "content": "Your last report failed the citation check:\n- "
             + "\n- ".join(failures) + "\nReturn the whole report again with every problem fixed."}]


def check_citations(out, bundle: dict) -> list[str]:
    """Every problem with a report's citations. An empty list means it passes."""
    if not isinstance(out, dict):
        return ["the report is not a JSON object"]
    failures = []
    claims = [("summary", 1, out.get("summary"))]
    for section in SECTIONS:
        items = out.get(section)
        if not isinstance(items, list):
            failures.append(f'section "{section}" is missing')
            continue
        claims += [(section, n, claim) for n, claim in enumerate(items, 1)]
    for section, n, claim in claims:
        where = f'claim {n} in "{section}"'
        if not isinstance(claim, dict) or not isinstance(claim.get("text"), str) or not claim["text"].strip():
            failures.append(f"{where} has no text")
            continue
        cites = claim.get("cites")
        if not isinstance(cites, list) or not cites:
            failures.append(f"{where} cites nothing")
            continue
        failures += [f"{where} cites unknown key {key}" for key in cites if not isinstance(key, str) or key not in bundle]
    return failures


def template(query: dict, bundle: dict, top_id: str) -> dict:
    """The report written by code from the bundle alone. It follows the same section rules
    as the model (each fact once, plain words), and every claim cites its own keys, so it
    always passes check_citations."""
    c = bundle[top_id]
    p = c["properties"]
    cite = [top_id]  # most lines are about the candidate, so they cite it
    n = len(c["datasets"])
    summary = _claim(f"{c['caption']} ({c['schema']}) matched with score {c['score']:.2f} "
                     f"and is on {n} list{'' if n == 1 else 's'}.", cite)

    # who_matched: the names with their aliases in one line, then one line per group of
    # identifying details the record has.
    aliases = [a for a in dict.fromkeys(p.get("alias", []) + p.get("weakAlias", [])) if a != c["caption"]]
    also = f", also known as {', '.join(aliases[:10])}" if aliases else ""
    who = [_claim(f"Listed as {c['caption']} ({c['schema']}){also}.", cite)]
    for group in _IDENTITY:
        parts = [f"{label}: {', '.join(p[prop])}" for prop, label in group if p.get(prop)]
        if parts:
            line = "; ".join(parts).rstrip(".")
            who.append(_claim(line[0].upper() + line[1:] + ".", cite))

    # why_it_matched: the matcher's features that count for the match, in plain words, then
    # the UN record's name as a second, independent source.
    # differences: the penalties, plus each detail the user gave that the listing lacks or
    # contradicts. A penalty about such a detail is left out, since that detail's own line
    # already gives both values.
    features = [(name, r) for name, r in c["explanations"].items() if isinstance(r, dict) and (r.get("score") or 0) > 0]
    why = [_claim(_feature_line(name, r), cite) for name, r in features if not _penalty(name)]
    why += [_claim(_un_line(item), [key]) for key, item in bundle.items() if item["kind"] == "un"]
    why = why or [_claim(f"The name you gave was compared with the listed names of {c['caption']}.", cite)]
    differences = [_claim(_feature_line(name, r), cite) for name, r in features
                   if _penalty(name) and _PENALTY_FIELD.get(name) not in query["properties"]]
    differences += _field_differences(query, p, cite)
    if not differences:
        differences = [_claim("The details you gave agree with the listing." if len(query["properties"]) > 1
                              else _name_only_line(p), cite)]

    # sanctions and news: one line per bundle item, each citing only itself.
    sanctions = [_claim(_sanction_line(item), [key]) for key, item in bundle.items() if item["kind"] == "sanction"]
    sanctions += [_claim(f"UN Security Council list entry {item['ref']} ({item['list_type']}), "
                         f"listed {item['listed_on']}.", [key])
                  for key, item in bundle.items() if item["kind"] == "un"]
    sanctions = sanctions or [_claim(f"Listed in: {', '.join(c['datasets']) or 'no named dataset'}.", cite)]
    news = [_claim(f"{item['title'] or item['url']} ({item['published_date'] or 'undated'}).", [key])
            for key, item in bundle.items() if item["kind"] == "news"]
    return {"summary": summary, "who_matched": who, "why_it_matched": why,
            "differences": differences, "sanctions": sanctions, "news": news}


def _claim(text: str, cites: list[str]) -> dict:
    """One report line and the bundle keys that back it."""
    return {"text": text, "cites": cites}


def _penalty(feature: str) -> bool:
    """True for a matcher feature that counts against the match, such as country_mismatch."""
    return any(word in feature for word in _PENALTY_WORDS)


def _feature_line(name: str, result: dict) -> str:
    """One matcher feature as a sentence, in plain words when we know the feature."""
    if name in _PLAIN:
        return _PLAIN[name] + "."
    detail = f": {result['detail']}" if result.get("detail") else ""
    return f"Matcher check {name} scored {result['score']:.2f}{detail}."


def _un_line(item: dict) -> str:
    """The UN check's best name as a sentence, saying whether it is the main name or an alias."""
    kind = "its main name" if item["quality"] == "primary" else f"an alias ({item['quality']} quality)"
    return (f"The UN Security Council list has {item['matched_name']} as {kind} under entry {item['ref']}, "
            f"which the UN check scored {item['score']:.2f} against the name you gave.")


def _name_only_line(listed: dict) -> str:
    """The differences line when only a name was given: which listing details went unchecked."""
    held = [label for prop, label in _UNCHECKED if listed.get(prop)]
    if not held:
        return "Only a name was given, and the listing holds no other details to compare."
    named = held[0] if len(held) == 1 else ", ".join(held[:-1]) + " and " + held[-1]
    return f"Only a name was given, so the listing's {named} could not be checked against it."


def _field_differences(query: dict, listed: dict, cite: list[str]) -> list[dict]:
    """Each detail the user gave that the listing lacks or contradicts."""
    claims = []
    for prop, values in query["properties"].items():
        if prop == "name":
            continue
        asked, label = values[0], _LABELS.get(prop, prop)
        shown = sorted({v for other in _COMPARE.get(prop, (prop,)) for v in listed.get(other, []) if v})
        if not shown:
            claims.append(_claim(f"You gave {label} {asked}; the listing has no {label}.", cite))
        elif not any(v.lower().startswith(asked.lower()) or asked.lower().startswith(v.lower()) for v in shown):
            claims.append(_claim(f"You gave {label} {asked}; the listing shows {', '.join(shown)}.", cite))
    return claims


def _sanction_line(item: dict) -> str:
    """One sanction entry as a sentence: program, authority and start date, then whichever
    of the measures, reference numbers, listing date, status and reason the entry has."""
    p = item["properties"]
    line = item["label"]
    if p.get("authority"):
        line += " by " + ", ".join(p["authority"])
    if p.get("startDate"):
        line += " since " + ", ".join(p["startDate"])
    refs = list(dict.fromkeys(p.get("authorityId", []) + p.get("unscId", [])))
    extras = [f"{label}: {', '.join(values)}" for label, values in (
        ("measures", p.get("provisions")), ("reference", refs), ("listed", p.get("listingDate")),
        ("ends", p.get("endDate")), ("status", p.get("status")), ("reason", p.get("reason"))) if values]
    return "; ".join([line, *extras]).rstrip(".") + "."
