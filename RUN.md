# Running TradeCheck

A question moves through three layers, as in the team's workflow diagram:

1. **Layer 1:** the hosted OpenSanctions matcher (yente) scores candidates, and our own copy of the UN Security Council list cross-checks it.
2. **Layer 2:** fixed cutoffs turn the top score into a band: clear below 0.70, review from 0.70, hit from 0.90. A failed match check is Unknown, never clear.
3. **Layer 3:** for review and hit cases only, a model writes a report from the evidence, and code checks every citation before the report is shown.

Review cases wait in a review queue, and every step is written to an append-only audit log. Design: [`docs/superpowers/specs/2026-10-08-tradecheck-architecture-design.md`](docs/superpowers/specs/2026-10-08-tradecheck-architecture-design.md).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
```

Fill in the three keys in `.env`. Never commit it (it is in `.gitignore`), and never paste a key into an issue, a chat or a log.

| Key | Used for |
| --- | --- |
| `OPENSANCTIONS_API_KEY` | Layer 1 match and Layer 3 full records (the team's free key) |
| `OPENROUTER_API_KEY` | Model calls: extracting the party and writing the report |
| `TAVILY_API_KEY` | News search in Layer 3 reports |

## 1. Download the UN list

```bash
python -m ingest.fetch
```

This writes `data/raw/un_sc.xml` and `data/raw/manifest.json`, which records the URL, `fetched_at`, byte count and SHA-256. Without the file the UN check is unavailable, and no result can be Clear\*.

## 2. Screen from the command line

```bash
python -m tradecheck.pipeline "Is KHAWA PANGA MANDRO sanctioned?" "Can we sell to Chief Kahwa?" "Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico?"
```

The command reads `.env` itself. Each line shows the label, score, top candidate, UN check, where the fields came from (`model` or `fallback`), whether the match was live, the report check and attempts, the time taken and the screen ID. Add `--full` to print each whole result as JSON.

Every screen makes live API calls and spends credits: one model call to extract the party and one OpenSanctions match. A Caution or Avoid result adds an OpenSanctions entity lookup, a Tavily search and up to four model calls for the report. Starting this command or the web app adds one OpenSanctions `/catalog` call. The tests make no calls.

## 3. Run the web app

```bash
uvicorn app.main:app --env-file .env
```

Open http://127.0.0.1:8000. The app reads `.env` itself at startup, so `--env-file .env` is optional. A key that is already set in the shell wins over the file, and the startup log names any key that is still missing. Restart uvicorn after you edit `.env`.

## Tests (offline)

```bash
python -m pytest
```

No test reaches the network: every outside service is faked.

## Demo cases

Measured in the live check on 2026-10-08. Type each question exactly as written: the saved responses are keyed to the fields extracted from it.

| Label | Question | Score | Time | Notes |
| --- | --- | --- | --- | --- |
| Avoid | Is KHAWA PANGA MANDRO sanctioned? | 1.00 | 25-29 s | Model report passed on the first attempt every time. "Chief Kahwa" is also Avoid (an exact OpenSanctions alias), and so is the question with "in Uganda" added. |
| Caution | Can we sell to Khawa Mandro? | 0.83 | 20-37 s | The listed name without its middle name. Goes to the review queue. If the report call passes 40 s, the code-written template is shown instead. |
| Clear\* | Can we ship to AgroDistribuidora del Bajío SA de CV in Mexico? | 0.00 | 4-6 s | No candidate; the UN check agrees. |

- Over about two dozen live screens the free OpenSanctions key returned no HTTP 429.
- With all three keys made invalid in the shell, all three cases replayed from saved responses (`live=False`, `parsed=saved`): Avoid, Caution and Clear\*.
- The live-check review cases were dismissed with the note "live check, not a real case", so the queue starts empty.

## Troubleshooting

### `CERTIFICATE_VERIFY_FAILED: unable to get local issuer certificate`

Python reached the server but couldn't verify its certificate. When every download or call
fails this way at once, the cause is almost always your network, VPN or antivirus inspecting
HTTPS: it re-signs traffic with its own root certificate, which your operating system (and so
your browser) trusts but Python's default bundle (`certifi`) doesn't.

1. Run `pip install -r requirements.txt`. It installs `truststore`, so TradeCheck verifies
   against your operating system's certificates, the same ones your browser uses. Then
   try again.
2. Still failing? Check whether it's interception. Does
   `python -c "import requests; requests.get('https://www.google.com')"` fail the same way?
   Does the browser's padlock show the certificate issued by Zscaler, Netskope, Fortinet, an
   antivirus or your university, rather than a public authority such as DigiCert or Entrust?
   If so, ask IT for that root certificate and point `REQUESTS_CA_BUNDLE` at a PEM bundle
   containing it plus the public roots. Or use another network (home Wi-Fi, a phone hotspot).

Never set `verify=False`: verification is what stops a tampered sanctions list or answer from
reaching the screen.

### OpenRouter answers `No endpoints found that can handle the requested parameters`

`provider.require_parameters` sends the request only to providers that support every setting,
including strict JSON output and `seed`. If no provider does, delete the `"seed": SEED,` line in
`chat()` in `tradecheck/http.py`, note the change here, and run the live check again.

### Every result is Unknown, or the fields always fall back

Check that all three keys are filled in in `.env`, then restart uvicorn. The audit record
(`/audit/<screen_id>`) says why: the `layer1` event's `error`, or the `input` event's
`parsed.fallback_reason`.

### `Tunnel connection failed: 403` (cloud sessions)

The session's network policy blocks the hosts. Allow `api.opensanctions.org`, `openrouter.ai`,
`api.tavily.com`, `scsanctions.un.org` and `unsolprodfiles.blob.core.windows.net` (where the UN
redirects the download), or run on your own machine.

## Data sources

| Source | Used for | Licence |
| --- | --- | --- |
| OpenSanctions `sanctions` collection (hosted API) | Layer 1 match, full records, data date | CC BY-NC 4.0: non-commercial use only |
| UN Security Council consolidated list | UN cross-check | Public government data |
| Tavily news search | News in review and hit reports | Tavily's terms of service |
