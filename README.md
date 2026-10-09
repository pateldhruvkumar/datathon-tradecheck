# TradeCheck

**A free sanctions check for Canadian small businesses, with the source and date behind every answer. A chatbot's "looks fine" isn't due diligence.**

## The problem

### It's real, and it starts at home

I'm in Canada, and this is built for Canadian small businesses first.

The spark: a textile business owner in India spent **over four months** trying to find and assess trading partners in Canada — calling friends here (including me), searching online for names, browsing Instagram for sellers. I was part of that search, and I watched how much guesswork went into decisions that deserved facts.

The same wall stands on this side of the Pacific: a BC farmer wondering whether they can sell crops in Mexico, the Caribbean, or the US; a Canadian jewellery maker exporting for the first time; an importer in Surrey who needs to vet a new overseas supplier. And there's a Canadian legal reason this matters: Canadians doing business abroad must comply with Canadian sanctions law (SEMA) — "I didn't know" is not a compliance strategy.

**Root cause:** the information exists, but it is spread across government portals and sanctions lists in different countries, formats, and languages. One question every one of these businesses must answer first: *"Is this name on a sanctions list?"* Today that means checking the US, EU, UK, UN and Canadian lists separately, or paying for an enterprise tool built for someone else.

### Who this is for (and who it's not for)

Multinationals already have their own compliance stacks and legal teams. **This is for the Canadian small, private, family-owned business** — the BC farmer eyeing Mexico, the Vancouver artisan exporting for the first time, the Surrey importer vetting a new supplier. They have a laptop and a deadline, not a compliance department. They get self-serve answers: no sales call, no enterprise contract.

### Measured baseline (first pass run Oct 6, 2026)

Test buyer: **"AgroDistribuidora del Bajío SA de CV"** (fictional Mexican distributor). Question: *is it sanctioned under Canadian, US, EU, UK, or UN lists?*

| Question | Minutes | # sources | Confidence (1–5) |
| --- | --- | --- | --- |
| Is my buyer/distributor sanctioned — under Canadian, US, EU, UK, or UN lists? | \~20 | 10+ across 5 portals | 2 |

What the run surfaced:

- **Five jurisdictions, five different doors.** OFAC has its own web search (with a confidence slider); the UN publishes a raw 5,285-line XML with no name search; the EU list lives on a data-portal page; the UK has a gov.uk publications page plus a separate FCDO search tool; Canada's consolidated SEMA/JVCFOA list sits with Global Affairs Canada. Different formats, different update cadences (OFAC: no fixed schedule; OFSI: every business day; EU: multiple times/week; UN: irregular).
- **Proving a negative by hand is the hard part.** Finding a *hit* is easy; being *confident there is no hit* across five portals — each searched separately, in different formats — is what eats the time. OFAC itself warns that a clean result in its own tool "does not immunize you from liability."
- **Free single-list tools exist; a free unified one doesn't.** OFAC's own search, third-party OFAC checkers, per-list mirrors (some rate-limited, e.g. 10 searches/hour) — each covers one jurisdiction. None gives one free search across all five with explained matches.

**Chatbot test:** asked a general-purpose chatbot the same question. Result: **unverifiable** — no live list access, no dated sources; the only honest answer is abstention, and abstention isn't due diligence. (Even for a *known* sanctioned name from our UN sample, the chatbot answers from stale training data and cannot confirm current listing status or cite list + date.)

*First pass run by AI assistant using the same public portals a human would; the team should re-run by hand and replace these numbers before Demo Day.*

## Why not just ask AI?

Fair question. We asked it ourselves, and here's what we found:

1. **AI answers from memory; we answer from live records, with receipts.** Sanctions lists change daily and a model's training data is months old. When a chatbot doesn't know, it guesses confidently. A wrong "you're clear" before a trade deal is dangerous.
2. **You can't show a chat transcript to a bank.** This produces a record: these lists, this date, this match, this evidence. That's the actual product for a small business doing due diligence.
3. **Chatbots can't say "I don't know."** Ours can, and does. Missing data shows as *Unknown*, never green. We think that honesty is the whole differentiation.
4. **Same input, same answer.** Ask a chatbot twice, get two answers. Ours runs the same matching logic every time, so results are testable and comparable.

