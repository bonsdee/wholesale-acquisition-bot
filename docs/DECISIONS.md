# Decision record

Decisions made before and during the build, against the architecture spec (Draft 1.0).
Spec section numbers in brackets.

## Pre-build (14 Sep 2026)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Section 1 scope change | Treated as settled. No fabricated competing offers, no independent identities. The offer ladder [6.4] with a genuine 48h expiry is what gets built. | ACL s18 / LMCT exposure; the spec's own reasoning. |
| 2 | Scope for day one | Phases 1–3 of the build sequence [12]: ingestion + data model + fact store, valuation engine offline, state machine with scripted messages. No language model in the loop. | Proves ingestion, economics and conversational flow before any generation risk. |
| 3 | Stack | Python 3.11+ / FastAPI, Pydantic, SQLAlchemy 2 + Alembic, pg8000 (was psycopg 3 until 15 Sep — see 61). | Spec's preference [11]; numeric valuation engine sits naturally in Python. |
| 4 | Queue | Postgres-backed jobs table, claimed with `FOR UPDATE SKIP LOCKED`, retries with exponential backoff. No Redis, no Celery. | Durability beats throughput at this volume [11]; one fewer hosted dependency; Celery is awkward on Windows. |
| 5 | Database | Supabase, used as plain Postgres (SQL migrations, no Supabase client library). | No Docker on the dev machine; app stays portable to any Postgres. |
| 6 | First contact | Upstream gets the seller into the dealership's Facebook Page inbox before sending the lead — via an `m.me/<page>?ref=<lead_id>` link. The Messenger referral webhook carries `lead_id`, which is how the thread links to the lead. PSID match on `seller.platform_id` is the fallback. | The Messenger Platform API cannot open a conversation with a user who has not messaged the Page. |
| 7 | Upstream lead source | Not built yet. Figure 2 is the contract it must emit; `acqbot simulate-lead` stands in for it. | — |
| 8 | Dealership identity | Config placeholders (`ACQBOT_DEALERSHIP_NAME`, `ACQBOT_DEALERSHIP_LMCT`, `ACQBOT_AGENT_NAMES`) until confirmed. | — |
| 9 | Calibration data | None yet. The Phase 2 harness ships with a synthetic fixture; real calibration waits for 200+ historical transactions [12, phase 2]. | — |
| 10 | Code delivery | Zip file per milestone. | — |

## Design decisions made while building

