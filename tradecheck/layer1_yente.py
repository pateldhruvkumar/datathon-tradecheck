"""Layer 1a: the hosted OpenSanctions matcher (yente).

The collection, algorithm and threshold are pinned, so the same query gets the same
answer. Every live answer is saved; when the live call fails, the saved answer for
the same query is used and marked ``live: false``.
"""

from __future__ import annotations

import hashlib
import json
import os
from urllib.parse import quote

import requests

from tradecheck import audit, http

BASE_URL = "https://api.opensanctions.org"
COLLECTION = "sanctions"
ALGORITHM = "logic-v2"
THRESHOLD = 0.7
LIMIT = 5
TIMEOUT_S = 15
PARAMS = {"algorithm": ALGORITHM, "threshold": THRESHOLD, "limit": LIMIT}  # sent as query parameters


class YenteUnavailable(Exception):
    """The live match failed and nothing is saved for this query."""


def _headers() -> dict:
    """The auth header. The key is read from the environment and never logged."""
    return {"Authorization": f"ApiKey {os.environ.get('OPENSANCTIONS_API_KEY', '')}"}


def payload(query: dict) -> dict:
    """The part of a parsed query that yente receives. The request body and the cache
    key are both built from this one place, so they can't drift apart."""
    return {"schema": query["schema"], "properties": query["properties"]}


def query_hash(query: dict) -> str:
    """SHA-256 of the canonical JSON of what is sent, plus the pinned settings.

    Careful: changing what goes in here changes every key, and the saved responses in
    data/tradecheck.sqlite (the demo's offline replays) would stop matching."""
    sent = {"collection": COLLECTION, **PARAMS, "query": payload(query)}
    return hashlib.sha256(json.dumps(sent, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def match(query: dict) -> dict:
    """Return {"candidates" (best first), "live", "fetched_at", "query_hash"}.
    Raises YenteUnavailable when the call fails and nothing is saved.

    The live call always goes first, so every screen spends one match call. The saved
    answer is only the fallback for when OpenSanctions can't be reached."""
    qhash = query_hash(query)
    try:
        resp = http.request(
            "POST", f"{BASE_URL}/match/{COLLECTION}",
            headers=_headers(), params=PARAMS, json={"queries": {"q": payload(query)}}, timeout=TIMEOUT_S,
        )
        resp.raise_for_status()
        candidates = sorted(resp.json()["responses"]["q"]["results"], key=lambda c: c["score"], reverse=True)
    except (requests.RequestException, KeyError, TypeError) as err:
        saved = audit.cache_get(qhash)
        if saved is None:
            raise YenteUnavailable(_why(err)) from err
        return {**saved, "live": False, "query_hash": qhash}
    fetched_at = audit.now()
    audit.cache_put(qhash, candidates, fetched_at)
    return {"candidates": candidates, "live": True, "fetched_at": fetched_at, "query_hash": qhash}


def _why(err: Exception) -> str:
    """A short, readable reason for the audit log and the Unknown report."""
    if isinstance(err, requests.HTTPError) and err.response is not None:
        return f"OpenSanctions answered HTTP {err.response.status_code}"
    if isinstance(err, (ValueError, KeyError, TypeError)):
        return "unexpected answer from OpenSanctions"
    return f"no connection to OpenSanctions ({type(err).__name__})"


def entity(entity_id: str) -> dict | None:
    """The full record, with nested sanction entries and linked entities. None on failure.
    Layer 3 calls this once per review or hit screen, and only after a live match."""
    try:
        resp = http.request("GET", f"{BASE_URL}/entities/{quote(entity_id, safe='')}",
                            headers=_headers(), timeout=TIMEOUT_S)
        resp.raise_for_status()
        record = resp.json()
    except requests.RequestException:
        return None
    return record if isinstance(record, dict) else None


def catalog_as_of() -> str | None:
    """When the sanctions collection was last updated, from GET /catalog. None if unknown.
    Called once at startup (pipeline.start), not once per screen."""
    try:
        resp = http.request("GET", f"{BASE_URL}/catalog", headers=_headers(), timeout=TIMEOUT_S)
        resp.raise_for_status()
        datasets = resp.json()["datasets"]
    except (requests.RequestException, KeyError, TypeError):
        return None
    for dataset in datasets:
        if isinstance(dataset, dict) and dataset.get("name") == COLLECTION:
            # yente's dataset model has all three fields; the live check confirms which is filled
            return dataset.get("updated_at") or dataset.get("last_export") or dataset.get("version")
    return None
