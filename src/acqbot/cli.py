"""Command line: `uv run acqbot --help`."""

from __future__ import annotations

import json
import logging
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
    quiet: bool = False,
) -> None:
    """Run a scripted seller through the whole pipeline on the Console transport and print the transcript."""
    import acqbot.queue.handlers  # noqa: F401
    from acqbot.demo import run_demo

    run = run_demo(scenario, seller, seed=seed, echo=False, human_presents=not auto_offer)
    if not quiet:
        typer.echo(run.render())


@app.command()
def chat(
    scenario: str = "clean",
    seed: int = 7,
    auto_offer: bool = False,
) -> None:
    """Interactive: you play the seller on the Console transport. Type 'quit' to stop."""
    import os

    import acqbot.queue.handlers  # noqa: F401
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
        from acqbot.config import reset_settings_cache

        reset_settings_cache()

    console = reset_console_transport(echo=True)
    payload = make_lead(scenario, seed=seed)
    payload["seller"]["platform_id"] = SELLER_ID
    with session_scope() as s:
        lead_id = ingest_lead(s, payload).lead_id
    drain("chat")
    vc = payload["vehicle_claimed"]
    typer.echo(
        f"lead {lead_id}: {vc['year']} {vc['make']} {vc['model']} — rego {vc['rego']} vin {vc['vin']} asking ${vc['asking_price_aud']:,}"
    )
    typer.echo("You are the seller. First message opens the thread via the m.me link.\n")
    first = True
    while True:
        text = typer.prompt("seller")
        if text.strip().lower() in {"quit", "exit"}:
            break
        console.inject(SELLER_ID, text, referral_ref=str(lead_id) if first else None)
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
            if lead.state.value == "PRICED" and not auto_offer:
                if typer.confirm("Lead is PRICED. Present the opening offer as the human?", default=True):
                    Conversation(s, console).present_offer(lead, LadderStep.OPENING, presented_by="human:you")
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


if __name__ == "__main__":  # pragma: no cover
    app()
