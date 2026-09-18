"""The human console — one screen for the queue, one per lead.

Section 11 suggests Retool over the database. This is the same idea without a second system to
buy, host and keep in step: server-rendered HTML over the admin endpoints that already exist. The
POST handlers call the very same functions `/admin` exposes, so there is one implementation of
"present an offer" and the console cannot drift from the API.

AUTHENTICATION is a signed cookie holding an HMAC of the admin token, never the token itself, so
the cookie cannot be replayed into the `/admin` API and possessing it does not reveal the secret.
`SameSite=Lax` keeps another site from POSTing here with your cookie attached. That is adequate for
a back-office screen; it is NOT adequate as the only thing between this and the public internet —
see the README. Like `/admin`, the whole console 404s when ACQBOT_ADMIN_TOKEN is unset.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

from acqbot.api import admin
from acqbot.config import get_settings
from acqbot.console.html import (
    LADDER_LABEL,
    LADDER_ORDER,
    ago,
    esc,
    fact_value,
    hours_since,
    login_page,
    money,
    page,
    reason_text,
)
from acqbot.conversation.handoff import _render_fact, transcript
from acqbot.db import session_scope
from acqbot.facts.fields import DISCOVERY_REQUIRED
from acqbot.facts.store import fact_sheet
from acqbot.models import (
    CLOSED_STATES,
    Escalation,
    HandoffPacket,
    LadderStep,
    Lead,
    LeadState,
    Message,
    ModelCall,
    Offer,
    Thread,
)
from acqbot.valuation.service import latest_valuation

router = APIRouter(prefix="/console", tags=["console"])

# States where the conversation is over: saying "bot still talking" about these is a lie, and the
# person reading the queue is deciding what to pick up on that basis. Defined in models.py, because
# the agent-assignment cap needs the same answer to "whose hands are actually full" and two copies
# of that set would drift apart.

COOKIE = "acqbot_console"
_COOKIE_SALT = b"acqbot-console-v1"


def _cookie_value(token: str) -> str:
    return hmac.new(token.encode(), _COOKIE_SALT, hashlib.sha256).hexdigest()


def _require_token() -> str:
    token = get_settings().admin_token
    if not token:
        raise HTTPException(status_code=404, detail="console disabled (ACQBOT_ADMIN_TOKEN unset)")
    return token


def _signed_in(request: Request) -> bool:
    token = _require_token()
    got = request.cookies.get(COOKIE)
    return bool(got) and hmac.compare_digest(got, _cookie_value(token))


def _redirect(to: str, *, flash: str | None = None, bad: bool = False) -> RedirectResponse:
    if flash:
        to += ("&" if "?" in to else "?") + f"m={'bad' if bad else 'ok'}:{flash}"
    return RedirectResponse(to, status_code=303)


def _flash(request: Request) -> tuple[str, str] | None:
    raw = request.query_params.get("m")
    if not raw or ":" not in raw:
        return None
    kind, text = raw.split(":", 1)
    return ("bad" if kind == "bad" else "ok", text[:300])


# ------------------------------------------------------------------------------- sign in


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    _require_token()
    if not _signed_in(request):
        return HTMLResponse(login_page(), status_code=401)
    return HTMLResponse(_queue(request))


@router.post("/login")
def login(token: str = Form(default="")) -> Any:
    real = _require_token()
    if not token or not hmac.compare_digest(real, token):
        return HTMLResponse(login_page("That token was not recognised."), status_code=401)
    resp = _redirect("/console")
    resp.set_cookie(
        COOKIE, _cookie_value(real), httponly=True, samesite="lax", max_age=12 * 3600, path="/console"
    )
    return resp


@router.post("/logout")
def logout() -> Any:
    resp = _redirect("/console")
    resp.delete_cookie(COOKIE, path="/console")
    return resp


def _guard(request: Request) -> None:
    _require_token()
    if not _signed_in(request):
        raise HTTPException(status_code=401, detail="sign in at /console")


# ------------------------------------------------------------------------------- the queue


def _lead_line(lead: Lead | None, sheet_get: Any = None) -> str:
    if lead is None:
        return "unknown lead"
    v = lead.upstream_payload.get("vehicle_claimed", {}) if lead.upstream_payload else {}
    bits = [str(v.get("year") or ""), v.get("make") or "", v.get("model") or "", v.get("variant") or ""]
    return " ".join(b for b in bits if b) or "vehicle unknown"


def _queue(request: Request) -> str:
    cfg = get_settings()
    with session_scope() as s:
        open_items = list(
            s.scalars(select(Escalation).where(Escalation.resolved_at.is_(None)).order_by(Escalation.at))
        )
        leads = {e.lead_id: s.get(Lead, e.lead_id) for e in open_items}
        unlinked = s.scalar(
            select(Thread).where(Thread.lead_id.is_(None)).with_only_columns(Thread.thread_id).limit(1)
        )
        waiting_packets = len(
            list(s.scalars(select(HandoffPacket).where(HandoffPacket.claimed_at.is_(None))))
        )
        rows = []
        for e in open_items:
            lead = leads.get(e.lead_id)
            state = lead.state if lead is not None else None
            stopped = state == LeadState.HUMAN
            closed = state in CLOSED_STATES
            title, what = reason_text(e.reason)
            age = hours_since(e.at)
            late = age > cfg.human_sla_hours
            klass = (
                "now" if late else ("stopped" if stopped else "soon" if age > cfg.human_sla_hours / 2 else "")
            )
            rows.append(
                f'<div class="card {klass}"><div class="row">'
                f'<div class="grow"><div class="title">{esc(title)}</div>'
                f'<div class="sub">{esc(what)}</div></div>'
                f'<span class="tag {"stopped" if stopped else "" if closed else "running"}">'
                f"{'automation stopped' if stopped else 'conversation closed' if closed else 'bot still talking'}"
                f"</span>"
                f'<span class="tag{" late" if late else ""}">{esc(ago(e.at))}</span>'
                f"</div>"
                f'<div class="row" style="margin-top:8px">'
                f'<div class="grow sub">{esc(_lead_line(lead))} · '
                f"{esc(lead.state.value if lead else '?')}</div>"
                f'<a href="/console/leads/{e.lead_id}">Open →</a></div></div>'
            )
    body = ["<h2>Needs a person</h2>"]
    body.append("".join(rows) if rows else '<p class="empty">Nothing waiting. The queue is clear.</p>')
    extra = []
    if waiting_packets:
        extra.append(f'<a href="/console/handoffs">{waiting_packets} deal packet(s) awaiting a closer</a>')
    if unlinked:
        extra.append('<a href="/console/threads">conversations not matched to a lead</a>')
    if extra:
        body.append(f'<h2>Also</h2><p class="sub">{" · ".join(extra)}</p>')
    return page("Queue", "".join(body), flash=_flash(request))


def _ceiling_block(lead_id: Any, e: Escalation) -> str:
    """The one-click half of a ceiling approval.

    Section 6.4 makes the ceiling a human decision, which is right — but a decision is not the same
    as a data-entry exercise. The automation has already done the arithmetic and knows the seller
    asked for; all that is missing is a person saying yes. So the figure is on the button."""
    if e.reason != "ceiling_approval":
        return ""
    d = e.details or {}
    ceiling, counter, current = d.get("ceiling"), d.get("counter"), d.get("amount")
    facts = [f"now at {money(current)}"]
    if counter is not None:
        facts.append(f"they asked for {money(counter)}")
    facts.append(f"ceiling is {money(ceiling)}")
    return (
        f'<p class="sub" style="margin:10px 0 6px">{esc(" · ".join(facts))}</p>'
        f'<form class="inline" method="post" action="/console/leads/{lead_id}/present-offer">'
        f'<input type="hidden" name="step" value="floor">'
        f'<input name="by" placeholder="Your name" required>'
        f'<button type="submit">Approve the ceiling — {money(ceiling)}</button>'
        f"</form>"
        # Two forms on one card needs a word between them, or they read as one confused form.
        f'<p class="sub" style="margin:12px 0 0">…or, if you settled it another way:</p>'
    )


# ------------------------------------------------------------------------------- one lead


@router.get("/leads/{lead_id}", response_class=HTMLResponse)
def lead_page(request: Request, lead_id: uuid.UUID) -> HTMLResponse:
    _guard(request)
    cfg = get_settings()
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if lead is None:
            raise HTTPException(404, "lead not found")
        sheet = fact_sheet(s, lead_id)
        val = latest_valuation(s, lead_id)
        offers = list(s.scalars(select(Offer).where(Offer.lead_id == lead_id).order_by(Offer.presented_at)))
        current = offers[-1] if offers else None
        open_items = list(
            s.scalars(
                select(Escalation)
                .where(Escalation.lead_id == lead_id, Escalation.resolved_at.is_(None))
                .order_by(Escalation.at)
            )
        )
        msgs = transcript(s, lead_id)
        last_out = s.scalars(
            select(Message)
            .where(Message.lead_id == lead_id, Message.direction == "outbound")
            .order_by(Message.sent_at.desc(), Message.msg_id.desc())
        ).first()
        waiting_for = (last_out.validation_notes or {}).get("asked_field") if last_out else None
        packet = s.scalars(select(HandoffPacket).where(HandoffPacket.lead_id == lead_id)).first()
        calls = len(list(s.scalars(select(ModelCall).where(ModelCall.lead_id == lead_id))))
        state = lead.state.value
        vehicle = _lead_line(lead)
        seller = (lead.upstream_payload or {}).get("seller", {}).get("display_name") or "the seller"
        ladder = dict(val.ladder) if val else {}
        basis = val.basis.value if val else None
        released = bool(val and basis == "verified")
        contradicted = dict(sheet.contradicted)
        confirmed = dict(sheet.confirmed)
        claimed = dict(sheet.claimed)
        packet_json = packet.packet if packet else None

    out: list[str] = []
    out.append(
        f'<div class="row"><div class="grow"><h2 style="margin:0">{esc(vehicle)}</h2>'
        f'<p class="sub">{esc(seller)} · <span class="meta">{esc(state)}</span>'
        + (f" · waiting on <b>{esc(waiting_for)}</b>" if waiting_for else "")
        + f" · {calls} model call(s)</p></div></div>"
    )

    if contradicted:
        lines = "; ".join(
            f"{esc(k.replace('_', ' '))}: seller said {esc(v.get('claimed'))}, "
            f"{esc(v.get('source'))} shows {esc(v.get('actual'))}"
            for k, v in contradicted.items()
        )
        out.append(f'<div class="warnbox">Contradicted claims — {lines}</div>')

    # --- what needs deciding ---
    if open_items:
        out.append("<h2>Needs a decision</h2>")
        for e in open_items:
            title, what = reason_text(e.reason)
            late = hours_since(e.at) > cfg.human_sla_hours
            out.append(
                f'<div class="card{" now" if late else ""}"><div class="row">'
                f'<div class="grow"><div class="title">{esc(title)}</div>'
                f'<div class="sub">{esc(what)}</div></div>'
                f'<span class="meta">{esc(ago(e.at))}</span></div>'
                + _ceiling_block(lead_id, e)
                + f'<form class="inline" method="post" action="/console/escalations/{e.escalation_id}/resolve" '
                f'style="margin-top:10px">'
                f'<input type="hidden" name="lead_id" value="{lead_id}">'
                f'<input name="resolution" placeholder="What you did" style="flex:1;min-width:220px">'
                f'<input name="by" placeholder="Your name" required>'
                f'<label><input type="checkbox" name="return_to_automation" value="1" checked> '
                f"hand back to the bot</label>"
                f'<button type="submit">Resolve</button></form></div>'
            )

    # --- valuation and offers ---
    out.append("<h2>Valuation</h2>")
    if not val:
        out.append('<p class="empty">Not priced yet.</p>')
    else:
        # JSONB hands the ladder back in whatever order it was stored; a person reads it upwards.
        ordered = [k for k in LADDER_ORDER if k in ladder] + [k for k in ladder if k not in LADDER_ORDER]
        steps = "".join(
            f"<div{' class=ceiling' if name == 'floor' else ''}>"
            f"{esc(LADDER_LABEL.get(name, name))}<b>{money(ladder[name])}</b></div>"
            for name in ordered
        )
        note = (
            ""
            if released
            else '<div class="warnbox">Basis is <b>indicative</b> — not every input is verified, so this '
            "may not be released to the seller.</div>"
        )
        out.append(f'{note}<div class="ladder">{steps}</div>')

    if offers:
        rows = "".join(
            f"<tr><td>{esc(LADDER_LABEL.get(o.ladder_step.value, o.ladder_step.value))}</td>"
            f"<td>{money(o.amount)}</td>"
            f"<td>{esc(o.presented_by)}</td><td>{esc(o.outcome.value if o.outcome else 'open')}</td>"
            f"<td class='meta'>{esc(ago(o.presented_at))}</td></tr>"
            for o in offers
        )
        out.append(
            "<h2>Offers</h2><table><tr><th>Step</th><th>Amount</th><th>By</th><th>Outcome</th>"
            f"<th>Presented</th></tr>{rows}</table>"
        )

    if released and state in {"PRICED", "OFFER_MADE", "NEGOTIATING", "HUMAN"}:
        options = "".join(
            f'<option value="{s.value}">{LADDER_LABEL.get(s.value, s.value)} — '
            f"{money(ladder.get(s.value))}</option>"
            for s in (LadderStep.OPENING, LadderStep.STEP_1, LadderStep.STEP_2, LadderStep.FLOOR)
            if s.value in ladder
        )
        out.append(
            f'<h2>Present an offer</h2><div class="card">'
            f'<form class="inline" method="post" action="/console/leads/{lead_id}/present-offer">'
            f'<select name="step">{options}<option value="human">above the ceiling (enter amount)</option>'
            f'</select><input name="amount_aud" placeholder="Amount, only for above-ceiling" size="22">'
            f'<input name="by" placeholder="Your name" required>'
            f'<button type="submit">Send offer</button></form>'
            f'<p class="sub" style="margin:10px 0 0">The ceiling and anything above it are your decision, '
            f"and are recorded against your name.</p></div>"
        )

    if current is not None and current.outcome is None:
        out.append(
            f'<h2>Record an outcome that happened off-channel</h2><div class="card">'
            f'<form class="inline" method="post" action="/console/leads/{lead_id}/offer-outcome">'
            f'<select name="outcome"><option value="accepted">accepted</option>'
            f'<option value="rejected">rejected</option><option value="expired">expired</option></select>'
            f'<input name="by" placeholder="Your name" required><button type="submit">Record</button>'
            f"</form></div>"
        )

    # --- facts ---
    out.append("<h2>What we know</h2>")
    hide = {"listing_images", "photos", "seller_notes", "deferred_fields", "contradictions_acknowledged"}

    def _rows(items: dict[str, Any]) -> str:
        # The fields that gate pricing first; everything else after, in the order it arrived.
        order = [k.key for k in DISCOVERY_REQUIRED]
        keys = [k for k in order if k in items] + [k for k in items if k not in order]
        return "".join(
            f"<tr><td>{esc(k.replace('_', ' '))}</td>"
            f"<td>{esc(fact_value(k, _render_fact(k, items[k]), items[k]))}</td></tr>"
            for k in keys
            if k not in hide
        )

    verified_rows = _rows(confirmed)
    claimed_rows = _rows(claimed)
    out.append(
        f"<table><tr><th>Verified</th><th></th></tr>{verified_rows or '<tr><td colspan=2>—</td></tr>'}"
        f"<tr><th>Seller's word only</th><th></th></tr>{claimed_rows or '<tr><td colspan=2>—</td></tr>'}</table>"
    )
    out.append(
        f'<div class="card" style="margin-top:12px">'
        f'<form class="inline" method="post" action="/console/leads/{lead_id}/facts">'
        f'<input name="field" placeholder="Field, e.g. odometer_km" required>'
        f'<input name="value" placeholder="Value" required>'
        f'<input name="by" placeholder="Your name" required>'
        f'<button type="submit">Record as verified</button></form>'
        f'<p class="sub" style="margin:10px 0 0">Recorded as an inspection result and re-prices the lead. '
        f"This is where the odometer from the dash photo goes.</p></div>"
    )

    if packet_json:
        out.append(
            f'<h2>Deal packet</h2><div class="card"><div class="row">'
            f'<div class="grow"><b>{money(packet_json.get("agreed_price_aud"))} agreed</b> · '
            f"next action {esc(packet_json.get('next_action'))}</div>"
            f'<a href="/console/handoffs">All packets →</a></div>'
            f'<p class="sub" style="margin-top:8px">{esc(packet_json.get("conversation_summary"))}</p></div>'
        )

    # --- transcript ---
    out.append("<h2>Conversation</h2>")
    if not msgs:
        out.append('<p class="empty">Nothing sent yet.</p>')
    channel_so_far: str | None = None
    for m in msgs:
        inbound = m["direction"] == "inbound"
        who = "Seller" if inbound else "Us"
        tag = "" if inbound else f" · {esc(m.get('model_version'))}"
        body_text = m["body"] or (f"[{len(m.get('attachments') or [])} photos]")
        # Mark the point the conversation changed channel, so a closer reading this can see that
        # the last four messages were texts, not Messenger.
        if m.get("channel") and m["channel"] != channel_so_far:
            if channel_so_far is not None:
                out.append(f'<p class="sub" style="margin:14px 0 6px">— moved to {esc(m["channel"])} —</p>')
            channel_so_far = m["channel"]
        out.append(
            f'<div class="msg {"in" if inbound else "out"}">'
            f'<div class="who"><span>{who}</span><span class="meta">{esc(ago(_dt(m["at"])))}{tag}</span></div>'
            f"<p>{esc(body_text)}</p></div>"
        )
    return HTMLResponse(page(vehicle, "".join(out), flash=_flash(request)))


def _dt(iso: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(iso) if iso else None
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------------------- actions
#
# Each one calls the same function `/admin` exposes, so there is exactly one implementation of the
# behaviour and the console cannot drift from the API.


@router.post("/escalations/{escalation_id}/resolve")
def do_resolve(
    request: Request,
    escalation_id: uuid.UUID,
    lead_id: str = Form(...),
    by: str = Form(...),
    resolution: str = Form(default=""),
    return_to_automation: str | None = Form(default=None),
) -> Any:
    _guard(request)
    try:
        admin.resolve_escalation(
            escalation_id,
            admin.Resolve(
                by=by.strip()[:64] or "console",
                resolution=resolution.strip()[:500] or "resolved from the console",
                return_to_automation=bool(return_to_automation),
            ),
        )
    except HTTPException as exc:
        return _redirect(f"/console/leads/{lead_id}", flash=str(exc.detail), bad=True)
    return _redirect(f"/console/leads/{lead_id}", flash="Resolved.")


@router.post("/leads/{lead_id}/present-offer")
def do_present(
    request: Request,
    lead_id: uuid.UUID,
    step: str = Form(...),
    by: str = Form(...),
    amount_aud: str = Form(default=""),
) -> Any:
    _guard(request)
    amount: float | None = None
    if amount_aud.strip():
        try:
            amount = float(amount_aud.replace("$", "").replace(",", "").strip())
        except ValueError:
            return _redirect(f"/console/leads/{lead_id}", flash="That amount is not a number.", bad=True)
    try:
        body = admin.PresentOffer(step=LadderStep(step), by=by.strip()[:64] or "console", amount_aud=amount)
        out = admin.present_offer(lead_id, body)
    except HTTPException as exc:
        return _redirect(f"/console/leads/{lead_id}", flash=str(exc.detail), bad=True)
    except ValueError as exc:
        return _redirect(f"/console/leads/{lead_id}", flash=str(exc), bad=True)
    return _redirect(f"/console/leads/{lead_id}", flash=f"Offer sent: {money(out['amount'])}.")


@router.post("/leads/{lead_id}/offer-outcome")
def do_outcome(request: Request, lead_id: uuid.UUID, outcome: str = Form(...), by: str = Form(...)) -> Any:
    _guard(request)
    from acqbot.models import OfferOutcome

    try:
        admin.offer_outcome(
            lead_id, admin.OfferOutcomeBody(outcome=OfferOutcome(outcome), by=by.strip()[:64] or "console")
        )
    except HTTPException as exc:
        return _redirect(f"/console/leads/{lead_id}", flash=str(exc.detail), bad=True)
    return _redirect(f"/console/leads/{lead_id}", flash=f"Recorded as {outcome}.")


@router.post("/leads/{lead_id}/facts")
def do_fact(
    request: Request, lead_id: uuid.UUID, field: str = Form(...), value: str = Form(...), by: str = Form(...)
) -> Any:
    _guard(request)
    raw: Any = value.strip()
    if raw.replace(",", "").replace(".", "").isdigit():
        raw = float(raw.replace(",", "")) if "." in raw else int(raw.replace(",", ""))
    elif raw.lower() in {"true", "yes"}:
        raw = True
    elif raw.lower() in {"false", "no"}:
        raw = False
    try:
        out = admin.add_fact(
            lead_id,
            admin.ManualFact(field=field.strip()[:64], value=raw, by=by.strip()[:64] or "console"),
        )
    except HTTPException as exc:
        return _redirect(f"/console/leads/{lead_id}", flash=str(exc.detail), bad=True)
    note = "Recorded." if out["created"] else "Already on file — nothing changed."
    return _redirect(f"/console/leads/{lead_id}", flash=note)


# ------------------------------------------------------------------------------- handoffs


@router.get("/handoffs", response_class=HTMLResponse)
def handoffs_page(request: Request) -> HTMLResponse:
    _guard(request)
    with session_scope() as s:
        packets = list(
            s.scalars(select(HandoffPacket).order_by(HandoffPacket.claimed_at, HandoffPacket.created_at))
        )
        cards = []
        for p in packets:
            d = p.packet or {}
            late = p.claimed_at is None and datetime.now(UTC) > (
                p.sla_expires_at if p.sla_expires_at.tzinfo else p.sla_expires_at.replace(tzinfo=UTC)
            )
            claimed = (
                f'<span class="tag">claimed by {esc(p.claimed_by)}</span>'
                if p.claimed_at
                else f'<form class="inline" method="post" action="/console/handoffs/{p.packet_id}/claim">'
                f'<input name="by" placeholder="Your name" required>'
                f'<button type="submit">Claim</button></form>'
            )
            flags = ", ".join(str(f) for f in (d.get("flags") or []) if not str(f).startswith("seller_note"))
            notes = [str(f)[13:] for f in (d.get("flags") or []) if str(f).startswith("seller_note")]
            cards.append(
                f'<div class="card{" now" if late else ""}"><div class="row">'
                f'<div class="grow"><div class="title">{money(d.get("agreed_price_aud"))} agreed — '
                f"{esc((d.get('seller') or {}).get('name') or 'seller')}</div>"
                f'<div class="sub">{esc(d.get("conversation_summary"))}</div></div>'
                f'<span class="tag{" late" if late else ""}">{esc(ago(p.created_at))}</span></div>'
                + (f'<p class="sub" style="margin:8px 0 0">Flags: {esc(flags)}</p>' if flags else "")
                + (
                    f'<p class="sub" style="margin:4px 0 0">Seller mentioned: {esc("; ".join(notes))}</p>'
                    if notes
                    else ""
                )
                + f'<div class="row" style="margin-top:10px">{claimed}'
                f'<span class="spacer"></span><a href="/console/leads/{p.lead_id}">Open lead →</a>'
                f"</div></div>"
            )
    body = "<h2>Deal packets</h2>" + ("".join(cards) or '<p class="empty">No deals agreed yet.</p>')
    return HTMLResponse(page("Handoffs", body, flash=_flash(request)))


@router.post("/handoffs/{packet_id}/claim")
def do_claim(request: Request, packet_id: uuid.UUID, by: str = Form(...)) -> Any:
    _guard(request)
    try:
        admin.claim_handoff(packet_id, admin.Claim(by=by.strip()[:64] or "console"))
    except HTTPException as exc:
        return _redirect("/console/handoffs", flash=str(exc.detail), bad=True)
    return _redirect("/console/handoffs", flash="Claimed — it's yours.")


# ------------------------------------------------------------------------------- unlinked threads


@router.get("/threads", response_class=HTMLResponse)
def threads_page(request: Request) -> HTMLResponse:
    _guard(request)
    with session_scope() as s:
        threads = list(
            s.scalars(select(Thread).where(Thread.lead_id.is_(None)).order_by(Thread.created_at.desc()))
        )
        cards = []
        for t in threads:
            first = s.scalars(
                select(Message).where(Message.thread_id == t.thread_id).order_by(Message.sent_at).limit(1)
            ).first()
            cards.append(
                f'<div class="card"><div class="row"><div class="grow">'
                f'<div class="title">{esc(t.channel.value)} · {esc(t.external_id)}</div>'
                f'<div class="sub">{esc(first.body if first else "no messages")}</div></div>'
                f'<span class="meta">{esc(ago(t.created_at))}</span></div>'
                f'<form class="inline" method="post" action="/console/threads/{t.thread_id}/link" '
                f'style="margin-top:10px"><input name="lead_id" placeholder="Lead id" required style="flex:1">'
                f'<button type="submit">Link</button></form></div>'
            )
    body = "<h2>Conversations with no lead</h2>" + (
        "".join(cards) or '<p class="empty">Every conversation is matched to a lead.</p>'
    )
    return HTMLResponse(page("Unlinked", body, flash=_flash(request)))


@router.post("/threads/{thread_id}/link")
def do_link(request: Request, thread_id: uuid.UUID, lead_id: str = Form(...)) -> Any:
    _guard(request)
    try:
        admin.link_thread(thread_id, admin.LinkThread(lead_id=uuid.UUID(lead_id.strip())))
    except (HTTPException, ValueError) as exc:
        detail = getattr(exc, "detail", None) or "That is not a lead id."
        return _redirect("/console/threads", flash=str(detail), bad=True)
    return _redirect("/console/threads", flash="Linked.")
