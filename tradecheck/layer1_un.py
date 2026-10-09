"""Layer 1b: cross-check the query against our own copy of the UN Security Council list.

yente already includes the UN list. This independent check catches the cases where
the two disagree (a missed listing, a delisting, data lag) and raises them for review.
"""

from __future__ import annotations

from pathlib import Path

from lxml import etree
from rapidfuzz import fuzz, process

from ingest.fetch import RAW_DIR, SOURCES
from ingest.normalize import normalize_name
from tradecheck.layer2 import CLEAR_BELOW

LIST_URL, _FILENAME = SOURCES["un_sc"]
DEFAULT_PATH = RAW_DIR / _FILENAME
WEIGHTS = {"primary": 1.0, "Good": 0.85, "Low": 0.60}  # the README's Oct 3 test on the UN sample
UNRATED = 0.85       # any other QUALITY: empty, or the "a.k.a."/"f.k.a." the UN gives entity aliases
STRONG_AT = 0.90     # only a primary name can reach this
MIN_SIMILARITY = 70  # WRatio a UN name needs before it counts as found

# The index. load() fills it once at startup; check() only reads it.
_names: list[dict] = []   # one entry per primary name or alias
_choices: list[str] = []  # the normalized names, in the same order
_list_date: str | None = None


def load(path: Path | str = DEFAULT_PATH) -> int:
    """Index every primary name and alias in the UN XML. Returns the number of records.
    A missing or broken file loads nothing, and every check is then unavailable."""
    global _list_date
    _names.clear()
    _choices.clear()
    _list_date = None
    parser = etree.XMLParser(resolve_entities=False, no_network=True)
    try:
        root = etree.parse(str(path), parser).getroot()
    except (OSError, etree.XMLSyntaxError):
        return 0
    _list_date = root.get("dateGenerated")
    records = 0
    for rec in root.iter("INDIVIDUAL", "ENTITY"):
        records += 1
        name = " ".join(filter(None, (_text(rec, tag) for tag in ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME"))))
        base = {"ref": _text(rec, "REFERENCE_NUMBER"), "name": name,
                "listed_on": _text(rec, "LISTED_ON"), "list_type": _text(rec, "UN_LIST_TYPE")}
        _add(base, name, "primary")
        for alias in rec.iter("INDIVIDUAL_ALIAS", "ENTITY_ALIAS"):
            _add(base, _text(alias, "ALIAS_NAME"), _text(alias, "QUALITY") or "unrated")
    return records


def _text(element, tag: str) -> str:
    """The trimmed text of a child element, or "" when it's missing."""
    return (element.findtext(tag) or "").strip()


def _add(base: dict, name: str, quality: str) -> None:
    """Index one name of a record. A name that normalizes to nothing is skipped."""
    normalized = normalize_name(name)
    if normalized:
        _names.append({**base, "matched_name": name, "quality": quality, "weight": WEIGHTS.get(quality, UNRATED)})
        _choices.append(normalized)


def check(query: dict, match: dict | None) -> dict:
    """Return {"status": "agree" | "disagree" | "unavailable", "best", "reason", "list_date"}.
    ``match`` is None when yente was unavailable; its top score then counts as 0."""
    wanted = normalize_name(query["name"])
    if not _names or not wanted:
        reason = "UN list not loaded" if not _names else "the name has no Latin letters to compare"
        return {"status": "unavailable", "best": None, "reason": reason, "list_date": _list_date}
    # The best weighted score among the names that reach MIN_SIMILARITY. limit=None because
    # the weights can reorder them: a Low alias at 100 scores below a primary name at 90.
    best = None
    for _, similarity, i in process.extract(wanted, _choices, scorer=fuzz.WRatio,
                                            score_cutoff=MIN_SIMILARITY, limit=None):
        score = round(similarity / 100 * _names[i]["weight"], 3)
        if best is None or score > best["score"]:
            n = _names[i]
            best = {"ref": n["ref"], "name": n["name"], "matched_name": n["matched_name"], "quality": n["quality"],
                    "score": score, "listed_on": n["listed_on"], "list_type": n["list_type"]}
    top = match["candidates"][0] if match and match["candidates"] else None
    top_score = top["score"] if top else 0.0
    # Rule (a): the UN list has a strong match, but the matcher scored it below the clear
    # cutoff, so it may have missed a UN listing. Rule (b): the matcher's top candidate is
    # on the UN list, but no UN name reaches MIN_SIMILARITY, which points to a delisting or
    # data lag. Anything else agrees. (Spec, section 5.3.)
    if best and best["score"] >= STRONG_AT and top_score < CLEAR_BELOW:
        status = "disagree"
        reason = f'UN match "{best["matched_name"]}" scored {best["score"]:.2f} but the matcher\'s top score is {top_score:.2f}'
    elif top and top_score >= CLEAR_BELOW and "un_sc_sanctions" in top.get("datasets", []) and best is None:
        status = "disagree"
        reason = (f'the matcher lists "{top.get("caption")}" on the UN list, '
                  f"but no UN name is at least {MIN_SIMILARITY}% similar")
    else:
        status, reason = "agree", "the UN check agrees with the matcher"
    return {"status": status, "best": best, "reason": reason, "list_date": _list_date}
