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

PROMPT_VERSION = "report-v2"
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
- Use only the evidence bundle. No outside knowledge.
- Every claim cites at least one bundle key in "cites", copied exactly.
- State facts and differences only. No verdicts, no recommendations, no legal advice.
Sections:
- summary: one or two sentences on who matched and how strongly.
- who_matched: who the listed party is (names, aliases, birth date, country).
- why_it_matched: which details of the query agree with the listing.
- differences: details of the query that differ from the listing or are missing from it.
- sanctions: programs, authorities and dates.
- news: one claim per relevant article, citing its URL.
Leave a section empty when the bundle has nothing for it.
Keep the report short enough to read aloud: at most 5 claims per section, one sentence each.
In sanctions, group the entries by authority or program instead of one claim per entry."""

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


def report(query: dict, match: dict, un: dict, band: dict) -> dict:
    """The report for a review or hit case (the spec, section 5.5)."""
    start = time.monotonic()
    top = match["candidates"][0]
    record = layer1_yente.entity(top["id"])
    news, news_status = _news(top.get("caption", ""), query.get("country"))
    bundle = build_bundle(top, record, news, un)
    out, attempts, reason = _ask_model(query, band, bundle, news_status, start)
    if out is None:
        out = template(query, bundle, top["id"])
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
        "steps": {"entity": "ok" if record else "unavailable", "news": news_status},
    }


def _news(caption: str, country: str | None) -> tuple[list[dict], str]:
    """Tavily news search. Returns (results, "ok" | "unavailable")."""
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
    props = (record or top).get("properties") or {}
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
    for entry in props.get("sanctions") or []:
        if isinstance(entry, dict) and entry.get("id"):
            p = entry.get("properties") or {}
            bundle[entry["id"]] = {
                "kind": "sanction",
                "label": ", ".join(_texts(p.get("program"))) or entry.get("caption") or "Sanction",
                "url": _safe_url(next(iter(_texts(p.get("sourceUrl"))), None)) or ENTITY_URL.format(top["id"]),
                "properties": {key: _texts(values) for key, values in p.items() if key != "entity"},
            }
    for article in news:
        url = _safe_url(article.get("url"))
        if url:
            bundle[url] = {"kind": "news", "label": article.get("title") or url, "url": url,
                           "title": article.get("title"), "content": article.get("content"),
                           "published_date": article.get("published_date")}
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
    failures: list[str] = []
    attempts = 0
    while attempts <= MAX_RETRIES:
        left = DEADLINE_S - (time.monotonic() - start)
        if left <= 0:
            return None, attempts, "deadline passed" + (f" ({'; '.join(failures)})" if failures else "")
        attempts += 1
        try:
            text = http.chat(_messages(query, band, bundle, news_status, failures), "report", REPORT_SCHEMA,
                             timeout=min(http.CHAT_TIMEOUT_S, left))
        except http.ModelError as err:
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


def _messages(query: dict, band: dict, bundle: dict, news_status: str, failures: list[str]) -> list[dict]:
    evidence = {
        "band": band["band"],
        "score": band["score"],
        "query": {"schema": query["schema"], "properties": query["properties"]},
        "news_search": news_status,
        "bundle": bundle,
    }
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": json.dumps(evidence, ensure_ascii=False)}]
    if failures:
        messages.append({"role": "user", "content": "Your last report failed the citation check:\n- "
                         + "\n- ".join(failures) + "\nReturn the whole report again with every problem fixed."})
    return messages


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
    """The report written by code from the bundle alone. Every claim cites its own
    keys, so it always passes check_citations."""
    c = bundle[top_id]
    p = c["properties"]
    cite = [top_id]
    datasets = ", ".join(c["datasets"]) or "no named dataset"
    summary = _claim(f"{c['caption']} ({c['schema']}) matched with score {c['score']:.2f}; listed in {datasets}.", cite)

    who = [_claim(f"Listed as {c['caption']} ({c['schema']}).", cite)]
    aliases = [a for a in dict.fromkeys(p.get("alias", []) + p.get("weakAlias", [])) if a != c["caption"]]
    if aliases:
        who.append(_claim("Also known as: " + ", ".join(aliases[:10]) + ".", cite))
    for prop, label in (("birthDate", "Born"), ("nationality", "Nationality"), ("country", "Country"),
                        ("jurisdiction", "Jurisdiction"), ("registrationNumber", "Registration number")):
        if p.get(prop):
            who.append(_claim(f"{label}: {', '.join(p[prop])}.", cite))

    features = [(name, r) for name, r in c["explanations"].items() if isinstance(r, dict) and (r.get("score") or 0) > 0]
    why = [_claim(_feature_line("Matcher feature", name, r), cite) for name, r in features if not _penalty(name)]
    why = why or [_claim(f"Overall match score {c['score']:.2f}.", cite)]
    differences = [_claim(_feature_line("Matcher penalty", name, r), cite) for name, r in features if _penalty(name)]
    differences += _field_differences(query, p, cite)
    if not differences:
        differences = [_claim("The details you gave agree with the listing." if len(query["properties"]) > 1
                              else "Only a name was given, so no other details could be compared.", cite)]

    sanctions = [_claim(_sanction_line(item), [key]) for key, item in bundle.items() if item["kind"] == "sanction"]
    sanctions += [_claim(f"UN Security Council list entry {item['ref']} ({item['list_type']}), listed "
                         f"{item['listed_on']}: matched {item['matched_name']} ({item['quality']}, "
                         f"score {item['score']:.2f}).", [key])
                  for key, item in bundle.items() if item["kind"] == "un"]
    sanctions = sanctions or [_claim(f"Listed in: {datasets}.", cite)]
    news = [_claim(f"{item['title'] or item['url']} ({item['published_date'] or 'undated'}).", [key])
            for key, item in bundle.items() if item["kind"] == "news"]
    return {"summary": summary, "who_matched": who, "why_it_matched": why,
            "differences": differences, "sanctions": sanctions, "news": news}


def _claim(text: str, cites: list[str]) -> dict:
    return {"text": text, "cites": cites}


def _penalty(feature: str) -> bool:
    return any(word in feature for word in _PENALTY_WORDS)


def _feature_line(prefix: str, name: str, result: dict) -> str:
    detail = f": {result['detail']}" if result.get("detail") else ""
    return f"{prefix} {name} scored {result['score']:.2f}{detail}."


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
    p = item["properties"]
    line = item["label"]
    if p.get("authority"):
        line += " by " + ", ".join(p["authority"])
    if p.get("startDate"):
        line += " since " + ", ".join(p["startDate"])
    return line + "."
