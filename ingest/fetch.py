"""Download the UN Security Council consolidated sanctions list directly from source.

TradeCheck screens against the hosted OpenSanctions matcher; this file is the
independent copy behind the UN cross-check (``tradecheck/layer1_un.py``).

Writes raw files to ``data/raw/`` and a ``manifest.json`` recording the URL,
``fetched_at`` (ISO-8601 UTC), byte size and SHA-256 for every file -- the
provenance the product promises ("source and date behind every answer").

The list is free government data with no key and no licence restriction.

TLS is verified against the operating system's trust store when the optional
``truststore`` package is installed (it is in requirements.txt), so networks that
inspect HTTPS with an IT-installed root certificate still verify. Verification is
never disabled: a tampered list would defeat the point of screening.

Run:  python -m ingest.fetch               # every source (today: just the UN list)
      python -m ingest.fetch --only un_sc
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
MANIFEST = RAW_DIR / "manifest.json"

# key -> (url, local filename). The UN answers with a redirect to Azure blob
# storage; requests follows it.
SOURCES: dict[str, tuple[str, str]] = {
    "un_sc": (
        "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
        "un_sc.xml",
    ),
}

# Some government endpoints reject empty/default user agents.
HEADERS = {"User-Agent": "TradeCheck/0.1 (sanctions screening; +https://github.com/dmodi07/tradecheck)"}

# One hint per failure class, printed after the run.
_TLS_HINT = (
    "Certificate verification failed. Your network, VPN or antivirus is most likely "
    "inspecting HTTPS and re-signing it with its own certificate, which Python doesn't "
    "trust by default. Run `pip install -r requirements.txt` (it installs truststore, so "
    "Python trusts the same certificates as your browser) and fetch again."
)
_TLS_HINT_WITH_TRUSTSTORE = (
    "Certificate verification failed even against your operating system's trust store. "
    "Ask IT for the root certificate your network uses to inspect HTTPS and point "
    "REQUESTS_CA_BUNDLE at a bundle containing it plus the public roots (see RUN.md), "
    "or fetch from another network. Don't turn verification off: it is what stops a "
    "tampered list from reaching the screen."
)
_HINTS = {
    "proxy": (
        "A proxy refused the connection. In a Claude Code cloud session, add "
        "'scsanctions.un.org' and 'unsolprodfiles.blob.core.windows.net' (where the UN "
        "redirects the download) under the environment's Network access > Allowed domains, "
        "or run this fetch on your own machine."
    ),
    "network": (
        "Couldn't download from the server. Check your internet connection and try again; "
        "the error details are printed above."
    ),
}


def _use_os_trust_store() -> bool:
    """Verify TLS against the operating system's trust store -- what the browser
    trusts -- instead of only certifi's bundle. Returns False when truststore isn't
    installed, leaving certifi in place. truststore also patches the SSL context
    requests preloads at import, so calling this after ``import requests`` is fine."""
    try:
        import truststore
    except ImportError:
        return False
    truststore.inject_into_ssl()
    return True


def _classify(err: BaseException) -> str:
    """Bucket a download failure: 'tls' (certificate not trusted), 'proxy' (a proxy
    refused the connection) or 'network' (anything else)."""
    if isinstance(err, RuntimeError) and err.__cause__ is not None:
        err = err.__cause__
    if isinstance(err, requests.exceptions.SSLError):
        return "tls"
    if isinstance(err, requests.exceptions.ProxyError):
        return "proxy"
    return "network"


def _retryable(err: BaseException) -> bool:
    """Only transient failures are worth retrying. Certificate and proxy-policy
    failures, and HTTP 4xx answers, come back the same every time."""
    if _classify(err) != "network":
        return False
    if isinstance(err, requests.exceptions.HTTPError) and err.response is not None:
        return err.response.status_code >= 500
    return True


def _download(url: str, dest: Path, retries: int = 4) -> dict:
    delay = 2
    last_err: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with requests.get(url, headers=HEADERS, stream=True, timeout=60) as resp:
                resp.raise_for_status()
                sha = hashlib.sha256()
                size = 0
                with open(dest, "wb") as fh:
                    for chunk in resp.iter_content(chunk_size=65536):
                        if chunk:
                            fh.write(chunk)
                            sha.update(chunk)
                            size += len(chunk)
            return {
                "url": url,
                "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "bytes": size,
                "sha256": sha.hexdigest(),
            }
        except Exception as err:  # noqa: BLE001 - surface any transport error
            last_err = err
            if attempt == retries or not _retryable(err):
                break
            time.sleep(delay)
            delay *= 2
    raise RuntimeError(f"failed to download {url}: {last_err}") from last_err


def fetch(only: list[str] | None = None) -> dict:
    trust_os = _use_os_trust_store()
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest: dict = {}
    if MANIFEST.exists():
        manifest = json.loads(MANIFEST.read_text())

    keys = only or list(SOURCES)
    failures: dict[str, str] = {}  # source key -> failure class
    for key in keys:
        url, filename = SOURCES[key]
        dest = RAW_DIR / filename
        print(f"-> {key}: {url}")
        try:
            meta = _download(url, dest)
            meta["file"] = filename
            manifest[key] = meta
            print(f"   ok  {meta['bytes']:,} bytes  @ {meta['fetched_at']}")
        except Exception as err:  # noqa: BLE001
            failures[key] = _classify(err)
            print(f"   FAIL {err}", file=sys.stderr)

    MANIFEST.write_text(json.dumps(manifest, indent=2))

    if failures:
        print(f"\n{len(failures)} of {len(keys)} downloads failed.", file=sys.stderr)
        for kind in sorted(set(failures.values())):
            if kind == "tls":
                hint = _TLS_HINT_WITH_TRUSTSTORE if trust_os else _TLS_HINT
            else:
                hint = _HINTS[kind]
            print(f"- {hint}", file=sys.stderr)
    return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description="Fetch the UN Security Council sanctions list.")
    ap.add_argument(
        "--only",
        nargs="*",
        choices=list(SOURCES),
        help="fetch only these source keys (default: all)",
    )
    args = ap.parse_args()
    fetch(only=args.only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
