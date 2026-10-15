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
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError
from sqlalchemy import select

from acqbot.api import admin
from acqbot.config import get_settings
from acqbot.console.html import (
    ENGINE_FOOTER,
    LADDER_LABEL,
    LADDER_ORDER,
    MANUAL_FOOTER,
    ago,
    esc,
    fact_value,
    hours_since,
    login_page,
    money,
    page,
    photo_grid,
    reason_text,
)
from acqbot.contracts import LeadContractError
from acqbot.conversation import templates as T
from acqbot.conversation.handoff import _render_fact, transcript
from acqbot.db import session_scope
from acqbot.enrichment.providers import KIND_LABEL, done_by_a_person, on_test_data, pricing_is_manual
from acqbot.facts.fields import DISCOVERY_REQUIRED
from acqbot.facts.store import fact_sheet
from acqbot.ingestion.service import ingest_lead
from acqbot.models import (
    CLOSED_STATES,
    AgentAssignment,
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
from acqbot.valuation.service import is_manual, latest_valuation

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


def _page(title: str, body: str, request: Request | None = None) -> str:
    """Every signed-in page. Says so, on every screen, when any figure or check is made up — a
    person demoing the console to the sales floor must never mistake a stub PPSR for a real one."""
    cfg = get_settings()
    stubs = on_test_data(cfg)
    banner = (
        "Test data: the "
        + ", ".join(KIND_LABEL[k] for k in stubs)
        + " here are made up. Fine for trying things out, never for a real seller — production "
        "refuses to start like this."
        if stubs
        else None
    )
    return page(
        title,
        body,
        flash=_flash(request) if request is not None else None,
        banner=banner,
        footer=MANUAL_FOOTER if pricing_is_manual(cfg) else ENGINE_FOOTER,
    )


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
        # Leads added but not yet in a conversation: the salesperson may need the link again.
        not_started = [
            lead
            for lead in s.scalars(
                select(Lead).where(Lead.state == LeadState.NEW).order_by(Lead.created_at.desc()).limit(20)
            )
            if s.scalars(select(Thread.thread_id).where(Thread.lead_id == lead.lead_id).limit(1)).first()
            is None
        ]
        waiting_rows = "".join(
            f'<div class="card"><div class="row"><div class="grow">{esc(_lead_line(lead))}'
            f'<div class="sub">{esc((lead.upstream_payload or {}).get("seller", {}).get("display_name"))}'
            f" · added {esc(ago(lead.created_at))}</div></div>"
            f'<a href="/console/leads/{lead.lead_id}">Link and message →</a></div></div>'
            for lead in not_started
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
                # A time, not a status: plain text, so it doesn't read as a second pill.
                f'<span class="meta{" late" if late else ""}">{esc(ago(e.at))}</span>'
                f"</div>"
                f'<div class="row" style="margin-top:8px">'
                f'<div class="grow sub">{esc(_lead_line(lead))} · '
                f"{esc(lead.state.value if lead else '?')}</div>"
                f'<a href="/console/leads/{e.lead_id}">Open →</a></div></div>'
            )
    body = ["<h2>Needs a person</h2>"]
    body.append("".join(rows) if rows else '<p class="empty">Nothing waiting. The queue is clear.</p>')
    if waiting_rows:
        # In SMS mode a lead only waits here when it could not be texted: no mobile on the listing,
        # or the number is already in another chat.
        heading = (
            "Not contacted yet — no text could be sent"
            if cfg.first_contact == "sms"
            else "Waiting for the seller to tap the link"
        )
        body.append(f"<h2>{heading}</h2>{waiting_rows}")
    extra = []
    if waiting_packets:
        extra.append(f'<a href="/console/handoffs">{waiting_packets} deal packet(s) awaiting a closer</a>')
    if unlinked:
        extra.append('<a href="/console/threads">conversations not matched to a lead</a>')
    if extra:
        body.append(f'<h2>Also</h2><p class="sub">{" · ".join(extra)}</p>')
    return _page("Queue", "".join(body), request)


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


# ------------------------------------------------------------------------------- add a lead
#
# Figure 1 has the leads arriving from "existing software" over a signed webhook. Until that exists,
# a salesperson who spots a car on Marketplace is the upstream source: this form builds the same
# Figure 2 payload and hands it to the same `ingest_lead` the webhook uses, so validation, dedupe and
# enrichment are identical whichever way a lead arrives.

_MARKETPLACE_ITEM = re.compile(r"facebook\.com/marketplace/item/(\d+)", re.I)
_LEAD_FIELDS = {
    "listing_url": "Marketplace listing link",
    "seller_name": "Seller's name, as on Marketplace",
    "seller_phone": "Seller's mobile, if the listing shows one",
    "make": "Make",
    "model": "Model",
    "variant": "Variant (if known)",
    "year": "Year",
    "odometer_km": "Odometer (km)",
    "asking_price": "Asking price ($)",
    "rego": "Rego (if shown)",
    "vin": "VIN (if shown)",
    "suburb": "Suburb",
    "postcode": "Postcode",
}
# Contract field paths → the label a person typed into.
_CONTRACT_LABEL = {
    "listing_url": "listing_url",
    "seller.display_name": "seller_name",
    "seller.phone": "seller_phone",
    "vehicle_claimed.make": "make",
    "vehicle_claimed.model": "model",
    "vehicle_claimed.variant": "variant",
    "vehicle_claimed.year": "year",
    "vehicle_claimed.odometer_km": "odometer_km",
    "vehicle_claimed.asking_price_aud": "asking_price",
    "vehicle_claimed.rego": "rego",
    "vehicle_claimed.vin": "vin",
    "location.suburb": "suburb",
    "location.postcode": "postcode",
}


def _number(text: str | None) -> int | None:
    digits = re.sub(r"[^\d.]", "", text or "")
    if not digits:
        return None
    try:
        return int(float(digits))
    except ValueError:
        return None


def _lead_form(values: dict[str, str], errors: list[str]) -> str:
    v = {
        k: esc(values.get(k, ""))
        for k in [*_LEAD_FIELDS, "description", "notes", "by", "state", "fuel", "transmission"]
    }

    def box(name: str, *, required: bool = False, wide: bool = False, kind: str = "text") -> str:
        return (
            f"<label{' class=wide' if wide else ''}>{esc(_LEAD_FIELDS[name])}{' *' if required else ''}"
            f'<input type="{kind}" name="{name}" value="{v[name]}"{" required" if required else ""}></label>'
        )

    def choose(name: str, label: str, options: list[str], default: str) -> str:
        current = values.get(name) or default
        opts = "".join(
            f'<option value="{o}"{" selected" if o == current else ""}>{o}</option>' for o in options
        )
        return f'<label>{esc(label)} *<select name="{name}" required>{opts}</select></label>'

    problems = (
        '<div class="errors"><b>That lead was not added:</b><ul>'
        + "".join(f"<li>{esc(e)}</li>" for e in errors)
        + "</ul></div>"
        if errors
        else ""
    )
    return (
        "<h2>Add a lead from Marketplace</h2>"
        '<p class="sub">Copy the details from the listing. Once it is added you get the link to send the '
        "seller — the assistant takes over when they tap it.</p>"
        f"{problems}"
        '<div class="card"><form method="post" action="/console/leads/new"><div class="grid">'
        + box("listing_url", required=True, wide=True)
        + box("seller_name", required=True)
        + box("seller_phone")
        + box("make", required=True)
        + box("model", required=True)
        + box("variant")
        + box("year", required=True)
        + box("odometer_km", required=True)
        + box("asking_price", required=True)
        + choose("transmission", "Transmission", ["auto", "manual"], "auto")
        + choose("fuel", "Fuel", ["petrol", "diesel", "hybrid", "ev"], "petrol")
        + box("rego")
        + box("vin")
        + box("suburb", required=True)
        + choose("state", "State", ["VIC", "NSW", "QLD", "SA", "WA", "TAS", "NT", "ACT"], "VIC")
        + box("postcode", required=True)
        + f'<label class=wide>Listing text (paste it)<textarea name="description">{v["description"]}</textarea></label>'
        + f'<label class=wide>Anything else worth knowing<textarea name="notes">{v["notes"]}</textarea></label>'
        + f'<label>Your name *<input name="by" value="{v["by"]}" required></label>'
        + '</div><p><button type="submit">Add lead</button></p></form></div>'
    )


@router.get("/leads/new", response_class=HTMLResponse)
def new_lead_page(request: Request) -> HTMLResponse:
    _guard(request)
    return HTMLResponse(_page("Add lead", _lead_form({}, []), request))


@router.post("/leads/new", response_class=HTMLResponse)
async def add_lead(request: Request) -> Any:
    _guard(request)
    form = {k: str(v).strip() for k, v in (await request.form()).items()}
    by = form.get("by", "")[:64] or "console"
    errors: list[str] = []
    numbers: dict[str, int | None] = {}
    for key in ("year", "odometer_km", "asking_price"):
        numbers[key] = _number(form.get(key))
        if numbers[key] is None:
            errors.append(f"{_LEAD_FIELDS[key]}: needs a number.")
    if errors:
        return HTMLResponse(_page("Add lead", _lead_form(form, errors), request), status_code=422)

    lead_id = uuid.uuid4()
    url = form.get("listing_url", "")
    item = _MARKETPLACE_ITEM.search(url)
    payload = {
        "schema_version": "1.0",
        "lead_id": str(lead_id),
        "source": "fb_marketplace",
        "listing_url": url,
        "seller": {
            # Marketplace never shows a seller's platform id. The listing's own id stands in, so the
            # same listing added twice is caught by the 90-day dedupe [3.2] like any other repeat.
            "platform_id": f"fb-marketplace-item:{item.group(1)}" if item else f"console:{lead_id}",
            "display_name": form.get("seller_name", ""),
            "phone": form.get("seller_phone") or None,
        },
        "vehicle_claimed": {
            "make": form.get("make", ""),
            "model": form.get("model", ""),
            "variant": form.get("variant") or None,
            "year": numbers["year"],
            "odometer_km": numbers["odometer_km"],
            "rego": form.get("rego") or None,
            "vin": form.get("vin") or None,
            "transmission": form.get("transmission") or "auto",
            "fuel": form.get("fuel") or "petrol",
            "asking_price_aud": numbers["asking_price"],
            "description_raw": form.get("description", ""),
            "images": [],
        },
        "location": {
            "suburb": form.get("suburb", ""),
            "state": form.get("state") or "VIC",
            "postcode": form.get("postcode", ""),
        },
        "qualified_at": datetime.now(UTC).isoformat(),
        "qualification_notes": f"Added in the console by {by}. {form.get('notes', '')}".strip(),
    }
    try:
        with session_scope() as s:
            result = ingest_lead(s, payload)
            if result.status == "duplicate":
                return _redirect(
                    f"/console/leads/{result.duplicate_of}",
                    flash="Already added — this is the same listing. Here it is.",
                    bad=True,
                )
            lead = s.get(Lead, result.lead_id)
            # Decided now rather than at the first message, so the invitation the salesperson sends
            # and the assistant's opening name the same person.
            from acqbot.conversation.service import agent_for

            agent_for(s, lead, get_settings())
    except LeadContractError as exc:
        for err in exc.errors or []:
            loc = err.get("loc", "")
            path = loc if isinstance(loc, str) else ".".join(str(p) for p in loc)
            label = _LEAD_FIELDS.get(_CONTRACT_LABEL.get(path, ""), path or "form")
            errors.append(f"{label}: {err.get('msg', 'not valid')}")
        return HTMLResponse(
            _page("Add lead", _lead_form(form, errors or [str(exc)]), request), status_code=422
        )
    return _redirect(f"/console/leads/{lead_id}", flash="Lead added. Send the seller the message below.")


# ------------------------------------------------------------------------------- one lead


def _words(key: str, value: Any) -> str:
    """A fact in words, whatever shape a seller's answer was stored in."""
    try:
        rendered = _render_fact(key, value)
    except (AttributeError, TypeError, ValueError):
        rendered = None
    return fact_value(key, rendered, value)


def _start_card(lead_id: Any, seller: str, vehicle: str, agent: str | None, cfg: Any) -> str:
    """The first touch, for the salesperson to send from Marketplace (the assistant cannot)."""
    link = T.marketplace_link(cfg.messenger_page_username, lead_id)
    message = T.marketplace_invite(
        dealership=cfg.dealership_name,
        lmct=cfg.dealership_lmct,
        agent=agent,
        seller_name=seller,
        vehicle=vehicle,
        link=link,
    )
    placeholder = cfg.messenger_page_username.strip().lower() in {"", "yourpage"}
    ppsr_first = (
        '<p class="sub" style="margin:8px 0 0">If the listing shows the rego or VIN, run the PPSR check '
        "first and record it below: a written-off or stolen car should never be contacted.</p>"
        if "ppsr" in done_by_a_person(cfg)
        else ""
    )
    return (
        '<h2>Start the conversation</h2><div class="card">'
        f'<p class="sub" style="margin:0">Send {esc(seller)} this on Marketplace'
        + (f", as {esc(agent)}" if agent else "")
        + ". When they tap the link and message the Page, the assistant takes over.</p>"
        f'<div class="copy">{esc(message)}</div>'
        + (
            '<div class="warnbox">The link points at a placeholder Page. Set ACQBOT_MESSENGER_PAGE_USERNAME '
            "to the Facebook Page's username once it exists.</div>"
            if placeholder
            else ""
        )
        + f"{ppsr_first}</div>"
    )


def _checks_form(lead_id: Any, year: Any, vin: str, rego: str, *, before_contact: bool) -> str:
    """Everything a PPSR search certificate answers, in one go. Every result is a deliberate choice:
    no field defaults to the good answer, because a hurried "no" on "written off" is the one mistake
    this form exists to prevent."""

    def pick(name: str, label: str, options: list[tuple[str, str]]) -> str:
        opts = '<option value="" selected disabled>choose…</option>' + "".join(
            f'<option value="{v}">{esc(t)}</option>' for v, t in options
        )
        return f'<label>{esc(label)} *<select name="{name}" required>{opts}</select></label>'

    ident = (
        f"rego {rego}" if rego else (f"VIN {vin}" if vin else "the rego or VIN (ask the seller if needed)")
    )
    return (
        "<h2>Record the PPSR check</h2>"
        f'<p class="sub">Search {esc(ident)} on ppsr.gov.au and copy the certificate here.'
        + (" Doing it now, before contact, is best." if before_contact else "")
        + "</p>"
        f'<div class="card"><form method="post" action="/console/leads/{lead_id}/checks"><div class="grid">'
        + pick("finance", "Finance owing", [("no", "no"), ("yes", "yes")])
        + '<label>Amount owing ($), if any<input name="amount_owing"></label>'
        + pick(
            "written_off",
            "Written off",
            [("no", "no"), ("repairable", "repairable"), ("statutory", "statutory")],
        )
        + pick("stolen", "Reported stolen", [("no", "no"), ("yes", "yes")])
        + pick(
            "rego_status",
            "Registration",
            [("current", "current"), ("expired", "expired"), ("unregistered", "unregistered")],
        )
        + '<label>Rego expiry<input type="date" name="rego_expiry"></label>'
        + f'<label>Year *<input name="year" value="{esc(year)}" required></label>'
        + f'<label>VIN<input name="vin" value="{esc(vin)}"></label>'
        + '<label>Certificate number<input name="certificate"></label>'
        + '<label>Odometer on the dash photo (km)<input name="odometer_km"></label>'
        + '<label>Your name *<input name="by" required></label>'
        + '</div><p><button type="submit">Record the check</button></p></form>'
        '<p class="sub" style="margin:4px 0 0">Written off or stolen stops the conversation and comes back '
        "to the queue. The odometer is optional here — it can also be confirmed from the photos later.</p></div>"
    )


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
        has_thread = (
            s.scalars(select(Thread.thread_id).where(Thread.lead_id == lead_id).limit(1)).first() is not None
        )
        assignment = s.get(AgentAssignment, lead_id)
        agent = assignment.agent if assignment else None
        manual_val = is_manual(val)
        ppsr_recorded = sheet.verified_from("finance_owing", ("ppsr",))
        closed = lead.state in CLOSED_STATES or lead.state == LeadState.TERMINATED
        not_started = lead.state == LeadState.NEW and not has_thread
        year_on_file = sheet.get("year")
        vin_on_file = sheet.get("vin") or ""
        rego_on_file = sheet.get("rego") or ""
        car_words = T._vehicle(sheet.get) or vehicle
        listing_url = lead.listing_url or ""

    out: list[str] = []
    # Back to the live listing: is it still up, what are they asking now, what do the photos show.
    listing = (
        f' · <a href="{esc(listing_url)}" target="_blank" rel="noopener noreferrer">Open listing ↗</a>'
        if listing_url.lower().startswith(("https://", "http://"))
        else ""
    )
    out.append(
        f'<div class="row"><div class="grow"><h1 class="page">{esc(vehicle)}</h1>'
        f'<p class="sub">{esc(seller)} · <span class="meta">{esc(state)}</span>'
        + (f" · waiting on <b>{esc(waiting_for)}</b>" if waiting_for else "")
        + f" · {calls} model call(s){listing}</p></div></div>"
    )

    if not_started:
        out.append(_start_card(lead_id, seller, car_words, agent, cfg))

    if contradicted:
        # In words, not storage: a PPSR result is a dict, and "seller said {'owing': False}" is
        # not something a salesperson should have to read.
        lines = "; ".join(
            f"{esc(k.replace('_', ' '))}: seller said {esc(_words(k, v.get('claimed')))}, "
            f"{esc(v.get('source'))} shows {esc(_words(k, v.get('actual')))}"
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

    # --- the PPSR check, when a person does it ---
    if "ppsr" in done_by_a_person(cfg) and not ppsr_recorded and not closed:
        out.append(_checks_form(lead_id, year_on_file, vin_on_file, rego_on_file, before_contact=not_started))

    # --- valuation and offers ---
    out.append("<h2>Valuation</h2>")
    if not val:
        out.append('<p class="empty">Not priced yet.</p>')
    elif manual_val:
        out.append(
            '<p class="sub">Pricing is manual — no price data is connected, so a person sets the figure.</p>'
            + (
                ""
                if released
                else '<div class="warnbox">Not every check is verified yet — record the PPSR check and '
                "confirm the odometer before pricing.</div>"
            )
        )
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
            f"<td class='num'>{money(o.amount)}</td>"
            f"<td>{esc(o.presented_by)}</td><td>{esc(o.outcome.value if o.outcome else 'open')}</td>"
            f"<td class='meta'>{esc(ago(o.presented_at))}</td></tr>"
            for o in offers
        )
        out.append(
            '<h2>Offers</h2><table><thead><tr><th scope="col">Step</th>'
            '<th scope="col" class="num">Amount</th><th scope="col">By</th><th scope="col">Outcome</th>'
            f'<th scope="col">Presented</th></tr></thead><tbody>{rows}</tbody></table>'
        )

    if released and manual_val and state in {"PRICED", "OFFER_MADE", "NEGOTIATING", "HUMAN"}:
        first = not offers
        out.append(
            f'<h2>{"Set the price" if first else "Send a new figure"}</h2><div class="card">'
            f'<form class="inline" method="post" action="/console/leads/{lead_id}/present-offer">'
            f'<input type="hidden" name="step" value="human">'
            f'<input name="amount_aud" placeholder="Amount, e.g. 18,500" required size="20">'
            f'<input name="by" placeholder="Your name" required>'
            f'<button type="submit">Send offer</button></form>'
            f'<p class="sub" style="margin:10px 0 0">'
            + (
                "Goes to the seller as a firm offer, subject to inspection, open for 48 hours."
                if first
                else "Goes to the seller as “I can go to $… — same terms”, with a fresh 48 hours."
            )
            + " Recorded against your name.</p></div>"
        )
    elif released and state in {"PRICED", "OFFER_MADE", "NEGOTIATING", "HUMAN"}:
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

    # --- photos: the odometer check is "read it off the dash photo", so the photo has to be here ---
    seller_photos = photo_grid(confirmed.get("photos") or claimed.get("photos"))
    listing_photos = photo_grid(confirmed.get("listing_images") or claimed.get("listing_images"))
    if seller_photos or listing_photos:
        out.append("<h2>Photos</h2>")
        if seller_photos:
            out.append(f'<p class="sub">From the seller — click to open full size.</p>{seller_photos}')
        if listing_photos:
            out.append(f'<p class="sub">From the listing.</p>{listing_photos}')

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
    none = "<tr><td colspan=2>—</td></tr>"
    out.append(
        '<table><thead><tr><th scope="col">Fact</th><th scope="col">Value</th></tr></thead>'
        f'<tbody><tr><th scope="rowgroup" colspan="2">Verified</th></tr>{verified_rows or none}</tbody>'
        f'<tbody><tr><th scope="rowgroup" colspan="2">Seller\'s word only</th></tr>{claimed_rows or none}</tbody>'
        "</table>"
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
        pics = photo_grid([a.get("url") for a in (m.get("attachments") or []) if isinstance(a, dict)])
        body_text = m["body"] or ("" if pics else f"[{len(m.get('attachments') or [])} photos]")
        # Mark the point the conversation changed channel, so a closer reading this can see that
        # the last four messages were texts, not Messenger.
        if m.get("channel") and m["channel"] != channel_so_far:
            if channel_so_far is not None:
                out.append(f'<p class="sub" style="margin:14px 0 6px">— moved to {esc(m["channel"])} —</p>')
            channel_so_far = m["channel"]
        out.append(
            f'<div class="msg {"in" if inbound else "out"}">'
            f'<div class="who"><span>{who}</span><span class="meta">{esc(ago(_dt(m["at"])))}{tag}</span></div>'
            + (f"<p>{esc(body_text)}</p>" if body_text else "")
            + f"{pics}</div>"
        )
    return HTMLResponse(_page(vehicle, "".join(out), request))


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


@router.post("/leads/{lead_id}/checks")
async def do_checks(request: Request, lead_id: uuid.UUID) -> Any:
    _guard(request)
    form = {k: str(v).strip() for k, v in (await request.form()).items()}
    back = f"/console/leads/{lead_id}"
    try:
        expiry = date.fromisoformat(form["rego_expiry"]) if form.get("rego_expiry") else None
        body = admin.RecordChecks(
            finance_owing=form.get("finance") == "yes",
            amount_owing_aud=_number(form.get("amount_owing")),
            written_off=form.get("written_off") or "",
            stolen=form.get("stolen") == "yes",
            rego_status=form.get("rego_status") or "",
            rego_expiry=expiry,
            year=_number(form.get("year")) or 0,
            vin=form.get("vin") or None,
            odometer_km=_number(form.get("odometer_km")),
            certificate=form.get("certificate") or None,
            by=form.get("by", "")[:64] or "console",
        )
        if form.get("finance") not in {"yes", "no"} or form.get("stolen") not in {"yes", "no"}:
            raise ValueError("Choose yes or no for finance owing and reported stolen.")
        out = admin.record_checks(lead_id, body)
    except ValidationError as exc:
        fields = ", ".join(sorted({str(e["loc"][0]).replace("_", " ") for e in exc.errors()}))
        return _redirect(back, flash=f"Check these and try again: {fields}.", bad=True)
    except HTTPException as exc:
        return _redirect(back, flash=str(exc.detail), bad=True)
    except ValueError as exc:
        return _redirect(back, flash=str(exc), bad=True)
    if out["result"] == "terminated":
        note = (
            "Recorded. It is written off or stolen, so the lead is closed — the seller was never contacted."
        )
    elif out["result"] == "escalated":
        note = "Recorded. It is written off or stolen — it's in the queue for someone to tell the seller."
    else:
        note = "Checks recorded." + (
            " Pricing is next." if out["state"] in {"VERIFICATION", "PRICED"} else ""
        )
    return _redirect(back, flash=note, bad=out["result"] != "clear")


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
    return HTMLResponse(_page("Handoffs", body, request))


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
    return HTMLResponse(_page("Unlinked", body, request))


@router.post("/threads/{thread_id}/link")
def do_link(request: Request, thread_id: uuid.UUID, lead_id: str = Form(...)) -> Any:
    _guard(request)
    try:
        admin.link_thread(thread_id, admin.LinkThread(lead_id=uuid.UUID(lead_id.strip())))
    except (HTTPException, ValueError) as exc:
        detail = getattr(exc, "detail", None) or "That is not a lead id."
        return _redirect("/console/threads", flash=str(detail), bad=True)
    return _redirect("/console/threads", flash="Linked.")


# ------------------------------------------------------------------------------- settings


def _dial(
    name: str, label: str, value: float, lo: float, hi: float, step: float, unit: str, default: str
) -> str:
    """A slider with a number box beside it. The box carries the value, so it works without script."""
    sync_box = f"this.form.elements['{name}'].value=this.value"
    sync_slider = f"this.form.elements['{name}_dial'].value=this.value"
    return (
        f'<div class="card"><div class="title">{esc(label)}</div>'
        f'<div class="row" style="margin-top:8px">'
        f'<input type="range" name="{name}_dial" min="{lo:g}" max="{hi:g}" step="{step:g}" value="{value:g}" '
        f'oninput="{sync_box}" class="grow" aria-label="{esc(label)} slider">'
        f'<input type="number" name="{name}" min="{lo:g}" max="{hi:g}" step="any" value="{value:g}" '
        f'oninput="{sync_slider}" style="width:9em" aria-label="{esc(label)}"> {esc(unit)}</div>'
        f'<p class="sub" style="margin:6px 0 0">Default from config: {esc(default)}</p></div>'
    )


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    _guard(request)
    from acqbot import business_settings as bs

    cfg = get_settings()
    with session_scope() as s:
        eff = bs.effective(s, cfg)
        rows = bs.history(s)
        past = "".join(
            f"<tr><td>{esc(ago(r.changed_at))}</td><td>{esc(bs.SPECS[r.key].label)}</td>"
            f'<td class="num">'
            + (f"{float(r.value) * 100:g}%" if r.key == bs.MARGIN.key else money(r.value))
            + f"</td><td>{esc(r.changed_by)}</td></tr>"
            for r in rows
        )
    segment_note = (
        '<p class="sub">Some vehicle types have their own margin set in config '
        f"({esc(', '.join(f'{k} {v * 100:g}%' for k, v in cfg.target_margin_by_segment.items()))}); "
        "those still apply to that type.</p>"
        if cfg.target_margin_by_segment
        else ""
    )
    body = (
        "<h2>Settings</h2>"
        '<p class="sub">Changes apply to new leads and new valuations. Offers already made keep their '
        "figures, and every valuation records the margin it used.</p>"
        '<form method="post" action="/console/settings">'
        + _dial(
            "high_value_threshold_aud",
            "High-value threshold — asking prices above this go to a person",
            eff.high_value_threshold_aud,
            bs.THRESHOLD.minimum,
            bs.THRESHOLD.maximum,
            bs.THRESHOLD.step,
            "AUD",
            money(cfg.high_value_threshold_aud),
        )
        + _dial(
            "target_margin_pct",
            "Target margin — taken off the adjusted value before the offer ladder",
            round(eff.target_margin_pct * 100, 2),
            bs.MARGIN.minimum * 100,
            bs.MARGIN.maximum * 100,
            bs.MARGIN.step * 100,
            "%",
            f"{cfg.target_margin_pct * 100:g}%",
        )
        + segment_note
        + '<div class="row" style="margin-top:10px"><input name="by" placeholder="Your name" required>'
        '<button type="submit">Save</button></div></form>'
        "<h2>Changes</h2>"
        + (
            '<table><thead><tr><th scope="col">When</th><th scope="col">Setting</th>'
            f'<th scope="col" class="num">Value</th><th scope="col">By</th></tr></thead><tbody>{past}</tbody></table>'
            if past
            else '<p class="empty">No changes yet — the defaults from config are in force.</p>'
        )
    )
    return HTMLResponse(_page("Settings", body, request))


@router.post("/settings")
def do_settings(
    request: Request,
    high_value_threshold_aud: str = Form(...),
    target_margin_pct: str = Form(...),
    by: str = Form(...),
) -> Any:
    _guard(request)
    from acqbot import business_settings as bs

    try:
        threshold = float(high_value_threshold_aud.replace(",", "").strip())
        margin = float(target_margin_pct.replace("%", "").strip()) / 100
    except ValueError:
        return _redirect("/console/settings", flash="Both settings need to be numbers.", bad=True)
    try:
        with session_scope() as s:
            changed = [
                bs.change(s, bs.THRESHOLD.key, threshold, by),
                bs.change(s, bs.MARGIN.key, round(margin, 4), by),
            ]
    except ValueError as exc:
        return _redirect("/console/settings", flash=str(exc), bad=True)
    note = "Saved." if any(changed) else "Nothing changed."
    return _redirect("/console/settings", flash=note)