## Why this lasts beyond the datathon

Honestly, the valuable part isn't the code, it's the data pipeline. A daily-refreshed sanctions dataset with transparent matching stays useful as long as governments keep publishing lists, which is forever. Enterprise vendors serve compliance teams; nobody serves the small exporter, and that gap doesn't close on its own. Every honest "unknown" and every dated source earns the next user's trust. And it grows: sanctions screening is module one, tariffs and firm checks come next. Same pipeline, same users.

## Data evidence

| Source | What it gives | Access | Licence / terms | Status |
| --- | --- | --- | --- | --- |
| OpenSanctions | Consolidated dataset from dozens of official lists — US, EU, UK, UN, **Canada (SEMA)**, plus PEPs and debarment; bulk CSV/JSON, updated daily | Hosted matcher API (team key); bulk download is free | **CC-BY-NC 4.0 — free for non-commercial use** (fits the datathon; commercial use needs a paid licence) | Layer 1 since Build Session 3, through the hosted matcher (yente) |
| UN Security Council Consolidated List | Direct source pull | Free XML | Public government data | ✅ Verified live pull Oct 3, 2026 — 5,285-line XML |
| Reference pattern | `gsmiguel/sanctions_lists_etl`: 4-list (OFAC/EU/UN/UK) ETL running daily via GitHub Actions | Open source | — | Proves the pipeline pattern is feasible |

**Key simplification:** instead of four separate ingestion pipelines, OpenSanctions already consolidates the major lists into one daily dataset. Our verified UN direct pull stays as a provenance cross-check.

### Signal check (done Oct 3, on a 10-record UN sample)

- Searching **"kawa"** → 1 person (KHAWA PANGA MANDRO) with **8 aliases**; confidence from the UN's own alias-quality labels: primary-name match 100, "Good" alias 85, "Low" alias 60.
- Searching **"dipen"** → clean no-hit verdict, no false positives in the sample.

**Test discipline:** the test set must include at least one known sanctioned entity per screened list. *A screen that never returns a hit is untested.*

## How we grade a hit

**Name matching:**

1. Normalize: lowercase, strip legal suffixes (Ltd, PLC, SAS, SARL, KK, …), fold accents.
2. Join on exact registry/ID first; fuzzy-match names only as fallback.
3. Fuzzy threshold ≈ 90 = **hit to review, never an automatic match**. Tune on test cases.

**The grades:**

| Level | Rule |
| --- | --- |
| **Avoid** | Strong sanctions hit on the subject |
| **Caution** | Fuzzy hit needing review, or conflicting identifiers |
| **Unknown → Caution** | No data for this name/list. Never shown as clear |
| **Clear\*** | No hits across all lists checked |

\* "Clear" always shows: *"Based on sources X as of date Y. Not legal advice."* Missing data must never render as green.

## Scope

**This microproduct (Build Sessions 2–3 + Demo Day):** one search box over consolidated sanctions data with transparent, explainable matches — built for the small business use case above.

**Out of scope for the datathon:** legal advice, automated block/allow decisions, transaction monitoring.

**After Demo Day:** extend the same pattern to tariffs/duties by product code and firm-registry checks. One place for "can I trade X with country Y?" The world-map explorer idea, but grounded in official data.

## Demo script (3 cases, live)

1. **Clear** — "AgroDistribuidora del Bajío SA de CV" (fictional Mexican buyer): no hits across all lists → Clear\*, with "checked at" timestamp and the lists screened.
2. **Caution** — "Khawa Mandro": the listed name without its middle name scores 0.83 → Caution, flagged for human review in the review queue, where the presenter confirms or dismisses it with a note. ("Chief Kahwa" turned out to be an exact OpenSanctions alias, so it scores 1.00 → Avoid.)
3. **Avoid** — "KHAWA PANGA MANDRO": exact primary-name hit on the UN Consolidated List → Avoid, with identifiers, program, listing date, and source link.

