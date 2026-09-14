# AI-Assisted Mini Lead Management System

A small FastAPI and SQLite lead-management backend. It imports the supplied CRM export, normalizes useful fields, supports lead operations, identifies likely duplicates, and extracts structured acquisition sources from free text.

Running instance: **https://wiz-leads.rafiakem.tech** (dashboard at `/`, interactive API docs at `/docs`). Nothing needs to be installed locally to review it.

## Run it

Requires Python 3.11 or later.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload
```

The first start creates `data/leads.db` and imports `data/leads_seed.csv`. The import is idempotent: later starts reuse the database. Open `http://127.0.0.1:8000/docs` for the interactive OpenAPI interface.

To load or reset the database explicitly and print the required data checks:

```bash
python -m scripts.load_data --reset
```

Run tests:

```bash
python -m pytest -q
```

To use another database, set `LEADS_DATABASE_PATH` before starting the application.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/leads` | List and paginate leads |
| `GET` | `/leads/{id}` | Get one lead |
| `PATCH` | `/leads/{id}` | Change status, owner, or notes |
| `GET` | `/leads/export` | Export the current filtered view as CSV |
| `POST` | `/leads/ingest` | Create or safely update leads from website forms |
| `POST` | `/leads/dedupe-candidates` | Rank likely duplicate groups |
| `POST` | `/source/extract` | Extract a structured source from raw text, with the answering layer and latency |
| `GET` | `/dashboard` | Count leads by status and source channel |
| `GET` | `/llm-budget` | Current fallback limits, mode, and model |

`GET /leads` and `/leads/export` accept `status`, `owner`, `country`, and `q`. Search covers name, company, and email. Listing also accepts `limit` and `offset`.

Examples:

```bash
curl "http://127.0.0.1:8000/leads?status=qualified&country=Singapore&q=logistics"
curl -X PATCH http://127.0.0.1:8000/leads/100234811 \
  -H "Content-Type: application/json" \
  -d '{"status":"Qualified","owner":"Marcus Wong"}'
curl -X POST http://127.0.0.1:8000/leads/dedupe-candidates \
  -H "Content-Type: application/json" -d '{"threshold":0.8}'
