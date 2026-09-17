"""Command line: `uv run acqbot --help`."""

from __future__ import annotations

import json
import logging
import os
import uuid
from pathlib import Path
from typing import Annotated

import typer

app = typer.Typer(no_args_is_help=True, help="Wholesale vehicle acquisition chatbot.")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")


@app.command()
def migrate(revision: str = "head") -> None:
    """Apply database migrations (alembic upgrade)."""
    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    command.upgrade(cfg, revision)
    typer.echo("migrations applied")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    """Run the HTTP API (lead webhook, lead inspector, Messenger webhook)."""
    import uvicorn

    uvicorn.run("acqbot.api.app:app", host=host, port=port, reload=reload)


@app.command()
def worker(
    once: Annotated[bool, typer.Option(help="Drain the queue and exit instead of running forever.")] = False,
) -> None:
    """Run the job worker (enrichment, and later sends, nudges, expiries)."""
    import acqbot.queue.handlers  # noqa: F401
    from acqbot.config import get_settings
    from acqbot.queue.worker import drain, run_forever

    s = get_settings()
    if once:
        n = drain(s.worker_id)
        typer.echo(f"processed {n} job(s)")
        return
    run_forever(s.worker_id, s.worker_poll_seconds)


@app.command("simulate-lead")
def simulate_lead(
    count: int = 1,
    seed: int | None = None,
    scenario: Annotated[
        str | None,
        typer.Option(
            help="clean | encumbered | written-off | stolen | contradiction | no-identifiers | high-value | expired-rego"
        ),
    ] = None,
    ingest: Annotated[bool, typer.Option(help="Ingest directly into the database (no HTTP).")] = False,
    post: Annotated[
        str | None,
        typer.Option(help="POST to this URL with a signed request, e.g. http://127.0.0.1:8000/leads"),
    ] = None,
    enrich: Annotated[bool, typer.Option(help="With --ingest: run the enrichment job immediately.")] = False,
) -> None:
    """Generate Figure 2 lead payloads. Prints JSON unless --ingest or --post is given."""
    from acqbot.simulator import make_leads

    leads = make_leads(count, seed=seed, scenario=scenario)

    if post:
        import httpx

        from acqbot.config import get_settings
        from acqbot.ingestion.security import SIGNATURE_HEADER, sign

        secret = get_settings().lead_webhook_secret
        for payload in leads:
            body = json.dumps(payload).encode()
            r = httpx.post(
                post,
                content=body,
                headers={"Content-Type": "application/json", SIGNATURE_HEADER: sign(secret, body)},
            )
            typer.echo(f"{r.status_code} {r.text}")
        return

    if ingest:
        import acqbot.queue.handlers  # noqa: F401
        from acqbot.db import session_scope
        from acqbot.ingestion.service import ingest_lead
        from acqbot.queue.worker import drain

        for payload in leads:
            with session_scope() as s:
                result = ingest_lead(s, payload)
            typer.echo(json.dumps(result.as_dict()))
        if enrich:
            n = drain("cli")
            typer.echo(f"enrichment jobs run: {n}")
        return

    typer.echo(json.dumps(leads if count > 1 else leads[0], indent=2))


@app.command()
def lead(lead_id: str) -> None:
    """Print a lead's state, fact sheet, market data and history."""
    from fastapi.testclient import TestClient

    from acqbot.api.app import app as api

    r = TestClient(api).get(f"/leads/{uuid.UUID(lead_id)}")
    typer.echo(json.dumps(r.json(), indent=2))


