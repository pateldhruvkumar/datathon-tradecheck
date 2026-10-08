"""Layer 1b on a small UN XML fixture."""

from pathlib import Path

import pytest

from tradecheck import layer1_un

FIXTURE = Path(__file__).parent / "fixtures" / "un_sample.xml"
NO_CANDIDATES = {"candidates": []}


@pytest.fixture(autouse=True)
def loaded():
    assert layer1_un.load(FIXTURE) == 2


def _q(name):
    return {"name": name}


def _match(score, datasets=("un_sc_sanctions",)):
    return {"candidates": [{"id": "NK-1", "caption": "Listed Party", "score": score, "datasets": list(datasets)}]}


@pytest.mark.parametrize("name, quality, score", [
    ("KHAWA PANGA MANDRO", "primary", 1.0),
    ("Blue Harbour Lines", "Good", 0.85),
    ("Testco Shipping", "unrated", 0.85),
    ("Orchid Cargo", "Low", 0.6),
    ("Chief Kahwa", "Low", 0.6),
])
def test_alias_weights(name, quality, score):
    best = layer1_un.check(_q(name), _match(0.95))["best"]
    assert (best["quality"], best["score"]) == (quality, score)


def test_best_carries_the_listing():
    assert layer1_un.check(_q("Chief Kahwa"), _match(0.80))["best"] == {
        "ref": "CDi.000", "name": "KHAWA PANGA MANDRO", "matched_name": "Chief Kahwa", "quality": "Low",
        "score": 0.6, "listed_on": "2005-11-01", "list_type": "DRC"}


def test_agree_when_both_find_the_listing():
    un = layer1_un.check(_q("KHAWA PANGA MANDRO"), _match(0.97))
    assert (un["status"], un["list_date"]) == ("agree", "2026-10-07T23:00:03.504Z")


def test_a_low_alias_match_is_not_a_disagreement():
    # A Low alias tops out at 0.60, but the UN list does contain the name.
    assert layer1_un.check(_q("Chief Kahwa"), _match(0.80))["status"] == "agree"


@pytest.mark.parametrize("match", [NO_CANDIDATES, None, _match(0.40)])
def test_disagree_a_strong_un_match_the_matcher_missed(match):
    un = layer1_un.check(_q("KHAWA PANGA MANDRO"), match)
    assert un["status"] == "disagree"
    assert un["reason"].startswith('UN match "KHAWA PANGA MANDRO" scored 1.00')


def test_disagree_b_the_matcher_says_un_listed_but_the_un_file_has_no_such_name():
    un = layer1_un.check(_q("Northwind Metals"), _match(0.92))
    assert (un["status"], un["best"]) == ("disagree", None)


def test_agree_when_neither_finds_anything():
    un = layer1_un.check(_q("AgroDistribuidora del Bajío SA de CV"), NO_CANDIDATES)
    assert (un["status"], un["best"]) == ("agree", None)


def test_a_name_without_latin_letters_is_unavailable():
    un = layer1_un.check(_q("Хава Панга Мандро"), NO_CANDIDATES)
    assert (un["status"], un["reason"]) == ("unavailable", "the name has no Latin letters to compare")


@pytest.mark.parametrize("content", [None, b"<CONSOLIDATED_LIST><INDIVIDUALS><INDIVID"])
def test_a_missing_or_truncated_file_makes_every_check_unavailable(tmp_path, content):
    path = tmp_path / "un_sc.xml"
    if content is not None:
        path.write_bytes(content)
    assert layer1_un.load(path) == 0
    un = layer1_un.check(_q("KHAWA PANGA MANDRO"), NO_CANDIDATES)
    assert (un["status"], un["reason"]) == ("unavailable", "UN list not loaded")