```

`POST /leads/ingest` accepts one supplied website-form object or an array of them. The response states `created` or `updated` for each object.

`POST /leads/dedupe-candidates` accepts an optional `limit` (1 to 500, default 50). The response separates `count` (groups found) from `returned` (groups in this payload) and reports `truncated`. Every returned pair carries its own `reasons` and both lead records, so a reviewer can render a comparison without a second request. Internal scoring fields (`normalized_*`, `email_domain`) are not exposed.

## Data decisions

SQLite is the best fit for this bounded exercise: it is durable, queryable, transactional, and needs no external service. The schema stores the useful populated CRM fields plus internal normalized values. Columns empty across the complete seed file (`City`, `Annual Revenue`, `Marketing contact status`, `GDPR consent`, and `Original Source Drill-Down 1`) are not carried into the active model. The original CSV remains the raw source.

Import performs these changes:

- trims whitespace and normalizes status labels;
- uses `Full Name` when present, otherwise first and last name;
- parses ISO timestamps, ISO dates, and US-style dates into ISO timestamps;
- normalizes email, phone digits, names, and company names into separate matching fields;
- strips common legal suffixes only from the internal company-matching value;
- derives source channel and detail from notes while retaining the original source.

The public fields stay human-readable. Matching values remain internal.

## Deduplication design

The implementation is deterministic entity resolution, not an LLM. It uses RapidFuzz for fast, reproducible string scores.

### Candidate generation

`app/dedupe.py` creates candidate pairs from:

1. the same last eight normalized phone digits;
2. the same email domain with email-localpart similarity of at least 80%;
3. the same normalized company with name similarity of at least 70%.

Only records that pass one of these blocks are scored. Blocks above 50 records are skipped because weak large blocks can grow quadratically. A set removes pairs found through more than one block. This avoids comparing all $n(n-1)/2$ pairs.

### Scoring and grouping

RapidFuzz scores the normalized name, company, and email localpart. Exact email or matching last-eight phone digits receives high weight. Conflicting phone digits reduce confidence. Confident pairs are joined with union-find, so connected pairs such as A-B and B-C become one review group. Every group includes its best confidence, human-readable evidence, and the supporting pair scores, and each pair keeps its own reason list so a reviewer can see which fields agreed. Nothing is merged automatically.

The confidence threshold is configurable from 0 to 1 and defaults to `0.72`. A production evaluation would label a representative sample and tune this threshold from measured precision, recall, and false-merge cost.

### Ingest idempotency

Ingest updates an existing lead when normalized email or normalized phone matches. Otherwise, it inserts a new lead. Repeating the same submission updates the same record rather than creating a duplicate. An update keeps the record ID and creation date, fills missing contact fields, and appends new notes without replacing existing notes.

## Source extraction design

`app/source_extraction.py` uses high-precision rules over notes, original source, and optional form-page context. It returns one allowed channel: `Website`, `Event`, `LinkedIn`, `Organic Search`, `Referral`, `Manual/Sales`, or `Other`.

The order resolves strong evidence first: LinkedIn, event, referral, organic search, manual/sales, then website. Regex captures useful details such as an event name or referring person.

Unmatched text goes through a provider seam. Mock mode is the safe default. Copy `.env.example` to `.env` for local configuration. Never commit `.env` or an API key.

```dotenv
SOURCE_LLM_MODE=gemini
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-3.6-flash
LLM_RATE_PER_IP_HOUR=30
LLM_RATE_GLOBAL_DAY=800
LLM_PROVIDER_MAX_CONCURRENCY=4
TRUSTED_PROXIES=
```

`gemini-3.6-flash` is the current Flash model returned by the Gemini API for this account. The Gemini adapter requests structured JSON and validates the returned channel. Network failures, invalid output, or a missing key degrade to a functional rule-only result. Tests inject the mock adapter, so the suite does not use the network.

Every response states which layer answered, so the fallback is observable rather than assumed:

```json
{"channel": "Event", "detail": "SFF - Booth QR Code", "answered_by": "rules",
 "latency_ms": 0.26, "model": null, "llm_throttled": false}
