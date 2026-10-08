"""Layer 2's band table. Pure functions, no fakes needed."""

import pytest

from tradecheck import layer2

AGREE = {"status": "agree", "best": None, "reason": "the UN check agrees with the matcher"}
DISAGREE = {"status": "disagree", "best": None, "reason": 'UN match "X" scored 1.00 but the matcher\'s top score is 0.00'}
UNAVAILABLE = {"status": "unavailable", "best": None, "reason": "UN list not loaded"}


def _match(*scores):
    return {"candidates": [{"id": f"NK-{i}", "score": s} for i, s in enumerate(scores)], "live": True}


@pytest.mark.parametrize("scores, expected", [
    ((0.95,), "hit"), ((0.90,), "hit"), ((0.89,), "review"), ((0.70,), "review"),
    ((0.69,), "clear"), ((), "clear"),
])
def test_band_table(scores, expected):
    assert layer2.band(_match(*scores), AGREE)["band"] == expected


def test_unknown_when_the_match_is_unavailable():
    result = layer2.band(None, AGREE)
    assert (result["band"], result["score"]) == ("unknown", None)


@pytest.mark.parametrize("un", [DISAGREE, UNAVAILABLE])
def test_a_un_doubt_raises_clear_to_review(un):
    result = layer2.band(_match(0.40), un)
    assert result["band"] == "review"
    assert result["reasons"][-1] == f"UN check {un['status']}: {un['reason']}"


def test_a_un_doubt_never_lowers_a_hit():
    assert layer2.band(_match(0.95), DISAGREE)["band"] == "hit"
    assert layer2.band(_match(0.80), DISAGREE)["band"] == "review"


def test_a_fallback_extraction_is_never_clear():
    result = layer2.band(_match(0.30), AGREE, fallback=True)
    assert result["band"] == "review"
    assert result["reasons"][-1] == "the fields could not be extracted, so the whole question was screened as a name"


def test_reasons_and_cutoffs():
    result = layer2.band(_match(0.83), AGREE)
    assert result["reasons"] == ["score 0.83 between 0.70 and 0.90"]
    assert result["cutoffs"] == {"clear_below": 0.70, "hit_at": 0.90}


def test_labels_and_statuses():
    assert layer2.LABEL == {"clear": "Clear*", "review": "Caution", "hit": "Avoid", "unknown": "Unknown"}
    assert layer2.STATUS == {"clear": "cleared", "review": "pending_review", "hit": "flagged", "unknown": "unknown"}
