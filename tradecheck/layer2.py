"""Layer 2: fixed rules turn the matcher's top score and the UN check into a band.

No model here. The matcher decides; the model only explains (Layer 3).
"""

from __future__ import annotations

CLEAR_BELOW = 0.70  # placeholder cutoffs until a labelled test set exists
HIT_AT = 0.90
# What the user sees for each band, and the status a screen starts with. A review case
# moves to "reviewed" once an analyst decides (audit.review).
LABEL = {"clear": "Clear*", "review": "Caution", "hit": "Avoid", "unknown": "Unknown"}
STATUS = {"clear": "cleared", "review": "pending_review", "hit": "flagged", "unknown": "unknown"}


def band(match: dict | None, un: dict, fallback: bool = False) -> dict:
    """Return {"band", "score", "cutoffs", "reasons"}. ``match`` is None when yente was
    unavailable. ``fallback`` is True when the model could not extract the fields."""
    cutoffs = {"clear_below": CLEAR_BELOW, "hit_at": HIT_AT}
    if match is None:
        return {"band": "unknown", "score": None, "cutoffs": cutoffs,
                "reasons": ["the live match check failed and no saved result exists"]}
    score = match["candidates"][0]["score"] if match["candidates"] else 0.0
    if score >= HIT_AT:
        name, reasons = "hit", [f"score {score:.2f} at or above {HIT_AT:.2f}"]
    elif score >= CLEAR_BELOW:
        name, reasons = "review", [f"score {score:.2f} between {CLEAR_BELOW:.2f} and {HIT_AT:.2f}"]
    else:
        name, reasons = "clear", [f"score {score:.2f} below {CLEAR_BELOW:.2f}"]
    if un["status"] != "agree":
        reasons.append(f"UN check {un['status']}: {un['reason']}")
    if fallback:
        reasons.append("the fields could not be extracted, so the whole question was screened as a name")
    # A doubt only ever raises clear to review. It never lowers a hit.
    if name == "clear" and (un["status"] != "agree" or fallback):
        name = "review"
    return {"band": name, "score": score, "cutoffs": cutoffs, "reasons": reasons}