@app.command()
def value(lead_id: str, persist: bool = True) -> None:
    """Run the valuation engine for a lead and print the result (band, ladder, lines, warnings)."""
    from acqbot.db import session_scope
    from acqbot.valuation.service import compute_valuation

    with session_scope() as s:
        out = compute_valuation(s, uuid.UUID(lead_id), persist=persist)
        r = out.result
        typer.echo(
            json.dumps(
                {
                    "basis": out.basis.value,
                    "engine_version": r.engine_version,
                    "base_value": r.base_value,
                    "base_components": r.base_components,
                    "condition_adj": r.condition_adj,
                    "condition_lines": [line.__dict__ for line in r.condition_lines],
                    "market_adj": r.market_adj,
                    "recon_lines": [line.__dict__ for line in r.recon_lines],
                    "recon_subtotal": r.recon_subtotal,
                    "contingency": r.contingency,
                    "contingency_rate": r.contingency_rate,
                    "verified_share": r.verified_share,
                    "recon_estimate": r.recon_estimate,
                    "target_margin": r.target_margin,
                    "transport_cost": r.transport_cost,
                    "wholesale_max": r.wholesale_max,
                    "band": [r.band_low, r.band_high],
                    "ladder": r.ladder,
                    "warnings": r.warnings,
                    "escalated": out.escalated,
                },
                indent=2,
            )
        )


