"""Name normalization for the UN cross-check (``tradecheck/layer1_un.py``).

Name normalization follows the README's "How we grade a hit" rules:
lowercase, strip legal suffixes (Ltd, PLC, SA de CV, ...), fold accents.
"""

from __future__ import annotations

import re
import unicodedata

# Legal-entity suffixes to strip from the END of a normalized name.
# Multi-word phrases are matched before single tokens (see _SUFFIX_PHRASES ordering).
_SUFFIXES: list[str] = [
    # multi-word first
    "sa de cv", "s a de c v", "s de rl de cv", "s de rl", "c v",
    "sp z oo", "pte ltd", "pty ltd", "co ltd", "and co", "e hf",
    # single token
    "ltd", "limited", "plc", "inc", "incorporated", "llc", "llp", "lp",
    "corp", "corporation", "co", "company", "cia", "compania",
    "sa", "sas", "sarl", "srl", "spa", "sl", "slu", "sro", "spzoo",
    "ag", "gmbh", "mbh", "kg", "ohg", "eg",
    "bv", "nv", "oy", "ab", "as", "aps",
    "kk", "gk", "yk",
    "pjsc", "ojsc", "cjsc", "jsc", "oao", "ooo", "zao", "pao",
    "pty", " pt", "tbk", "bhd", "sdn", "sdn bhd",
    "fze", "fzco", "fze llc", "wll", "psc",
]
# Longer phrases (more tokens) must be tried first so "sa de cv" wins over "cv".
_SUFFIX_PHRASES: list[list[str]] = sorted(
    (s.split() for s in _SUFFIXES), key=len, reverse=True
)

_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")
_WS_RE = re.compile(r"\s+")


def fold_accents(text: str) -> str:
    """Strip diacritics via NFKD decomposition (é -> e, ü -> u)."""
    decomposed = unicodedata.normalize("NFKD", text)
    return decomposed.encode("ascii", "ignore").decode("ascii")


def normalize_name(raw: str | None) -> str:
    """Return a comparable form of ``raw``: lowercased, de-accented, de-punctuated,
    with trailing legal suffixes removed. Used as the join/fuzzy-match key."""
    if not raw:
        return ""
    s = fold_accents(raw).lower()
    s = _PUNCT_RE.sub(" ", s)
    s = _WS_RE.sub(" ", s).strip()
    tokens = s.split()
    changed = True
    while changed and tokens:
        changed = False
        for phrase in _SUFFIX_PHRASES:
            n = len(phrase)
            if n < len(tokens) and tokens[-n:] == phrase:
                # keep at least one token so a name never normalizes to empty
                tokens = tokens[:-n]
                changed = True
                break
    return " ".join(tokens)