```

`answered_by` is `rules` when the deterministic pass matched and the model was never called, `llm` when Gemini returned a valid classification, and `fallback` when the model was unavailable, errored, returned unusable output, or was deliberately skipped. A response is always HTTP 200: throttling degrades the answer, it does not fail the request.

The fallback was verified against the real API on a 12-note sample drawn from the 244 notes the rules leave unmatched (~88% of notes are caught instantly by rules). Observed behavior: latency of ~3.3-3.9s per call, correct classification of genuinely ambiguous text (e.g. a note describing a comment on a LinkedIn post → `LinkedIn`), and sensible `Other` verdicts where no channel fits. At that latency the fallback is appropriate for offline batch re-classification, not for per-request realtime use, which is why rules stay first in the pipeline and the LLM is a seam, not the default path.

Gemini also answers `503` under load, which was observed during testing. The adapter retries once, only for transport trouble or a `5xx`; a `4xx` or malformed output is not retried. Each real provider attempt, including a retry, consumes one budget unit. Provider work is also bounded by `LLM_PROVIDER_MAX_CONCURRENCY`; requests above that in-flight limit degrade immediately instead of occupying another application worker.

### Guarding a public fallback

The hosted demo is reachable by anyone and each unmatched note can cost a model call, so `app/llm_budget.py` enforces `LLM_RATE_PER_IP_HOUR` in a sliding 60-minute window and `LLM_RATE_GLOBAL_DAY` across 24 hours. Rules hits are free. A refused initial call or retry returns a degraded result with `llm_throttled: true`. Counters are thread-safe but remain in-process, so a restart clears them and multiple workers do not share them; Redis is the production upgrade path. `GET /llm-budget` reports the limits, current usage, and active mode.

Forwarded client headers are ignored by default. Set `TRUSTED_PROXIES` to the CIDR ranges of reverse-proxy peers that overwrite `X-Real-IP` or append `X-Forwarded-For`; only requests whose socket peer is in those ranges may supply a forwarded client address. Direct clients are always limited by their socket address.

### Dashboard

`GET /` serves a single static HTML dashboard with no build step and no frontend dependency. It has one tab per workflow (`Leads`, `Ingest`, `Dedupe`, `Extract`), selected through the URL hash so a specific view can be linked, and it renders the duplicate groups as field-by-field comparisons with differing values marked. The Extract tab shows the answering layer and latency, so the live fallback can be exercised from the browser.

## Tests

49 tests cover observable and ambiguous behavior:

- combined filters, search, PATCH restrictions, valid status enum, export, and missing records;
- dashboard count invariants;
- exact-email and normalized-phone ingest updates, repeated-ingest idempotency, and note preservation;
- weak identity evidence creating a new lead;
- last-eight phone blocking, fuzzy email-localpart blocking, RapidFuzz scoring, and union-find grouping;
- the similar-name/same-company false-positive trap with conflicting contact data;
- all six reachable source channels plus an injected fallback for ambiguous text;
- the answering layer per path (`rules`, `llm`, `fallback`) and the latency field;
- dedupe payload shape: per-pair reasons, both lead records, and no internal scoring fields;
- dedupe pagination: `limit` respected while `count` stays total, and `truncated` flipping correctly;
- the budget guard: per-IP and global caps, rules hits never consuming budget, and a forged `X-Forwarded-For` prefix failing to mint a fresh bucket;
- request validation on malformed input, including `limit` outside its range.

`Other` is intentionally absent from the rules-hit cases. It is the degraded label the fallback returns when nothing matches, so by construction the deterministic pass never produces it.

## Deployment

The hosted demo runs the same code at `https://wiz-leads.rafiakem.tech` with the fallback enabled (`SOURCE_LLM_MODE=gemini`), so a reviewer can exercise the live model path from the browser without any local setup. The key is supplied through an environment file outside the repository. This is a demo deployment for review, not a production deployment: it is a single uvicorn process behind nginx on SQLite, with none of the hardening a real service would need.

## Scope and limitations

I kept the requested backend and bonus dashboard, and added a single-file read-only UI plus a demo deployment so the work can be tried without cloning it. I did not add authentication, audit history, background jobs, or HubSpot migration tooling.

Known limits:

- The dashboard is a single static file with no build step; it renders the first ten duplicate groups and expands the rest client-side from the same response.
- The budget counters are in-process, so they reset on restart and do not coordinate across workers.
- SQLite `LIKE` search is sufficient for 2,049 rows but is not ranked full-text search.
- Blocking can miss a duplicate when every blocking key is changed.
- Shared phone numbers can still require human review; candidate output never auto-merges.
- Rule extraction handles known language well; the optional Gemini fallback adds external latency and nondeterminism for unmatched text.
- The dashboard reflects the current derived channel stored with each lead; changing notes through PATCH does not silently reinterpret historical attribution.

## What I would do next

1. Label a representative candidate-pair sample and tune thresholds using precision and recall.
2. Add a review workflow that records accepted and rejected duplicate suggestions.
3. Move search to SQLite FTS5, then PostgreSQL trigram indexes if volume or concurrency requires it.
4. Measure fallback accuracy and cost on a labeled unresolved-text set, then decide whether to keep it as an offline batch job or leave it live.
5. Replace the in-process budget counters with Redis so limits survive a restart and hold across workers.
6. Add migrations, authentication, audit events, structured logs, metrics, and backups only when moving beyond this exercise.