The three cases walk the full verdict taxonomy on real data. Questions and measured times are in [`RUN.md`](RUN.md#demo-cases).

## Key links

**Data sources**

- OpenSanctions — bulk downloads and docs: https://www.opensanctions.org/ · https://www.opensanctions.org/docs/
- UN Consolidated List (raw XML): https://scsanctions.un.org/resources/xml/en/consolidated.xml
- OFAC Sanctions List Search: https://sanctionssearch.ofac.treas.gov/
- EU consolidated list (data portal): https://data.europa.eu/data/datasets/consolidated-list-of-persons-groups-and-entities-subject-to-eu-financial-sanctions?locale=en
- UK: GOV.UK — search "OFSI consolidated list of targets" (CSV/XLS download)
- Canada: Global Affairs Canada — Consolidated Canadian Autonomous Sanctions List (SEMA/JVCFOA)

**References**

- Trilemma Request for Microproducts: https://build.trilemma.foundation/docs/request-for-microproducts
- Build Session 1 checklist: https://github.com/TrilemmaFoundation/Datathon-Season-2026/blob/main/build%20session%20checklist/build-session-1.md
- Pipeline pattern reference (4-list ETL, daily): https://github.com/gsmiguel/sanctions\_lists\_etl/blob/HEAD/README.md

## How it works (Build Session 3)

**Decided Oct 8:** the team's workflow diagram ("How a question moves through TradeCheck") is the plan. It replaces the Oct 7 US + Canada direct-pull pipeline. One call to the hosted OpenSanctions matcher covers the US, Canada, EU, UK and UN lists, and our own copy of the UN list cross-checks it. OpenSanctions data is CC BY-NC 4.0: fine for the datathon, and a paid licence is needed before any commercial use. Design: [the spec](docs/superpowers/specs/2026-10-08-tradecheck-architecture-design.md). Commands: [`RUN.md`](RUN.md).

```
question ─► model extracts the party ─► Layer 1: OpenSanctions match + UN cross-check
         ─► Layer 2: bands (clear < 0.70 ≤ review < 0.90 ≤ hit; failed check = Unknown)
         ─► Layer 3, review and hit only: full record + news + model report, citations checked in code
         ─► review: analyst queue, then reviewed · hit: flagged · every step: append-only audit log
```

- **The matcher decides; the model explains.** Model output never changes a score, band, label or status.
- **Missing data is never clear.** A failed match check is Unknown. An unavailable UN check or a failed extraction blocks Clear\*.
- **Every claim cites evidence**, and code checks each citation before the report is shown.
- **The audit log is append-only**, enforced by database triggers.

**Stack:** Python, FastAPI, SQLite, rapidfuzz and one static HTML page, with OpenSanctions (hosted yente), OpenRouter (`qwen/qwen3.8-27b`) and Tavily.

### Open risks

- **Licence:** OpenSanctions data is non-commercial (CC BY-NC 4.0). Commercial use needs a paid licence.
- **Attribution:** CC BY-NC requires crediting OpenSanctions. The credit is in the UI footer and this README.
- **Placeholder cutoffs:** 0.70 and 0.90 are starting points, not tested numbers. Tune them on a labelled test set.
- **Third parties see the names:** counterparty names go to OpenSanctions, OpenRouter and Tavily. That's fine for the demo; disclose it before real users.
- **Staleness:** OpenSanctions refreshes four times a day, and our UN copy is fetched by hand. The UN cross-check flags the gap instead of hiding it.

## Team

- Dipen Modi, Mansi Purohit, Dhruv Patel
- Who owns what: Dipen - data, UI; Mansi - TBD; Dhruv - TBD.

## Repo status

Build Session 1 (Oct 5): framing and this README. Build Session 2 (Oct 7): a US + Canada direct-pull pipeline, since replaced. Build Session 3 (Oct 9): the three-layer flow above, with an OpenSanctions match plus UN cross-check, score bands, cited model reports, a review queue and an append-only audit log, tested offline with `python -m pytest`. See [`RUN.md`](RUN.md).
