"""Download error handling in ingest.fetch. No network: requests.get is mocked."""

import pytest
import requests

from ingest import fetch


@pytest.fixture
def calls(monkeypatch):
    """Make requests.get raise the configured error, count the calls, skip backoff sleeps."""
    state = {"n": 0, "error": None}

    def fake_get(*args, **kwargs):
        state["n"] += 1
        raise state["error"]

    monkeypatch.setattr(fetch.requests, "get", fake_get)
    monkeypatch.setattr(fetch.time, "sleep", lambda seconds: None)
    return state


def _http_error(status):
    resp = requests.Response()
    resp.status_code = status
    return requests.exceptions.HTTPError(f"{status} error", response=resp)


@pytest.mark.parametrize(
    "error, kind, attempts",
    [
        # deterministic: fail fast
        (requests.exceptions.SSLError("certificate verify failed"), "tls", 1),
        (requests.exceptions.ProxyError("Tunnel connection failed: 403 Forbidden"), "proxy", 1),
        (_http_error(404), "network", 1),
        # transient: retried with backoff
        (_http_error(503), "network", 4),
        (requests.exceptions.ConnectionError("connection reset"), "network", 4),
    ],
)
def test_download_failure_handling(calls, tmp_path, error, kind, attempts):
    calls["error"] = error
    dest = tmp_path / "list.xml"
    with pytest.raises(RuntimeError) as exc:
        fetch._download("https://example.invalid/list.xml", dest, retries=4)
    assert calls["n"] == attempts
    assert fetch._classify(exc.value) == kind
    assert not dest.exists()


@pytest.mark.parametrize(
    "trust_os, expected",
    [
        (False, "pip install -r requirements.txt"),
        (True, "even against your operating system's trust store"),
    ],
)
def test_certificate_failure_gets_tls_hint_not_proxy_hint(
    calls, tmp_path, monkeypatch, capsys, trust_os, expected
):
    calls["error"] = requests.exceptions.SSLError("certificate verify failed")
    monkeypatch.setattr(fetch, "RAW_DIR", tmp_path)
    monkeypatch.setattr(fetch, "MANIFEST", tmp_path / "manifest.json")
    monkeypatch.setattr(fetch, "_use_os_trust_store", lambda: trust_os)

    fetch.fetch(only=["un_sc"])

    err = capsys.readouterr().err
    assert "Certificate verification failed" in err
    assert expected in err
    assert "Allowed domains" not in err  # no longer blamed on the cloud proxy
