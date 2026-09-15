# Build phases

Follows Section 12 of the spec. Each phase is independently testable.

## Phase 1 — Ingestion, data model, fact store ✅

- Section 10 schema with database-enforced append-only rules (`alembic/versions/0001_initial_schema.py`)
- Figure 2 contract, versioned and strict (`contracts/lead.py`)
- `POST /leads`: HMAC-signed, validate → dedupe (90 days, odometer tolerance) → persist → claimed facts → enqueue
- Fact store with supersession chains, source authority, contradiction detection (`facts/store.py`)
- Enrichment pipeline behind provider Protocols with deterministic stubs: rego → VIN decode → PPSR (hard gate) → guide + comps
- Postgres-backed job queue and worker
- Lead simulator with eight scenarios
- 53 tests

Not in Phase 1: any outbound contact.

## Phase 2 — Valuation engine, offline ✅

- Pure computation module (`valuation/engine.py`): guide + recency/match-weighted comps → base value; value-side condition deductions and cost-side recon line items kept apart; market adjustment; contingency that scales with the verified share of the condition picture; `wholesale_max`; ±1σ band; four-step ladder rounded to $50
- Unknown condition fields carry an expected-spend recon line rather than zero
- Service layer (`valuation/service.py`): builds inputs from the fact store and `market_data`, decides `indicative` vs `verified` basis, persists to `valuations`, escalates `encumbered_above_offer`
- Calibration harness (`valuation/calibration.py`, `acqbot calibrate`): recon MAE/bias/MAPE/p90/coverage, `wholesale_max` vs price paid, realised margin, per-segment breakdown. `fixtures/calibration_synthetic.csv` exercises it; **real calibration waits for 200+ historical transactions**
- 21 tests

## Phase 3 — State machine with scripted messages ✅

- Transport abstraction [4.3] (`transport/`): `Transport` protocol with Console (local/testing), Messenger Platform (Send API, webhook parsing, referral linking, 24h window) and Twilio-shaped SMS implementations
- State machine [5] (`conversation/state.py`): stage computed from the fact store, required fields [5.1], verification requirements, contradiction re-entry [6.2]; sticky vs computed states
- Rule-based extraction (`conversation/extract.py`): one-field-at-a-time parsers, escalation screens [5.2], offer intents, photos, phone capture
- Scripted templates (`conversation/templates.py`): disclosure in message one [8], per-field asks, clarifications, contradiction check, offer / concession / ceiling / accept / reject / human-answer / opt-out; versioned as `template:v1`
- Validation gate [7, stage 6] (`conversation/gate.py`): no figures below PRICED, ladder-only figures, no commitment / competition / pressure language, year sanity, length, tone — applied to every outbound, scripted or not
- Orchestrator (`conversation/service.py`): the seven-stage loop [Fig 4] minus the model; thread linking via `m.me ?ref=<lead_id>`; escalations discard the pending reply; "are you a bot?" answered then handed over; no-progression counter; offer lifecycle with automated concessions to step_2 and escalation above it; floor unreachable by automation
- Handoff packet [Fig 5] (`conversation/handoff.py`, `handoff_packets` table) written at ACCEPTED with SLA timer
- Human loop: `verification_review` (dash-photo odometer, until Phase 4 vision), `offer_presentation`, `offer_response_needed`, `handoff` tasks; minimal admin console at `/admin/*`
- `acqbot demo` scripted sellers; `acqbot chat` interactive seller; Messenger and SMS webhooks
- 63 tests, including end-to-end runs asserting gate discipline on every outbound

Mode: `ACQBOT_AUTO_PRESENT_OFFER=false` (default) — a person presents every offer and every counter routes to a person. `true` — Phase 5 behaviour: automation presents the opening and traverses the two authorised concessions.

## Phase 4 — Language model for discovery only ✅

- Model client (`llm/client.py`): Claude API, structured JSON output via `output_config`, error mapping; `fake` provider for tests and demos; provider chosen by `ACQBOT_LLM_PROVIDER` / the API key
- Extraction [7 stage 2] (`conversation/extract_model.py`, `llm/schemas.py`): Haiku reads every inbound in context, returns facts in a fixed wire format; code coerces into the fact vocabulary, applies a confidence threshold, keeps the regex screens as a floor; seller questions, deferrals and notes captured
- Prompt architecture [7.2] (`llm/prompts.py`): system (identity, disclosure stance, hard constraints, tone, process notes) + per-turn context (stage, outstanding fields, fact sheet, `Valuation: NOT RELEASED`, instruction) + history; `prompt:v1`
- Planner → composer [7 stages 4–6] (`conversation/compose.py`): code decides the directive, Sonnet words it, gate checks it, one rewrite with the violations, then the scripted wording; model escalation flags honoured; `proposed_state` logged only
- Gate additions: odometer and make must match the fact sheet
- Memory [7.3] (`conversation/history.py`): last 15 turns verbatim, rolling summary on the thread
- Trace capture [11] (`model_calls`, migration 0003): every call stored whole and immutable; `acqbot model-check`, `model-calls`, `prompt`
- Scope: discovery messages only; opening, disclosure, bot-question answer, offers and closes stay scripted; every offer still presented by a person
- 43 new tests (unit, end-to-end through the fake client, SDK client with a mocked transport), two of them live against the API when a key is present

Not in Phase 4: reading photos (odometer confirmation is still a human task), offer wording (Phase 5), prompt caching, Langfuse.

## Phase 5 — Automated offer presentation

Already wired behind `ACQBOT_AUTO_PRESENT_OFFER`; remaining: human approval workflow for floor and above, offer-expiry follow-through, SLA return-to-queue.

## Phase 6 — SMS transition and nudge sequences

Phone capture → thread migration Messenger→SMS; nudge cadence inside the 24h window; STALLED/ARCHIVED policy; re-engagement for relisters.
