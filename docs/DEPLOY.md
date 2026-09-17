# Deploying

The README explains running this on a laptop. This is the other thing: putting it on a server where
a seller can reach it. Written for whoever holds the infrastructure, not for the dealership.

Nothing here is exotic — two processes, one database, four webhook URLs — but every item is
load-bearing and the failure modes are quiet, which is the whole reason Section 4.1 warns about a
system that "reports healthy while the pipeline is dead".

## What runs

Two processes against one database. They share a codebase and a `.env`, and neither is optional.

| Process | Command | What it does | If it stops |
|---|---|---|---|
| **API** | `uvicorn acqbot.api.app:create_app --factory --host 0.0.0.0 --port 8000` | Receives leads and webhooks, serves the console | Meta and Twilio get errors and retry; sellers' messages queue up at the platform, then get dropped |
| **Worker** | `acqbot worker` | Runs the conversation, the valuation, the nudges, the expiries | **The bot goes silent.** The API keeps answering 200 and nothing is ever replied to |

The second row is the dangerous one. A stopped worker looks exactly like a quiet afternoon. That is
what `queue_moving` in `/health` exists to catch — watch it.

Run **one worker**. Per-thread ordering is not implemented in the queue, so two workers can process
two messages from the same seller out of order.

### Process supervision

Anything that restarts on exit: systemd units, a container orchestrator, a process manager. Both
processes are stateless — all state is in Postgres — so restarting either is always safe, and an
in-flight job is picked up by the next worker after ten minutes (`requeue_stale`).

## The database

Postgres 16. The append-only guarantees are enforced by triggers, so they survive anything that
connects — including a person with psql, including us.

- **Connection**: `ACQBOT_DATABASE_URL`. On Supabase use the **Session pooler on port 5432**. Never
  the transaction pooler on 6543: it does not hold the session state SQLAlchemy needs and you will
  get intermittent, confusing failures rather than an honest connection error.
- **Migrations**: `acqbot migrate` on every deploy, before the new code starts serving. Migrations
  are additive; there are no destructive migrations on conversation data, by design.
- **Backups**: this database *is* the audit trail. Messages, offers, valuations and state changes
  cannot be altered or deleted, which is worth nothing if the whole thing is lost. Daily backups
  minimum, and restore one somewhere before you need to.

## Configuration

Copy `.env.example`, fill it in, and keep it out of version control. `ACQBOT_ADMIN_TOKEN` and the
database password are the two that matter most — the first is the only thing in front of the
console, the second is in front of every conversation you have ever had.

Set `ACQBOT_ENVIRONMENT=production` and `ACQBOT_RELEASE` to the deployed commit, so error reports
say which version broke.

### The switches that change behaviour

| Setting | Start at | Move to |
|---|---|---|
| `ACQBOT_AUTO_PRESENT_OFFER` | `false` — a person presents every offer | `true` once you have read a few dozen transcripts and trust the wording |
| `ACQBOT_LLM_PROVIDER` | `anthropic` | `off` runs scripted templates only, which is a genuine fallback if the API is down |
| `ACQBOT_ALLOW_UNSIGNED_LEADS` | **never `true` in production** | — |
| `ACQBOT_SMS_MIGRATION` | `true` | `false` if Twilio is not set up yet |

## URLs to register

The API must be reachable over HTTPS at a stable hostname before any of these can be set.

| Where | URL | Notes |
|---|---|---|
| Upstream lead source | `POST https://<host>/leads` | HMAC-signed with `ACQBOT_LEAD_WEBHOOK_SECRET` |
| Meta → your app → webhooks | `https://<host>/webhooks/messenger` | Verify token is `ACQBOT_MESSENGER_VERIFY_TOKEN`. Subscribe to `messages`, `messaging_referrals`, `messaging_postbacks` |
| Twilio → your number → messaging | `https://<host>/webhooks/sms` | |
| Uptime monitor | `https://<host>/health` | 200 while serving, 503 when it cannot |

## The console is not public

`/console` is one shared token with no per-user accounts and no sign-in audit. That is adequate for
a back office and not adequate on the open internet.

Put it behind a VPN or an authenticating proxy, and give it a **different hostname** from the
webhooks — the webhook endpoints must be reachable by Meta and Twilio, and the console must not be
reachable by anyone else. Two hostnames, one application, different network rules.

## What to watch

```
GET  /health          every minute, from an uptime monitor
acqbot doctor         by hand, or from cron with the exit code wired to an alert
```

`/health` returns 503 only when the service genuinely cannot work — currently that means the
database is gone. Everything else reports `ok: false` with a 200, because taking the API out of
rotation would not help anyone clear a backlog.

The seven checks, and what each one means when it fails:

| Check | Means |
|---|---|
| `database` | The service is down. This is the only one that returns 503. |
| `queue_moving` | Jobs are due and nothing is running them. **The worker has stopped.** |
| `jobs_dead` | Jobs exhausted their retries. Something is broken in a handler; look at Sentry. |
| `human_queue` | Leads are past the SLA. Not a software fault — nobody is working the queue. |
| `deals_unclaimed` | Agreed deals nobody picked up. The most expensive failure here: a seller has been told yes and is waiting. |
| `wording` | Most outbound in the last hour fell back to the script. The prompt, the ladder or the fact sheet has broken. |
| `model` | Model calls are failing. Usually an expired key or an empty balance. |

Set `ACQBOT_SENTRY_DSN` for crashes. Without it the system still runs and still logs; it just has
nobody to tell. The worker also runs the checks above every fifteen minutes and reports failures
through the same channel.

Nothing sent to Sentry carries a seller's words — `send_default_pii` is off and the events carry ids
and counts only.

## Rolling back

Deploys are: pull, `uv sync`, `acqbot migrate`, restart both processes. To roll back, deploy the
previous commit and restart — **do not roll back migrations.** They are additive and the older code
ignores columns it does not know about; reversing one would mean deleting conversation data, which
the triggers will refuse anyway.

## First run, in order

1. Database created, `acqbot migrate` applied, backups configured.
2. `.env` complete. `acqbot doctor` passes.
3. API and worker running under supervision, `/health` green from outside.
4. `acqbot simulate-lead --count 1 --post https://<host>/leads` — a synthetic lead runs end to end
   on the console transport without touching Messenger.
5. Register the Meta and Twilio webhooks. Send yourself a message from a personal account.
6. `ACQBOT_AUTO_PRESENT_OFFER=false`, and read every transcript for the first week.