| # | Decision | Choice | Why |
|---|---|---|---|
| 11 | Append-only enforcement | Database triggers, not convention. `messages`, `state_log`, `valuations`, `market_data`, `lead_duplicates` are strictly immutable. `vehicle_facts.superseded_by`, `offers.outcome/outcome_at` and `escalations.resolved_*` are write-once (NULL → value exactly once; every other column frozen). `leads`, `threads`, `jobs` are operational pointers and stay mutable — their history is in `state_log` and `messages`. | [2.1] "Every store is append-only"; the trigger makes it true for a human with psql, not just for the app. |
| 12 | Dedupe key | Fingerprint = normalised make / model / year. Odometer applied as a tolerance in the query (10% or 3,000 km) rather than hashed. | A bucketed odometer creates boundary misses (84,900 vs 85,100 km). [3.2] |
| 13 | Upstream `lead_id` | Used as our primary key. A re-delivered webhook is idempotent, not a duplicate. | Distinguishes retries from relistings. |
| 14 | PPSR timing | Runs the moment a VIN (or a rego that resolves to one) is known: at ingestion when the listing has one, otherwise `rego` is the first field DISCOVERY asks for and enrichment re-runs. Hard gate before VERIFICATION either way. Written-off / stolen → `TERMINATED` before any contact. | PPSR needs a VIN; the contract has VIN and rego nullable [3.1]. |
| 15 | PPSR unavailable | Retryable provider error → job retries with backoff, nothing goes out. Non-retryable → escalation `ppsr_unavailable`. | Hard gate must not fail open. |
| 16 | Fact confidence | Seller facts: 0.6 from the listing, 0.9 stated in conversation ("confirmed"), 1.0 verified. DISCOVERY exit needs ≥ 0.9 or verified; VERIFICATION exit needs PPSR / rego / photo sources. | Gives "confirmed odometer" [5.1] a concrete meaning. |
| 17 | Source authority | inspection > ppsr > rego = vin > photo > seller. A weaker fact arriving after a stronger one is kept in the chain but never becomes current. | [6.2] verified beats claimed. |
| 18 | Contradiction | A superseded, unverified seller claim whose verified superseder differs materially (odometer: > 2,000 km or 5%; booleans: flipped; else: not equal). Surfaces in the fact sheet's `contradicted` block and re-enters DISCOVERY [6.2]. | Revised claims are signal the closer needs [10]. |
| 19 | `valuations.band` | Two columns `band_low` / `band_high` = uncertainty on `wholesale_max`; the ladder is cut from the point estimate and stored as JSON. `basis` ∈ {indicative, verified} — only `verified` can be released. | The handoff example [9] reads as an uncertainty band, not the ladder range. |
| 20 | Market data | Guide and comps results live in `market_data`, not `vehicle_facts`. | They are valuation inputs, not facts about the vehicle. |
| 21 | Escalation states | `ESCALATED` is logged as the event, `HUMAN` is the resting state; both transitions land in `state_log`. | Matches Figure 3 while keeping one resting state. |
| 22 | High-value trigger | Fires after enrichment (so the human gets the PPSR result), on the listing's asking price vs `ACQBOT_HIGH_VALUE_THRESHOLD_AUD` (default 60,000). | [5.2] |
| 23 | Stub providers | Deterministic by input hash, with magic suffixes to steer outcomes (see `enrichment/stubs.py`). Real providers implement the same Protocols and are selected by config. | Reproducible tests; no pipeline change when real APIs arrive. |
| 24 | Provider hints | `decode()` / `lookup()` accept an optional `hint` (the seller's claims). Real providers ignore it; stubs use it to produce coherent data. | — |

## Phase 3 decisions

| # | Decision | Choice | Why |
|---|---|---|---|
| 25 | Thread ↔ lead linking | `m.me/<page>?ref=<lead_id>` referral first; fallback match on `seller.platform_id`; otherwise the thread sits in `/admin/threads/unlinked` for a person to link. | Messenger only exposes a PSID once the seller has messaged the Page. |
| 26 | Seller messages first | The opening reply is sent only once enrichment is complete (`maybe_send_opening` job fires from both the thread link and enrichment completion, whichever is last). | [3.3] "All enrichment completes before the first message is sent." |
| 27 | Extraction without a model | One field per turn, parsed by rules; a reply that cannot be parsed gets one clarification, then the no-progression counter (3 turns) escalates. | Phase 3 proves flow and transport without generation risk [12]. |
| 28 | "Are you a bot?" | Fixed disclosure answer is sent, then the lead is escalated to a person. | Section 8 requires a straight answer; 5.2 lists it as a trigger. |
| 29 | Other 5.2 triggers | No reply is generated; the lead goes to HUMAN with the message attached. | [5.2] "the pending model response is discarded". |
| 30 | Tasks vs escalations | Both live in `escalations`. An *escalation* moves the lead to HUMAN (automation stops). A *task* (`verification_review`, `offer_presentation`, `handoff`, `offer_expired`, `send_window_closed`) leaves the automation running. | One queue for the console, two different meanings. |
| 31 | Odometer verification in Phase 3 | Photos are received but not read by a model, so a `verification_review` task asks a person to confirm the odometer from the dash photo. The valuation is `indicative` until then and is never released. | [6.2] no released value may depend on an unverified claim. Phase 4 automates this. |
| 32 | Offer presentation mode | `ACQBOT_AUTO_PRESENT_OFFER=false`: a person presents every offer (`POST /admin/leads/{id}/present-offer`) and every counter escalates. `true`: automation presents the opening and concedes to step_1 and step_2 on pushback. | [12] phases 3–4 keep offers human; phase 5 flips the flag. |
| 33 | Floor and above | Automation can never present `floor`; after step_2 it states the ceiling and escalates `above_authorised_ladder`. A person can present `floor` or any amount (`step=human`). | [6.4] "hard ceiling; not reachable by the model"; "anything above floor is a human decision". |
| 34 | Counter below our offer | Treated as acceptance at our current offer. | — |
| 35 | Rejection vs pushback | "Not interested / no thanks / keep it" is final → REJECTED → ARCHIVED with the offer left open until expiry. "Too low / do better / I want $X" is a counter. | Keeps the door open without chasing. |
| 36 | Transitions | ENGAGED is logged once on the first reply; DISCOVERY↔VERIFICATION↔PRICED are recomputed from facts; OFFER_MADE/NEGOTIATING alternate as offers and replies land. Every change is in `state_log`. | [5] deterministic stage. |
| 37 | Timestamps | `messages.sent_at` and `offers.presented_at` are set by the app, not `now()`, so ordering inside one transaction is strict. | Stage computation compares them. |
| 38 | Windows | No `%-I` strftime, `tzdata` bundled for `Australia/Melbourne`, no Docker, no Celery. | Dev machine is Windows. |

## Fixes after first live run (14 Sep 2026)

| # | Defect | Fix |
|---|---|---|
| 39 | `parse_panel` graded "Good overall, a couple of small scratches on the rear bumper" as **fair**, because the word *bumper* was in the fair-inference list and was tested before the explicit grade words. On the first live lead this added a $650 recon line instead of $150 and cost ~$600 off the ladder. | An explicit grade word (excellent/good/fair/poor) now wins outright; damage inference only runs when no grade was stated, and a body-part noun alone no longer implies damage. |
| 40 | `conversation_summary` dumped raw dict fields into the closer's packet — "Finance owing (PPSR): owing False", "Mechanical faults, warning lights: none True". | Per-field renderer producing prose ("no finance owing", "no mechanical faults reported", "registered until 2026-11-02"), with an explicit line naming which condition facts are the seller's word only, and encumbrance/write-off rendered in caps when present. |
| 41 | `GET /` returned a bare 404, which is the first thing anyone sees after `acqbot serve`. | Root route returns service name, version and the endpoint map. |

## Phase 4 — the model in the loop (15 Sep 2026)

| # | Decision | Detail | Why / spec reference |
|---|---|---|---|
| 42 | Two models, two jobs | Haiku 4.5 (`claude-haiku-4-5-20251001`) extracts on every inbound; Sonnet 5 (`claude-sonnet-5`) writes discovery replies. Separate prompts, separate context windows. | [7.1] "Extraction and persuasion are different tasks with different failure modes and should not share a context window." [11] Haiku for extraction; Sonnet or Opus for conversation. |
| 43 | "Temperature zero" without a temperature | Sampling temperature is no longer a Messages API parameter. The extraction step gets its determinism from a strict structured-output schema, `effort=low`, and code-side coercion of every value; nothing the model returns reaches the fact store unvalidated. | [7.1] intent, current API. |
| 44 | Extraction wire format | The model returns every fact as a short string in a fixed per-field format (`owing:5000`, `current:2027-03`, `full`); `llm/schemas.py` coerces into the same shapes the Phase 3 parsers produced, drops anything outside the vocabulary, and records what it dropped. | Keeps the schema well inside structured-output limits and keeps one fact vocabulary across phases. |
| 45 | Regex screens stay as a floor | The 5.2 screens (legal, deceased estate, distress, minor/no authority, "are you a bot?", hostile, STOP) run on every message underneath the model; flags are a union. A model-flagged trigger the regex missed also escalates. | A safety check that depends on a network call is not a safety check. |
| 46 | Confidence threshold | Model-extracted facts below `ACQBOT_FACT_MIN_CONFIDENCE` (0.7) are not recorded; the field is asked again, and the writer is told the vague answer so it can ask specifically. | [5.1] a claim recorded at 0.9 must be a plain statement, not a guess. |
| 47 | The planner decides, the model words | Code still picks what the next message does (ask / clarify / contradiction / photos / verification wait) and hands the model an instruction plus the scripted wording. `proposed_state` from the model is logged as advisory and never acted on. | Invariant 2: the model never decides the stage. |
| 48 | Gate → one rewrite → script | A draft that fails the gate goes back to the model with the violations, once; a second failure sends the scripted template. A failed API call sends the template immediately. Both are recorded in `messages.validation_notes` (`generator`, `attempts`, `fallback`). | [7 stage 6] "reject and retry, or escalate" — the script is the safe alternative to escalating on a wording problem. |
| 49 | What the model may write in Phase 4 | Discovery only: field questions, clarifications, contradiction queries, partial-photo follow-ups, the verification wait. Opening + disclosure, the "are you a bot?" answer, opt-out acknowledgement, every offer/concession/ceiling/accept/reject line stay scripted. | [12] "Language model for discovery only." [8] disclosure wording versioned and stable. |
| 50 | Valuation structurally absent | The generation prompt builder has no parameter for the valuation or ladder; the context block prints `Valuation: NOT RELEASED` unconditionally. Tests assert no `$` and no ladder in any generation request. | [7.2] "a number it does not hold cannot be leaked". |
| 51 | Fact-sheet consistency at the gate | New gate rules for generated text: an odometer figure must match the fact sheet (or the two values of a contradiction being queried); a vehicle make other than the seller's is rejected. | [7 stage 6] "no vehicle fact absent from the fact store". |
| 52 | Full trace capture in Postgres | `model_calls` stores every call whole (system, messages, schema, response, tokens, latency, error) under the same immutable trigger as `messages`; `messages.prompt_hash` is the SHA-256 of the exact request plus the prompt version. `acqbot prompt <msg_id>` reconstructs it. | [11] "Full LLM trace capture is not optional"; [10] `prompt_hash` "lets you reconstruct precisely which prompt". Langfuse/Helicone can be added later; the data is already there. |
| 53 | Rolling summary on the thread | Last 15 turns verbatim; once 25 unfolded turns exist the oldest are folded into `threads.history_summary` by the extraction model. A failed summary widens the window rather than dropping turns. | [7.3]. |
| 54 | Deferrals and notes | "I'll check tonight" moves that field to the back of the queue while others remain, then it is asked once more at the end. Things the model notices that are not fields (`seller_notes`) ride into the handoff packet's `flags`. Neither counts as progress for the no-progression counter. | [9] "the flags array carries anything the model noticed that was not a structured field". |
| 55 | Process knowledge is a short whitelist | `llm/knowledge.py` is the only source the model may answer process questions from; anything else is deferred to the named agent. Placeholder wording until the business confirms it; versioned with the prompt. | Never invent dealership process. [A.1] disclosure and wording review. |
| 56 | Prompt caching not used yet | Structured outputs and prompt caching are documented as interacting awkwardly; the system prompt is ~1k tokens, so the saving is small. Revisit once volume justifies it. | Cost. |
| 57 | Deterministic fake client | `fake` provider answers extraction with the Phase 3 parsers and generation with the scripted wording, so the whole Phase 4 path (schemas, coercion, gate retries, traces, summaries) runs in tests and demos with no network and gives a transcript identical to template mode. | Tests must not depend on a paid API; the live smoke test covers the real one when a key is present. |
| 58 | Webhooks enqueue, the worker converses | `/webhooks/messenger` and `/webhooks/sms` verify, parse and enqueue an `inbound_message` job (deduplicated by channel message id, 3 attempts); `handle_inbound` persists the seller's message idempotently by external id so a retry after a failed send does not double-record it. One worker per deployment until the queue orders jobs per thread. | Model latency (seconds) must not sit on a webhook Meta expects answered quickly; redeliveries happen. |
| 59 | Quoting our figure back is not a yes | A figure at or below the current offer reads as acceptance only when nothing in the message pushes back; "$5,600 is too low" is a counter. Found while reviewing decision 34. | Decision 34 as written would have booked a rejection as an acceptance. |
| 60 | A missing API key never crashes a turn | `ACQBOT_LLM_PROVIDER=anthropic` with an empty key logs one error and runs scripted; `chat`, `demo --model anthropic` and `model-check` refuse up front with the fix. Any non-API exception from the SDK (the "could not resolve authentication method" TypeError) is wrapped as a non-retryable `ModelError`, so the template fallback applies. | Found on the first run on Claudia's machine (15 Sep). |
| 61 | Pure-Python database driver | psycopg 3's bundled libpq DLL was blocked on Claudia's machine by Windows Application Control ("An Application Control policy has blocked this file"), which took every command down. Switched to pg8000 (pure Python, SCRAM-capable); `db.engine_args` rewrites `postgresql+psycopg://` URLs and translates libpq's `sslmode` (require → TLS without verification, verify-full → verified) so existing .env files keep working. The append-only tests now match on the trigger message rather than the driver's exception class. | A dependency that a security policy can silence is not a dependency this system can have. |

## Open — waiting on inputs

- Target margin by segment and transport cost (config placeholders: 10%, $250).
- Dealership name, LMCT number, named buyer identities.
- Disclosure wording review [8, A.1]; process notes in `llm/knowledge.py` (inspection, payment, pick-up) to confirm.
- An Anthropic API key for the first real-model runs (`acqbot model-check`, `acqbot chat --model anthropic`).
- Human SLA on escalation and handoff [A.1].
- Stall / nudge cadence [A.2], re-engagement policy [A.2], concurrency per identity [A.2].
- Recon from photographs vs verbal-until-inspection [A.2] — default verbal until inspection.
