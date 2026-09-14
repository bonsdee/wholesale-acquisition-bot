# Decision record

Decisions made before and during the build, against the architecture spec (Draft 1.0).
Spec section numbers in brackets.

## Pre-build (14 Sep 2026)

| # | Decision | Choice | Why |
|---|---|---|---|
| 1 | Section 1 scope change | Treated as settled. No fabricated competing offers, no independent identities. The offer ladder [6.4] with a genuine 48h expiry is what gets built. | ACL s18 / LMCT exposure; the spec's own reasoning. |
| 2 | Scope for day one | Phases 1–3 of the build sequence [12]: ingestion + data model + fact store, valuation engine offline, state machine with scripted messages. No language model in the loop. | Proves ingestion, economics and conversational flow before any generation risk. |
| 3 | Stack | Python 3.11+ / FastAPI, Pydantic, SQLAlchemy 2 + Alembic, psycopg 3. | Spec's preference [11]; numeric valuation engine sits naturally in Python. |
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

## Open — waiting on inputs

- Target margin by segment and transport cost (config placeholders: 10%, $250).
- Dealership name, LMCT number, named buyer identities.
- Disclosure wording review [8, A.1].
- Human SLA on escalation and handoff [A.1].
- Stall / nudge cadence [A.2], re-engagement policy [A.2], concurrency per identity [A.2].
- Recon from photographs vs verbal-until-inspection [A.2] — default verbal until inspection.
