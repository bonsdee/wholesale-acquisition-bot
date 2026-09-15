# acqbot — Wholesale Vehicle Acquisition Chatbot

Automated seller engagement and vehicle appraisal for wholesale acquisition. Consumes qualified
private-seller leads, conducts a structured discovery conversation, produces a bounded valuation,
presents a firm offer within pre-authorised limits, and hands a complete deal packet to a human
closer.

**Status** Phases 1–4 of 6 complete · 187 tests · Not production ready — see
[Before going live](#before-going-live)
**Stack** Python 3.11+ · FastAPI · PostgreSQL · SQLAlchemy 2 · Alembic · Claude API
**Jurisdiction** Victoria, Australia — LMCT-regulated activity

---

## Overview

A lead arrives from an upstream sourcing platform over a signed webhook. It is validated against a
strict versioned contract, deduplicated against the last ninety days, and enriched — registration
lookup, VIN decode, PPSR, trade guide, auction comparables — before any contact is attempted. A
written-off or stolen result ends the lead before a message is ever sent.

The conversation then collects the ten facts that gate pricing. Every claim the seller makes is
recorded as a claim, never as a fact; when a verified source later contradicts one, the lead
re-enters discovery and the contradiction is preserved for the closer. Once identity and the
statutory checks are verified, the valuation engine produces a ceiling and a four-step offer ladder.
The conversation may express those four figures and nothing else.

On acceptance, a handoff packet is written containing everything a salesperson would otherwise have
to re-ask: confirmed facts, unverified claims, contradictions, the PPSR result, the valuation with
its reconditioning line items and comparables, the agreed price, and the full transcript.

## How it works

```mermaid
flowchart TD
    U[Upstream sourcing platform] -->|signed webhook| I
    I[Ingestion<br/>validate · dedupe · persist] --> E[Enrichment<br/>rego · VIN · PPSR · guide · comps]
    E -->|written off / stolen| X[Terminated<br/>before contact]
    E --> O[Orchestrator]
    O <--> S[State machine<br/>deterministic]
    O <--> V[Valuation engine<br/>pure computation]
    O --> G[Validation gate]
    G --> T[Transport<br/>console · Messenger · SMS]
    T --> O
    O --> H[Handoff packet] --> C[Human closer]
    O -.escalations.-> C
```

| Subsystem | Responsibility | Module |
|---|---|---|
| Ingestion | Contract validation, deduplication, claimed-fact capture | `ingestion/` |
| Fact store | Append-only facts with supersession chains and contradiction detection | `facts/` |
| Enrichment | Provider-agnostic lookups; PPSR as a routing gate | `enrichment/` |
| Valuation engine | Base value, condition, reconditioning, ceiling, offer ladder | `valuation/` |
| State machine | Deal stage computed from collected facts | `conversation/state.py` |
| Validation gate | Every outbound message checked before it is sent | `conversation/gate.py` |
| Orchestrator | Per-message processing loop | `conversation/service.py` |
| Language model | Fact extraction and discovery replies, behind the gate; every call traced | `llm/`, `conversation/extract_model.py`, `conversation/compose.py` |
| Transport | Channel abstraction the orchestrator cannot see through | `transport/` |
| Queue | Durable Postgres-backed jobs with retry and backoff | `queue/` |

## Design invariants

Four properties hold everywhere, each enforced structurally rather than by convention.

**The language model never computes a price.** Every monetary value originates in the valuation
engine, a pure module with no network and no model access. The conversation receives four
authorised figures as data.

**The language model never decides the stage.** Deal stage is recomputed from the fact store on
every inbound message. A lead cannot reach an offer before the facts required to price it exist.

**No figure can be expressed that is not authorised.** The validation gate rejects any outbound
message containing a dollar amount before the `PRICED` stage, or any amount that is not one of the
four ladder values. This is code at the send boundary, not an instruction in a prompt.

**Conversation evidence is immutable.** Messages, state transitions, valuations and market data
cannot be updated or deleted — enforced by database triggers, so the guarantee survives direct
access to the database. Fact supersession, offer outcomes and escalation resolutions are
write-once. A disputed transaction can be reconstructed exactly as it happened.

## The model in the loop

Two models with two jobs, neither of which is deciding anything. On every inbound message a small
model (Haiku) reads the seller's words and returns facts in a fixed format; code coerces each value
into the fact vocabulary or drops it, and the regex screens from Phase 3 still run underneath as a
floor — a legal threat the model misses is still caught. The state machine then recomputes the
stage from the fact store exactly as before.

The reply is planned by code: the planner decides what the next message *does* — ask for the
odometer, clarify a vague answer, raise a contradiction, ask for the remaining photos, say the
checks are running — and hands that instruction, with the scripted wording attached, to a stronger
model (Sonnet). The model writes it in its own words. The gate checks the draft; if it fails, the
violations go back to the model for one rewrite; if that fails too, the scripted wording is sent
instead. The model can neither block a conversation nor send an unchecked sentence.

What the model holds is deliberately narrow. Its context carries the stage, the outstanding
fields, the fact sheet split into verified / claimed / contradicted, the last fifteen turns
verbatim with older turns folded into a rolling summary, and the instruction for this turn. It
never carries the valuation or the ladder — there is no code path that puts them there — so a
figure it does not hold cannot be leaked. In this phase it writes discovery messages only; the
opening, the disclosure, the "are you a bot?" answer, every offer and every closing line remain
scripted.

Every call is stored whole in `model_calls` — system prompt, messages, schema, response, tokens,
latency, error — under an immutable trigger, and every generated message records the model,
the prompt version and the hash of the exact prompt that produced it. `acqbot prompt <msg_id>`
reconstructs that prompt on demand.

```powershell
# .env:  ACQBOT_ANTHROPIC_API_KEY=sk-ant-...     (console.anthropic.com → API keys)
uv run acqbot model-check                          # verifies the key and both models with one tiny call each
uv run acqbot demo --model anthropic --seller accept
uv run acqbot chat --model anthropic               # you play the seller, the model writes the replies
uv run acqbot demo --model fake                    # the whole Phase 4 path with no network (tests use this)
uv run acqbot model-calls <lead_id>                # trace: purpose, model, tokens, latency, errors
uv run acqbot prompt <msg_id>                      # the exact prompt behind an outbound message
```

Without `ACQBOT_ANTHROPIC_API_KEY` the system runs on scripted templates exactly as in Phase 3.

## Compliance posture

The system identifies itself as automated in its first message, names the dealership and licence
number, and answers a direct question about whether the seller is talking to a person with a
straight answer followed by a handover. It never references other buyers, competing offers, or
market competition that has not been supplied as verified data, and it never uses binding
commitment language — all three are gate rules, not guidance.

The forty-eight hour offer expiry is real: valuation inputs move, stock position changes, and the
offer is recorded with its expiry and expires on schedule.

An earlier design that simulated competing offers from multiple seller-facing accounts was
deliberately not implemented. See `docs/DECISIONS.md` (decision 1).

---

## Getting started

### Prerequisites

- Python 3.11 or later — or none, and let `uv` provide it
- [uv](https://docs.astral.sh/uv/) — `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
- A PostgreSQL 14+ database. Supabase works as plain Postgres; no Docker required.

### Install

```powershell
uv sync        # runtime and dev tools (pytest, ruff) — dev tools are a uv dependency group
```

### Database

Take a connection string from your provider and adjust it in two ways: the scheme must be
`postgresql+pg8000://` (a pure-Python driver — no native DLL to be blocked; the earlier
`postgresql+psycopg://` scheme is accepted and rewritten), and `?sslmode=require` should
be appended for any hosted database.

On Supabase, use the **Session pooler** connection rather than the direct one. Direct connections
resolve over IPv6 only, which fails on IPv4-only networks with `getaddrinfo failed`. The session
pooler uses port 5432 and a username of the form `postgres.<project-ref>`. Do not use the
transaction pooler on port 6543 — it does not support the session features migrations require.

If the database password contains `@ # % / ?`, percent-encode it.

### Configure

```powershell
Copy-Item .env.example .env
```

At minimum set `ACQBOT_DATABASE_URL`, `ACQBOT_LEAD_WEBHOOK_SECRET` and `ACQBOT_ADMIN_TOKEN`.
Generate secrets with:

```powershell
uv run python -c "import secrets; print(secrets.token_urlsafe(48))"
```

### Initialise

```powershell
uv run acqbot migrate
```

This creates the schema and the append-only triggers. Verify the resolved connection at any time
without exposing the password:

```powershell
uv run python -c "from acqbot.config import get_settings as g; u=g().database_url; print(u.split('://')[0], '|', u.split('@')[-1])"
```

---

## Running

### Without any external services

Every external provider has a deterministic stub, so the whole pipeline runs offline against your
database alone.

```powershell
uv run acqbot demo --auto-offer      # scripted seller, full pipeline, prints the transcript
uv run acqbot chat                   # you play the seller
```

Scenarios: `clean`, `encumbered`, `written-off`, `stolen`, `contradiction`, `no-identifiers`,
`high-value`, `expired-rego`.
Seller scripts: `accept`, `negotiate`, `reject`, `bot_question`, `legal`, `silent_pushback`.

```powershell
uv run acqbot demo --scenario contradiction --seller accept --auto-offer
```

### Services

```powershell
uv run acqbot serve      # API on http://127.0.0.1:8000 — interactive docs at /docs
uv run acqbot worker     # job worker: enrichment, valuation, inbound messages, sends, expiries
```

### Lead intake and inspection

```powershell
uv run acqbot simulate-lead --count 5 --ingest --enrich          # direct to the database
uv run acqbot simulate-lead --count 3 --post http://127.0.0.1:8000/leads   # signed, as upstream will
uv run acqbot lead <lead_id>                                      # state, facts, market data, history
uv run acqbot value <lead_id>                                     # valuation with full working
uv run acqbot calibrate --csv historical_transactions.csv         # calibration report
uv run acqbot queue                                               # job counts by kind and status
```

### Human console

All endpoints under `/admin` require the `x-admin-token` header and are disabled entirely when
`ACQBOT_ADMIN_TOKEN` is unset.

| Endpoint | Purpose |
|---|---|
| `GET /admin/escalations` | Open work queue |
| `POST /admin/escalations/{id}/resolve` | Close an item; optionally return the lead to automation |
| `GET /admin/leads/{id}/transcript` | Full message history with template or model version and gate result |
| `GET /admin/leads/{id}/valuation` | Latest valuation with its complete input snapshot |
| `POST /admin/leads/{id}/facts` | Record a verified fact; triggers revaluation |
| `POST /admin/leads/{id}/present-offer` | Present a ladder step, or an above-ladder amount as a human decision |
| `POST /admin/leads/{id}/offer-outcome` | Record an outcome that happened off-channel |
| `GET /admin/handoffs` · `POST /admin/handoffs/{id}/claim` | Deal packets awaiting a closer |
| `GET /admin/threads/unlinked` · `POST /admin/threads/{id}/link` | Conversations that could not be matched to a lead |

### Messenger

Sellers reach the dealership Page through an `m.me/<page>?ref=<lead_id>` link, and the referral
webhook carries the lead id that links the conversation. Configure the webhook at
`/webhooks/messenger` with `ACQBOT_MESSENGER_VERIFY_TOKEN`, subscribing to `messages`,
`messaging_referrals` and `messaging_postbacks`.

The Messenger Platform cannot open a conversation with someone who has not messaged the Page
first, and outbound is limited to twenty-four hours after the seller's last message. Conversations
that go quiet beyond that window continue over SMS.

Both webhooks verify, parse and enqueue; the worker runs the conversation loop. The webhook
therefore answers in milliseconds regardless of model latency, a redelivered event is deduplicated
by message id, and a reply that fails on the way out is retried without recording the seller's
message twice. Run one worker per deployment unless per-thread ordering is added to the queue.

---

## Conversation states

```mermaid
stateDiagram-v2
    [*] --> NEW
    NEW --> TERMINATED: PPSR written off or stolen
    NEW --> CONTACTED: first message delivered
    CONTACTED --> ENGAGED: seller replied
    ENGAGED --> DISCOVERY
    DISCOVERY --> VERIFICATION: required facts collected
    VERIFICATION --> PRICED: checks verified, valuation released
    PRICED --> OFFER_MADE
    OFFER_MADE --> NEGOTIATING: seller countered
    NEGOTIATING --> OFFER_MADE: authorised concession
    NEGOTIATING --> DISCOVERY: new material facts
    OFFER_MADE --> ACCEPTED
    NEGOTIATING --> ACCEPTED
    ACCEPTED --> HANDOFF
    OFFER_MADE --> REJECTED
    REJECTED --> ARCHIVED
    DISCOVERY --> STALLED: no reply
    DISCOVERY --> HUMAN: escalation
    NEGOTIATING --> HUMAN: above authorised ladder
    HANDOFF --> [*]
```

Stages in the main sequence are computed from the fact store on every message. Terminal and
human-owned states are set by events and are never recomputed.

---

## Project layout

```
src/acqbot/
  config.py              Settings (ACQBOT_* environment variables)
  models.py              Data model, job queue, handoff packets
  contracts/lead.py      Inbound lead contract, versioned and strict
  ingestion/             Signature verification, fingerprinting, ingest service
  facts/                 Field registry and the append-only fact store
  enrichment/            Provider protocols, deterministic stubs, enrichment pipeline
  valuation/             Engine (pure), service (persistence), calibration harness
  conversation/          State machine, extraction (rules + model), planner/composer, templates, gate,
                         orchestrator, history, handoff
  llm/                   Model client, prompts, output schemas and coercion, trace capture, fakes
  transport/             Transport protocol; console, Messenger, SMS
  queue/                 Postgres-backed job queue and worker
  api/                   Lead webhook, channel webhooks, admin console
  simulator.py           Lead generator standing in for the upstream platform
  demo.py                Scripted seller runs
  cli.py                 Command line
alembic/versions/        0001 schema and append-only triggers · 0002 handoff packets · 0003 model calls
docs/                    DECISIONS.md · PHASES.md
fixtures/                Synthetic calibration data
tests/
```

## Development

### Tests

The suite drops and recreates the schema on the database it is pointed at, and refuses to run
unless the database name contains `test`. Use a separate database — never one holding real leads.

```powershell
$env:ACQBOT_TEST_DATABASE_URL = "postgresql+pg8000://<user>:<password>@<host>:5432/postgres_test?sslmode=require"
uv run pytest
```

End-to-end tests drive scripted sellers through the complete pipeline over the console transport
and assert gate discipline on every outbound message — once on scripted templates and once through
the Phase 4 model path with a deterministic fake client. Two tests call the real Claude API and are
skipped unless `ACQBOT_ANTHROPIC_API_KEY` is set:

```powershell
$env:ACQBOT_ANTHROPIC_API_KEY = "sk-ant-..."
uv run pytest tests/test_live_model.py -v
```

### Migrations

```powershell
uv run alembic revision --autogenerate -m "description"
uv run acqbot migrate
uv run alembic check      # detect drift between models and schema
```

Downgrades are intentionally unsupported. Conversation data is never destroyed by a migration.

### Code style

```powershell
uv run ruff format src tests
uv run ruff check src tests
```

---

## Build status

| Phase | Scope | Status |
|---|---|---|
| 1 | Ingestion, data model, fact store | Complete |
| 2 | Valuation engine, offline | Built; **not calibrated** |
| 3 | State machine, scripted messages, transport | Complete |
| 4 | Language model for discovery | Complete; not yet run against a real seller |
| 5 | Automated offer presentation | Behind `ACQBOT_AUTO_PRESENT_OFFER`; approval workflow outstanding |
| 6 | SMS transition and nudge sequences | Not started |

With `ACQBOT_AUTO_PRESENT_OFFER=false` (the default) a person presents every offer and every
counter routes to a person. The automation collects facts, verifies, prices and escalates.

## Before going live

This system is not ready to contact a real seller. In order of lead time:

**The valuation engine has never seen a real price.** Trade guide, auction comparables, PPSR, VIN
decode and registration lookup are all deterministic stubs. The arithmetic is correct and tested;
the inputs are invented. Commercial agreements with a guide provider and an auction data source are
the longest lead items on the project.

**The calibration gate has not been passed.** The engine must be run against at least two hundred
historical transactions with known purchase price and actual reconditioning spend, and tuned until
the error distribution is acceptable. The harness exists and reports the right measures. The data
does not.

**The market-conditions term is inert.** `market_adj` is implemented but nothing populates days'
supply or current stock position, so it evaluates to zero. Slow-moving and fast-moving models are
currently priced identically.

**Statutory and platform access is outstanding.** PPSR access via AFSA or an accredited reseller;
Meta app review for `pages_messaging`, which requires business verification; an Australian mobile
number for two-way SMS.

**Configuration is still placeholder.** Dealership name, licence number, buyer identities, target
margin by segment, transport cost, high-value escalation threshold and the human response SLA.

**The prompts have not met a real seller.** Every model call is traced, the gate and the scripted
fallback are exercised in tests, and the live smoke test passes against the API — but the wording
has only been tested on scripted sellers. Run `acqbot chat --model anthropic` and read
`model_calls` for a few dozen conversations before pointing it at Messenger. Error tracking
(Sentry) is still to be added.

**The process notes are placeholders.** `llm/knowledge.py` is the only source the model may answer
process questions from (inspection, payment, pick-up). Until the business confirms them, the model
defers everything else to the named agent.

Confirmation that the dealer licence covers automated first contact as designed should be obtained
from someone qualified to give it.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `ACQBOT_DATABASE_URL` | PostgreSQL connection | local |
| `ACQBOT_LEAD_WEBHOOK_SECRET` | Shared secret for inbound lead signatures | — |
| `ACQBOT_ADMIN_TOKEN` | Enables and guards `/admin`; unset disables it | unset |
| `ACQBOT_DEALERSHIP_NAME` · `_LMCT` · `_AGENT_NAMES` | Identity used in every message | placeholder |
| `ACQBOT_TARGET_MARGIN_PCT` · `_BY_SEGMENT` | Target margin | 0.10 |
| `ACQBOT_TRANSPORT_COST_AUD` | Per-vehicle transport | 250 |
| `ACQBOT_LADDER_OPENING` · `_STEP_1` · `_STEP_2` | Ladder multipliers | 0.88 · 0.93 · 0.97 |
| `ACQBOT_OFFER_EXPIRY_HOURS` | Offer validity | 48 |
| `ACQBOT_HIGH_VALUE_THRESHOLD_AUD` | Escalation threshold | 60000 |
| `ACQBOT_DEDUPE_WINDOW_DAYS` | Relisting suppression window | 90 |
| `ACQBOT_AUTO_PRESENT_OFFER` | Automation presents offers and concessions | false |
| `ACQBOT_MIN_PHOTOS` | Photographs required to exit discovery | 6 |
| `ACQBOT_HUMAN_SLA_HOURS` | Escalation and handoff response target | 4 |
| `ACQBOT_*_PROVIDER` | Enrichment provider selection | stub |
| `ACQBOT_ANTHROPIC_API_KEY` | Enables the language model | unset |
| `ACQBOT_LLM_PROVIDER` | `auto` · `anthropic` · `fake` · `off` | auto |
| `ACQBOT_EXTRACTION_MODEL` · `_CONVERSATION_MODEL` | Which model reads, which model writes | Haiku 4.5 · Sonnet 5 |
| `ACQBOT_LLM_GATE_RETRIES` | Rewrites allowed after a gate rejection | 1 |
| `ACQBOT_HISTORY_VERBATIM_TURNS` | Turns kept verbatim before summarising | 15 |
| `ACQBOT_FACT_MIN_CONFIDENCE` | Below this a model-extracted fact is not recorded | 0.7 |

See `.env.example` for the complete list.

## Documentation

- `docs/DECISIONS.md` — every design decision with its rationale and the specification section it
  answers, including deliberate departures
- `docs/PHASES.md` — what each build phase delivers

Internal system. Not for distribution.
