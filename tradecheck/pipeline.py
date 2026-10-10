"""Run one question through every layer and write the audit trail.

Also a command line for the live check (it reads the keys from .env). It makes real
API calls and spends credits; the tests never do (see RUN.md):
    python -m tradecheck.pipeline "Is KHAWA PANGA MANDRO sanctioned?" "Chief Kahwa"
    python -m tradecheck.pipeline --full "Chief Kahwa"     # the whole result as JSON
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from pathlib import Path

from tradecheck import audit, http, layer1_un, layer1_yente, layer2, layer3, parse

DISCLAIMER = "Not legal advice. Screening reflects the listed sources as of the dates shown."
COLLECTION_SOURCE = {"key": "sanctions", "kind": "collection", "label": "OpenSanctions sanctions collection",
                     "url": "https://www.opensanctions.org/datasets/sanctions/"}
UN_SOURCE = {"key": "UN", "kind": "un_list", "label": "UN Security Council consolidated list",
             "url": layer1_un.LIST_URL}

AS_OF: str | None = None  # when the sanctions collection was last updated; set by start()


def start(un_path: Path | str = layer1_un.DEFAULT_PATH) -> dict:
    """Load what every screen needs. Call once, before the first screen."""
    global AS_OF
    audit.init()
    records = layer1_un.load(un_path)
    AS_OF = layer1_yente.catalog_as_of()
    return {"un_records": records, "as_of": AS_OF}


def screen(question: str) -> dict:
    """Screen one question and return the API response. Raises parse.NoParty when the
    question names no party."""
    screen_id = str(uuid.uuid4())
    checked_at = audit.now()
    model = {"model": http.MODEL, "prompt_version": parse.PROMPT_VERSION}

    # Input: model call 1 pulls the party out of the question.
    try:
        query = parse.extract(question)
    except parse.NoParty as err:
        audit.log(screen_id, "input", {"question": question, "error": str(err), **model})
        # Nothing to screen, but the record still ends with a final event like every other.
        audit.log(screen_id, "final", {"status": "not_screened"})
        raise
    if query["source"] == "fallback":
        # The model is down. If this exact question was asked before, reuse the fields the
        # model gave then: the query matches again, so a saved yente response can replay
        # even when the whole network is down.
        saved = audit.saved_extraction(question)
        if saved:
            query = {**saved, "source": "saved", "fallback_reason": query["fallback_reason"]}
    audit.log(screen_id, "input", {"question": question, "parsed": query, "source": query["source"], **model})

    # Layer 1: the OpenSanctions match, then our own UN cross-check. The UN check runs even
    # when the match failed, so the audit record always shows what it found.
    try:
        match, error = layer1_yente.match(query), None
    except layer1_yente.YenteUnavailable as err:
        match, error = None, str(err)
    un = layer1_un.check(query, match)
    live = bool(match and match["live"])
    fetched_at = match["fetched_at"] if match else None
    # Just what the page and the audit log need. The full candidates stay in `match` for Layer 3.
    candidates = [{"id": c["id"], "caption": c.get("caption"), "score": c["score"], "datasets": c.get("datasets", [])}
                  for c in (match["candidates"] if match else [])]
    audit.log(screen_id, "layer1", {
        "collection": layer1_yente.COLLECTION, "algorithm": layer1_yente.ALGORITHM,
        "threshold": layer1_yente.THRESHOLD, "candidates": candidates, "live": live, "fetched_at": fetched_at,
        "query_hash": match["query_hash"] if match else None, "error": error, "un": un,
    })

    # Layer 2: fixed rules pick the band. No model is involved.
    band = layer2.band(match, un, fallback=query["source"] == "fallback")
    audit.log(screen_id, "layer2", band)

    # Layer 3: a cited report, but only for review and hit cases with a candidate to explain.
    # Every other case gets a fixed report and makes no model call.
    if band["band"] in ("review", "hit") and candidates:
        try:
            report = layer3.report(query, match, un, band)
        except Exception as err:  # the report explains the decision; a bug in it must not hide it
            report = _error_report(band, err)
    else:
        report = _fixed_report(band, match, error)
    # Fixed reports are logged too, so the audit record (and the queue view built from it)
    # always holds the report the user saw. check: "fixed" marks the ones with no model call.
    audit.log(screen_id, "layer3", report)

    status = layer2.STATUS[band["band"]]
    audit.log(screen_id, "final", {"status": status})
    return {
        "screen_id": screen_id,
        "question": question,
        "parsed": query,
        "band": band["band"],
        "label": layer2.LABEL[band["band"]],
        "score": band["score"],
        "cutoffs": band["cutoffs"],
        "reasons": band["reasons"],
        "status": status,
        "candidates": candidates,
        "un_check": un,
        "live": live,
        "fetched_at": fetched_at,
        "report": report,
        # The startup data date only describes a live match. A replayed match is as old as its
        # saved result (fetched_at), and an Unknown result used no sanctions data at all.
        "as_of": AS_OF if live else None,
        "checked_at": checked_at,
        "disclaimer": DISCLAIMER,
    }


def _fixed_report(band: dict, match: dict | None, error: str | None) -> dict:
    """The report when Layer 3 doesn't run: clear, unknown, or a review raised with no
    matcher candidate to explain. No model call. ``match`` is None only for unknown."""
    name = band["band"]
    if name == "unknown":
        return _plain_report(f"Live check failed: {error}. Not clear. Retry.", [], None, "fixed")
    if name == "clear":
        # A replayed result is only as recent as its saved answer; naming the data date read
        # at startup would claim a newer check than really happened.
        if match["live"]:
            when = AS_OF or "the time checked"
        else:
            when = f"the saved result from {match['fetched_at']}"
        text = (f"No candidate reached {layer2.CLEAR_BELOW:.2f} in the OpenSanctions sanctions "
                f"collection (US, Canada, EU, UK, UN and more) as of {when}, and the UN check agrees.")
    else:
        text = (f"No matcher candidate reached {layer2.CLEAR_BELOW:.2f}, but this needs review: "
                + "; ".join(band["reasons"]) + ".")
    return _plain_report(text, [COLLECTION_SOURCE, UN_SOURCE], layer3.NEXT_STEP.get(name), "fixed")


def _error_report(band: dict, err: Exception) -> dict:
    """The report when Layer 3 itself fails: the band stands, and the error is named."""
    return _plain_report("The evidence report could not be built, so only the matcher's result is shown.",
                         [], layer3.NEXT_STEP[band["band"]], "error", f"{type(err).__name__}: {err}")


def _plain_report(text: str, sources: list[dict], next_step: str | None, check: str,
                  check_reason: str | None = None) -> dict:
    """A one-line report written without the model, in the same shape as layer3.report(),
    so the page and the audit log handle every report the same way. The line cites
    every source passed in."""
    return {"summary": {"text": text, "cites": [source["key"] for source in sources]},
            "sections": {section: [] for section in layer3.SECTIONS}, "sources": sources,
            "next_step": next_step, "check": check, "check_reason": check_reason,
            "attempts": 0, "steps": {}}


def main(argv: list[str] | None = None) -> int:
    """The command line: screen each question and print one summary line (or, with
    --full, the whole JSON result). Questions with no party are reported and skipped."""
    from dotenv import load_dotenv  # comes with uvicorn[standard]

    for stream in (sys.stdout, sys.stderr):
        # Windows gives Python a cp1252 stream when output isn't an interactive console
        # (Git Bash, a pipe), and listed names and aliases are often Cyrillic or Arabic.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="Screen questions through every TradeCheck layer.")
    ap.add_argument("questions", nargs="+", help="free-text questions or bare names")
    ap.add_argument("--full", action="store_true", help="print each whole result as JSON")
    args = ap.parse_args(argv)
    load_dotenv()
    info = start()
    print(f"UN records: {info['un_records']}  sanctions data as of: {info['as_of']}", file=sys.stderr)
    for question in args.questions:
        began = time.monotonic()
        try:
            res = screen(question)
        except parse.NoParty as err:
            print(f"{question!r}: {err}", file=sys.stderr)
            continue
        if args.full:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        top = res["candidates"][0]["caption"] if res["candidates"] else "-"
        score = "-" if res["score"] is None else f"{res['score']:.2f}"
        print(f"{res['label']:8} score={score}  top={top}  UN={res['un_check']['status']}  "
              f"parsed={res['parsed']['source']}  live={res['live']}  "
              f"report={res['report']['check']}/{res['report']['attempts']}  "
              f"{time.monotonic() - began:.1f}s  {res['screen_id']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