@app.command()
def calibrate(
    csv_path: Annotated[
        Path | None, typer.Option("--csv", help="Historical transactions CSV (see valuation/calibration.py).")
    ] = None,
    synthetic: Annotated[int, typer.Option(help="Generate N synthetic rows instead of reading a CSV.")] = 0,
    seed: int = 1,
    write_fixture: Annotated[
        Path | None, typer.Option(help="Also write the synthetic rows to this CSV path.")
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the report as JSON.")] = False,
) -> None:
    """Run the valuation engine over historical transactions and report the error distribution."""
    from acqbot.valuation import calibration
    from acqbot.valuation.service import engine_config_from_settings

    if csv_path is None and synthetic <= 0:
        raise typer.BadParameter("pass --csv PATH or --synthetic N")
    if csv_path is not None:
        rows, is_synth = calibration.load_csv(csv_path), False
    else:
        rows, is_synth = calibration.synthetic_rows(synthetic, seed=seed), True
        if write_fixture is not None:
            calibration.write_csv(rows, write_fixture)
            typer.echo(f"wrote {len(rows)} synthetic rows to {write_fixture}")
    report = calibration.run(rows, engine_config_from_settings(), synthetic=is_synth)
    typer.echo(json.dumps(report.as_dict(), indent=2, default=str) if as_json else report.render())


@app.command()
def demo(
    scenario: str = "clean",
    seller: Annotated[
        str, typer.Option(help="accept | negotiate | reject | bot_question | legal | silent_pushback")
    ] = "negotiate",
    seed: int = 42,
    auto_offer: Annotated[
        bool, typer.Option(help="Let the automation present and concede (Phase 5 mode) instead of a human.")
    ] = False,
    model: Annotated[
        str | None,
        typer.Option(
            help="Language model for this run: off (scripted), fake (deterministic, no network), anthropic. "
            "Default: whatever ACQBOT_LLM_PROVIDER / the API key say."
        ),
    ] = None,
    quiet: bool = False,
) -> None:
    """Run a scripted seller through the whole pipeline on the Console transport and print the transcript."""
    import acqbot.queue.handlers  # noqa: F401
    from acqbot.demo import run_demo

    if model == "anthropic":
        from acqbot.config import get_settings
        from acqbot.llm.registry import missing_key_reason

        reason = missing_key_reason(get_settings().model_copy(update={"llm_provider": "anthropic"}))
        if reason:
            typer.echo(reason)
            raise typer.Exit(code=1)
    run = run_demo(scenario, seller, seed=seed, echo=False, human_presents=not auto_offer, model=model)
    if not quiet:
        typer.echo(run.render())
        gens = [m.get("generator") for m in run.transcript if m["direction"] == "outbound"]
        typer.echo("\noutbound by generator: " + ", ".join(f"{g}={gens.count(g)}" for g in sorted(set(gens))))


@app.command()
def chat(
    scenario: str = "clean",
    seed: int = 7,
    auto_offer: bool = False,
    model: Annotated[
        str | None, typer.Option(help="off (scripted) | fake (no network) | anthropic. Default: environment.")
    ] = None,
) -> None:
    """Interactive: you play the seller on the Console transport. Type 'quit' to stop."""
    import os

    import acqbot.queue.handlers  # noqa: F401

    if model is not None:
        os.environ["ACQBOT_LLM_PROVIDER"] = model
    from acqbot.conversation.service import Conversation
    from acqbot.db import session_scope
    from acqbot.demo import SELLER_ID
    from acqbot.ingestion.service import ingest_lead
    from acqbot.models import LadderStep, Lead
    from acqbot.queue.worker import drain
    from acqbot.simulator import make_lead
    from acqbot.transport.registry import reset_console_transport

    if auto_offer:
        os.environ["ACQBOT_AUTO_PRESENT_OFFER"] = "true"
    from acqbot.config import get_settings, reset_settings_cache
    from acqbot.llm.registry import missing_key_reason

    reset_settings_cache()
    reason = missing_key_reason(get_settings())
    if reason:
        typer.echo(reason)
        raise typer.Exit(code=1)
    typer.echo(f"language model: {get_settings().resolved_llm_provider}")

    console = reset_console_transport(echo=True)
    payload = make_lead(scenario, seed=seed)
    # A fresh seller identity per run: the 90-day dedupe is keyed on seller + vehicle, so re-running
    # `chat` with the same seed would otherwise be a duplicate of the previous run's lead.
    seller_id = f"{SELLER_ID}-{uuid.uuid4().hex[:8]}"
    payload["seller"]["platform_id"] = seller_id
    with session_scope() as s:
        result = ingest_lead(s, payload)
    if result.status == "duplicate":
        typer.echo(f"this lead is a duplicate of {result.duplicate_of} — try another --seed")
        raise typer.Exit(code=1)
    lead_id = result.lead_id
    drain("chat")
    vc = payload["vehicle_claimed"]
    typer.echo(
        f"lead {lead_id}: {vc['year']} {vc['make']} {vc['model']} — rego {vc['rego']} vin {vc['vin']} asking ${vc['asking_price_aud']:,}"
    )
    typer.echo("You are the seller. First message opens the thread via the m.me link. Type 'quit' to stop.\n")
    first = True
    while True:
        text = typer.prompt("seller")
        if text.strip().lower() in {"quit", "exit"}:
            break
        console.inject(seller_id, text, referral_ref=str(lead_id) if first else None)
        first = False
        for msg in console.poll(
            __import__("datetime").datetime(2000, 1, 1, tzinfo=__import__("datetime").UTC)
        ):
            with session_scope() as s:
                r = Conversation(s, console).handle_inbound(msg)
            typer.echo(f"[{r.action} · {r.stage}{' · escalated: ' + r.escalated if r.escalated else ''}]")
        drain("chat")
        with session_scope() as s:
            lead = s.get(Lead, lead_id)
            if lead is None:
                typer.echo("lead vanished — stopping")
                raise typer.Exit(code=1)
            if lead.state.value == "PRICED" and not auto_offer:
                if typer.confirm("Lead is PRICED. Present the opening offer as the human?", default=True):
                    Conversation(s, console).present_offer(lead, LadderStep.OPENING, presented_by="human:you")
            elif lead.state.value in {"HUMAN", "HANDOFF", "ARCHIVED", "REJECTED", "TERMINATED"}:
                typer.echo(f"conversation ended in {lead.state.value} — automation has stopped for this lead")
                break
        drain("chat")


@app.command()
def queue() -> None:
    """Show job queue counts by kind and status."""
    from sqlalchemy import func, select

    from acqbot.db import session_scope
    from acqbot.models import Job

    with session_scope() as s:
        rows = s.execute(select(Job.kind, Job.status, func.count()).group_by(Job.kind, Job.status)).all()
    for kind, status, n in sorted(rows, key=lambda r: (r[0], r[1].value)):
        typer.echo(f"{kind:20s} {status.value:8s} {n}")
    if not rows:
        typer.echo("queue empty")


@app.command()
def doctor(
    verbose: Annotated[
        bool, typer.Option("--verbose", "-v", help="Show every check, not just failures.")
    ] = False,
) -> None:
    """Check the things that fail quietly. Exit code 1 if anything is wrong."""
    from acqbot.db import session_scope
    from acqbot.observability import checks

    with session_scope() as s:
        report = checks(s)
    for c in report.checks:
        if c.ok and not verbose:
            continue
        mark = "ok  " if c.ok else ("DOWN" if c.fatal else "warn")
        typer.echo(f"{mark}  {c.name:16s} {c.detail}")
    if report.ok:
        typer.echo(f"all {len(report.checks)} checks passed")
    else:
        typer.echo(f"\n{len(report.failures)} of {len(report.checks)} checks failed")
        raise typer.Exit(1)


@app.command()
def review(
    model: Annotated[
        str | None,
        typer.Option(help="anthropic (the real thing) | fake (no network) | off. Default: environment."),
    ] = None,
    persona: Annotated[
        list[str] | None, typer.Option(help="Run only these personas. Repeatable. Default: all of them.")
    ] = None,
    scenario: str = "clean",
    seed: int = 42,
    auto_offer: Annotated[
        bool, typer.Option(help="Let the automation traverse the ladder so negotiation is reviewed too.")
    ] = True,
    out: Annotated[str, typer.Option(help="Where to write the markdown report.")] = "review.md",
) -> None:
    """Run the awkward sellers past the model and write a report on how it handled them.

    This is the prompt-tuning loop: read the report, change the wording in llm/prompts.py, run it
    again. Against the real API a full run is roughly 90 calls and a few cents.
    """
    import acqbot.queue.handlers  # noqa: F401
    from acqbot.config import get_settings, reset_settings_cache
    from acqbot.llm.registry import missing_key_reason
    from acqbot.personas import ALL, BY_NAME
    from acqbot.review import render_markdown, run_review

    if model is not None:
        os.environ["ACQBOT_LLM_PROVIDER"] = model
    reset_settings_cache()
    cfg = get_settings()
    reason = missing_key_reason(cfg)
    if reason:
        typer.echo(reason)
        raise typer.Exit(code=1)

    chosen = list(ALL)
    if persona:
        unknown = [p for p in persona if p not in BY_NAME]
        if unknown:
            typer.echo(f"unknown persona(s): {', '.join(unknown)}. Known: {', '.join(BY_NAME)}")
            raise typer.Exit(code=1)
        chosen = [BY_NAME[p] for p in persona]

    label = (
        f"{cfg.extraction_model} + {cfg.conversation_model}"
        if cfg.resolved_llm_provider == "anthropic"
        else cfg.resolved_llm_provider
    )
    real = cfg.resolved_llm_provider == "anthropic"
    if real:
        # One tiny call per model before committing to twenty minutes of conversation. A dead key
        # or an empty balance produces a report that says nothing except "fell back to the script".
        from acqbot.llm.registry import get_model_client, probe

        client = get_model_client(cfg)
        for model in (cfg.extraction_model, cfg.conversation_model):
            ok, detail = probe(client, model, effort=cfg.llm_effort)
            if not ok:
                typer.echo(f"{model}: {detail}")
                typer.echo("nothing was run — fix that and try again, or use --model fake for a dry run.")
                raise typer.Exit(code=1)
        typer.echo("both models answered.")
    typer.echo(f"running {len(chosen)} seller(s) against {label}...")

    def report_one(c) -> None:
        mark = "ok  " if c.ok else ("--  " if c.needs_model and not real else "SEE ")
        bits = []
        if c.problems:
            bits.append(f"{len(c.problems)} problem(s)")
        if c.rejections:
            bits.append(f"{len(c.rejections)} gate rejection(s)")
        if c.fallbacks:
            bits.append(f"{len(c.fallbacks)} fallback(s)")
        if c.needs_model and not real:
            bits.append("needs a real model")
        typer.echo(f"  {mark} {c.persona:14s} {c.final_state:11s} {'; '.join(bits)}")

    result = run_review(
        chosen,
        scenario=scenario,
        seed=seed,
        model_label=label,
        auto_offer=auto_offer,
        on_persona=report_one,
    )
    Path(out).write_text(render_markdown(result), encoding="utf-8")
    if result.aborted:
        typer.echo(f"\n{result.aborted}")
    clean = sum(1 for c in result.conversations if c.ok)
    typer.echo(
        f"\n{clean}/{len(result.conversations)} clean · {result.calls} calls · "
        f"US${result.cost_aud:.2f}\n\n  → {out}"
    )


@app.command("model-check")
def model_check() -> None:
    """Verify the language-model configuration with one tiny call to each configured model."""
    from acqbot.config import get_settings
    from acqbot.llm.registry import get_model_client, missing_key_reason, probe

    cfg = get_settings()
    typer.echo(f"provider: {cfg.resolved_llm_provider} (ACQBOT_LLM_PROVIDER={cfg.llm_provider})")
    reason = missing_key_reason(cfg)
    if reason:
        typer.echo(reason)
        raise typer.Exit(code=1)
    client = get_model_client(cfg)
    if client is None:
        typer.echo(
            "language model is off — set ACQBOT_ANTHROPIC_API_KEY in .env (or ACQBOT_LLM_PROVIDER=fake)"
        )
        raise typer.Exit(code=1)
    if client.name == "fake":
        typer.echo("fake client: deterministic, no network — nothing to verify")
        return
    failed = False
    for purpose, model in (("extract", cfg.extraction_model), ("generate", cfg.conversation_model)):
        ok, detail = probe(client, model, effort=cfg.llm_effort)
        typer.echo(f"{purpose:9s} {model}: {'ok' if ok else 'FAILED'} — {detail}")
        failed |= not ok
    if failed:
        raise typer.Exit(code=1)


@app.command("model-calls")
def model_calls(lead_id: str, limit: int = 50) -> None:
    """List the model calls made for a lead (purpose, model, tokens, latency, errors)."""
    from sqlalchemy import select

    from acqbot.db import session_scope
    from acqbot.models import ModelCall

    with session_scope() as s:
        rows = list(
            s.scalars(
                select(ModelCall)
                .where(ModelCall.lead_id == uuid.UUID(lead_id))
                .order_by(ModelCall.at.desc())
                .limit(limit)
            )
        )
        for c in reversed(rows):
            status = (
                f"ERROR {c.error[:60]}"
                if c.error
                else f"{c.input_tokens or 0} in / {c.output_tokens or 0} out"
            )
            typer.echo(
                f"{c.at:%H:%M:%S}  {c.purpose:9s} {c.model:34s} {c.latency_ms or 0:5d} ms  {status}  {c.call_id}"
            )
    if not rows:
        typer.echo("no model calls for this lead")


@app.command("prompt")
def prompt(ref: str) -> None:
    """Reconstruct the exact prompt behind an outbound message (msg_id) or a model call (call_id)."""
    from sqlalchemy import select

    from acqbot.db import session_scope
    from acqbot.models import Message, ModelCall

    with session_scope() as s:
        call = s.get(ModelCall, uuid.UUID(ref))
        if call is None:
            msg = s.get(Message, uuid.UUID(ref))
            if msg is None:
                typer.echo("no message or model call with that id")
                raise typer.Exit(code=1)
            if not msg.prompt_hash:
                typer.echo("that message has no prompt hash")
                raise typer.Exit(code=1)
            call = s.scalars(select(ModelCall).where(ModelCall.prompt_hash == msg.prompt_hash)).first()
            if call is None:
                notes = msg.validation_notes or {}
                typer.echo(
                    f"message was scripted ({msg.model_version}, template {notes.get('template')}); "
                    "no model prompt to show"
                )
                raise typer.Exit(code=0)
        typer.echo(f"call {call.call_id}  purpose={call.purpose}  model={call.model}  {call.prompt_version}")
        typer.echo(f"prompt_hash {call.prompt_hash}\n")
        typer.echo("=== SYSTEM ===")
        typer.echo(call.request["system"])
        typer.echo("\n=== MESSAGES ===")
        for m in call.request["messages"]:
            typer.echo(f"[{m['role']}] {m['content']}")
        typer.echo("\n=== RESPONSE ===")
        typer.echo(json.dumps((call.response or {}).get("parsed"), indent=2, ensure_ascii=False))
        if call.error:
            typer.echo(f"\nERROR: {call.error}")


if __name__ == "__main__":  # pragma: no cover
    app()
