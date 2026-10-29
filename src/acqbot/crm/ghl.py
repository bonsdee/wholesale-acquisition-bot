"""GoHighLevel: leads in from a GHL workflow, pipeline stage out on every state change.

Two directions, deliberately unequal:

  GHL → bot   A workflow's Webhook action posts the contact to /webhooks/ghl. We turn it into a
              normal lead contract and ingest it like any other source, so dedupe, enrichment and
              first contact behave exactly as they do for a console lead.
  bot → GHL   Every state change queues a `ghl_sync` job that moves the contact's opportunity to
              the matching stage. The job reads the lead as it is when it runs, so a burst of
              transitions in one turn costs a few idempotent writes, never an out-of-order one.

GHL never talks to the seller for us. The bot keeps its own Twilio number; GHL is the dealership's
view of the pipeline.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from acqbot.config import Settings, get_settings
from acqbot.contracts import parse_lead
from acqbot.facts.store import fact_sheet
from acqbot.models import CrmLink, Lead, LeadState, Offer, StateLog
from acqbot.queue.jobs import enqueue
from acqbot.queue.worker import RetryableError, job_handler

log = logging.getLogger("acqbot.ghl")

API_VERSION = "2021-07-28"
INBOUND_TOKEN_HEADER = "X-Acqbot-Token"
# Lead ids for GHL contacts are derived from the contact and the car, not random, so a workflow that
# fires twice for the same submission lands on the same lead (ingestion is idempotent on lead_id).
_LEAD_NAMESPACE = uuid.UUID("6f1c2a52-5d1e-4b7a-9a43-3f0d2c8e9b11")

# Stage names as created in the "Car Acquisition" pipeline. Several bot states share a stage: the
# dealership cares where the deal is, not which sub-step of discovery the bot is in. PRICED sits
# with verification because the seller has not seen a number yet — a person presents the offer.
STAGE_FOR_STATE: dict[LeadState, str] = {
    LeadState.NEW: "New Lead",
    LeadState.CONTACTED: "Contacted",
    LeadState.ENGAGED: "In Conversation",
    LeadState.DISCOVERY: "In Conversation",
    LeadState.VERIFICATION: "Verifying Details",
    LeadState.PRICED: "Verifying Details",
    LeadState.OFFER_MADE: "Offer Made",
    LeadState.NEGOTIATING: "Negotiating",
    LeadState.ACCEPTED: "Offer Accepted",
    LeadState.HANDOFF: "Inspection / Pickup",
}
# Ends that are not a place on the board but a verdict on the opportunity.
STATUS_FOR_STATE: dict[LeadState, str] = {
    LeadState.REJECTED: "lost",
    LeadState.TERMINATED: "lost",
    LeadState.ARCHIVED: "abandoned",
}
# Waiting on a person, or gone quiet: the deal keeps its stage and gains a tag staff can filter on.
TAG_FOR_STATE: dict[LeadState, str] = {
    LeadState.ESCALATED: "needs-human",
    LeadState.HUMAN: "needs-human",
    LeadState.STALLED: "bot-stalled",
}
MISSING_DETAILS_TAG = "bot-missing-details"


class GhlError(RuntimeError):
    pass


# --------------------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------------------


class GhlClient:
    """The handful of LeadConnector v2 calls the sync needs. Scopes: contacts, opportunities, locations."""

    def __init__(self, settings: Settings, *, http: httpx.Client | None = None, timeout: float = 15.0):
        self.location_id = settings.ghl_location_id
        self._http = http or httpx.Client(timeout=timeout)
        self._base = settings.ghl_base_url.rstrip("/")
        self._headers = {
            "Authorization": f"Bearer {settings.ghl_api_key}",
            "Version": API_VERSION,
            "Accept": "application/json",
        }

    def _call(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            r = self._http.request(method, self._base + path, headers=self._headers, **kwargs)
        except httpx.HTTPError as exc:
            raise RetryableError(f"ghl {method} {path}: {exc}") from exc
        if r.status_code == 429 or r.status_code >= 500:
            raise RetryableError(f"ghl {method} {path}: {r.status_code}")
        if r.status_code >= 400:
            raise GhlError(f"ghl {method} {path}: {r.status_code} {r.text[:300]}")
        return r.json() if r.content else {}

    def pipelines(self) -> list[dict[str, Any]]:
        return self._call("GET", "/opportunities/pipelines", params={"locationId": self.location_id}).get(
            "pipelines", []
        )

    def upsert_contact(self, body: dict[str, Any]) -> str:
        data = self._call("POST", "/contacts/upsert", json={"locationId": self.location_id, **body})
        return data["contact"]["id"]

    def update_contact(self, contact_id: str, body: dict[str, Any]) -> None:
        self._call("PUT", f"/contacts/{contact_id}", json=body)

    def add_tags(self, contact_id: str, tags: list[str]) -> None:
        self._call("POST", f"/contacts/{contact_id}/tags", json={"tags": tags})

    def remove_tags(self, contact_id: str, tags: list[str]) -> None:
        self._call("DELETE", f"/contacts/{contact_id}/tags", json={"tags": tags})

    def add_note(self, contact_id: str, body: str) -> None:
        self._call("POST", f"/contacts/{contact_id}/notes", json={"body": body})

    def create_opportunity(self, body: dict[str, Any]) -> str:
        data = self._call("POST", "/opportunities/", json={"locationId": self.location_id, **body})
        return data["opportunity"]["id"]

    def find_opportunity(self, contact_id: str, pipeline_id: str) -> str | None:
        """The contact's existing opportunity in this pipeline, newest first, if any."""
        data = self._call(
            "GET",
            "/opportunities/search",
            params={"location_id": self.location_id, "contact_id": contact_id, "pipeline_id": pipeline_id},
        )
        opps = data.get("opportunities") or []
        return opps[0]["id"] if opps else None

    def update_opportunity(self, opportunity_id: str, body: dict[str, Any]) -> None:
        self._call("PUT", f"/opportunities/{opportunity_id}", json=body)


_client: GhlClient | None = None


def get_client(settings: Settings) -> GhlClient:
    global _client
    if _client is None:
        _client = GhlClient(settings)
    return _client


def set_client(client: GhlClient | None) -> None:
    """Test hook, and how a settings change drops the cached client."""
    global _client, _pipeline_cache
    _client = client
    _pipeline_cache = None


_pipeline_cache: tuple[str, dict[str, str]] | None = None


def pipeline_stages(client: GhlClient, settings: Settings) -> tuple[str, dict[str, str]]:
    """(pipeline id, {stage name: stage id}) for the configured pipeline. Cached for the process."""
    global _pipeline_cache
    if _pipeline_cache is not None:
        return _pipeline_cache
    wanted_id, wanted_name = settings.ghl_pipeline_id, settings.ghl_pipeline_name.strip().lower()
    for p in client.pipelines():
        if (wanted_id and p.get("id") == wanted_id) or (
            not wanted_id and str(p.get("name", "")).strip().lower() == wanted_name
        ):
            stages = {str(st["name"]).strip().lower(): st["id"] for st in p.get("stages", [])}
            _pipeline_cache = (p["id"], stages)
            return _pipeline_cache
    raise GhlError(f"pipeline {settings.ghl_pipeline_id or settings.ghl_pipeline_name!r} not found in GHL")


# --------------------------------------------------------------------------------------
# bot → GHL
# --------------------------------------------------------------------------------------


def schedule_sync(session: Session, lead: Lead) -> None:
    """Queue a push of this lead's current state. No-op unless GHL is configured.

    No dedupe key: a job already running would swallow the newer state. Each job syncs whatever the
    lead looks like when it runs, so extra jobs are harmless."""
    if get_settings().ghl_enabled:
        enqueue(session, "ghl_sync", {"lead_id": str(lead.lead_id)}, max_attempts=8)


def _latest_offer(session: Session, lead_id: uuid.UUID) -> float | None:
    offer = session.scalars(
        select(Offer).where(Offer.lead_id == lead_id).order_by(Offer.presented_at.desc()).limit(1)
    ).first()
    return float(offer.amount) if offer else None


def _custom_fields(session: Session, lead: Lead, settings: Settings) -> list[dict[str, Any]]:
    """Contact fields under the "Vehicle" folder, by GHL's generated keys (contact.<snake name>)."""
    sheet = fact_sheet(session, lead.lead_id)
    values: dict[str, Any] = {
        "bot_lead_id": str(lead.lead_id),
        "make": sheet.get("make"),
        "model": sheet.get("model"),
        "year": sheet.get("year"),
        "odometer_km": sheet.get("odometer_km"),
        "rego": sheet.get("rego"),
        "asking_price": sheet.get("asking_price_aud"),
        "offer_amount": _latest_offer(session, lead.lead_id),
    }
    if settings.public_base_url:
        values["bot_transcript"] = f"{settings.public_base_url.rstrip('/')}/console/leads/{lead.lead_id}"
    return [{"key": f"contact.{k}", "field_value": v} for k, v in values.items() if v not in (None, "")]


def _last_trigger(session: Session, lead_id: uuid.UUID) -> str:
    row = session.scalars(
        select(StateLog).where(StateLog.lead_id == lead_id).order_by(StateLog.at.desc(), StateLog.id.desc())
    ).first()
    return row.trigger if row else ""


def _ensure_contact(session: Session, client: GhlClient, lead: Lead) -> CrmLink:
    link = session.get(CrmLink, lead.lead_id)
    if link is not None:
        return link
    seller = parse_lead(lead.upstream_payload).seller  # the stored payload is raw; this normalises the phone
    body: dict[str, Any] = {"name": seller.display_name, "source": "Acquisition bot"}
    if seller.phone:
        body["phone"] = seller.phone  # upsert matches on phone, so a seller already in GHL is reused
    link = CrmLink(lead_id=lead.lead_id, contact_id=client.upsert_contact(body))
    session.add(link)
    session.flush()
    return link


def sync_lead(session: Session, lead_id: uuid.UUID, client: GhlClient | None = None) -> None:
    settings = get_settings()
    if not settings.ghl_enabled:
        return
    lead = session.get(Lead, lead_id)
    if lead is None:
        return
    client = client or get_client(settings)
    link = _ensure_contact(session, client, lead)
    state, before = lead.state, link.synced_state

    # Facts first and on their own: a field the sub-account does not have must not stop the stage moving.
    try:
        client.update_contact(link.contact_id, {"customFields": _custom_fields(session, lead, settings)})
    except GhlError as exc:
        log.warning("ghl custom fields not written for lead %s: %s", lead_id, exc)

    pipeline_id, stages = pipeline_stages(client, settings)
    stage_name = STAGE_FOR_STATE.get(state)
    opp: dict[str, Any] = {"status": STATUS_FOR_STATE.get(state, "open")}
    if stage_name:
        stage_id = stages.get(stage_name.lower())
        if stage_id is None:
            raise GhlError(f"stage {stage_name!r} is missing from the GHL pipeline")
        opp["pipelineStageId"] = stage_id
    value = _latest_offer(session, lead.lead_id) or fact_sheet(session, lead.lead_id).get("asking_price_aud")
    if value:
        opp["monetaryValue"] = float(value)

    if link.opportunity_id is None:
        sheet = fact_sheet(session, lead.lead_id)
        car = " ".join(str(x) for x in (sheet.get("year"), sheet.get("make"), sheet.get("model")) if x)
        opp.setdefault("pipelineStageId", stages.get(STAGE_FOR_STATE[LeadState.NEW].lower()))
        body = {"pipelineId": pipeline_id, "contactId": link.contact_id, "name": car or "Vehicle", **opp}
        try:
            link.opportunity_id = client.create_opportunity(body)
        except GhlError:
            # GHL allows one opportunity per contact per pipeline unless the sub-account says otherwise,
            # so a seller's second car is refused. Take over their existing card: it then shows the car
            # they are selling now. Anything else that went wrong still fails the job.
            existing = client.find_opportunity(link.contact_id, pipeline_id)
            if existing is None:
                raise
            link.opportunity_id = existing
            client.update_opportunity(existing, {"pipelineId": pipeline_id, "name": body["name"], **opp})
    else:
        client.update_opportunity(link.opportunity_id, {"pipelineId": pipeline_id, **opp})

    tag, old_tag = TAG_FOR_STATE.get(state), TAG_FOR_STATE.get(before) if before else None
    if old_tag and old_tag != tag:
        client.remove_tags(link.contact_id, [old_tag])
    if tag and tag != old_tag:
        client.add_tags(link.contact_id, [tag])
        if tag == "needs-human":
            client.add_note(
                link.contact_id,
                f"Acquisition bot handed this seller to a person ({_last_trigger(session, lead.lead_id)}). "
                "Reply from the bot console so the conversation stays in one place.",
            )

    link.synced_state = state
    link.synced_at = datetime.now(UTC)
    session.flush()


@job_handler("ghl_sync")
def _handle_ghl_sync(session: Session, payload: dict[str, Any]) -> None:
    sync_lead(session, uuid.UUID(payload["lead_id"]))


@job_handler("ghl_tag_contact")
def _handle_ghl_tag(session: Session, payload: dict[str, Any]) -> None:
    settings = get_settings()
    if not settings.ghl_enabled:
        return
    client = get_client(settings)
    client.add_tags(payload["contact_id"], [payload["tag"]])
    if payload.get("note"):
        client.add_note(payload["contact_id"], payload["note"])


# --------------------------------------------------------------------------------------
# GHL → bot
# --------------------------------------------------------------------------------------

# What the workflow's webhook should carry, as Custom Data keys. Contact custom fields arrive at the
# top level under their display names, so those are read too — whichever the workflow sends.
_ALIASES: dict[str, tuple[str, ...]] = {
    "make": ("make",),
    "model": ("model",),
    "variant": ("variant",),
    "year": ("year",),
    "odometer_km": ("odometer_km", "odometer (km)", "odometer", "km"),
    "asking_price": ("asking_price", "asking price", "price"),
    "rego": ("rego",),
    "rego_state": ("rego_state", "rego state", "state"),
    "vin": ("vin",),
    "transmission": ("transmission",),
    "fuel": ("fuel",),
    "suburb": ("suburb", "city"),
    "postcode": ("postcode", "postal_code", "postal code"),
    "listing_url": ("listing_url", "listing url"),
    "lead_source": ("lead_source", "lead source", "source"),
    "notes": ("notes", "description"),
}
REQUIRED = ("make", "model", "year", "odometer_km", "asking_price", "postcode")


def _pick(payload: dict[str, Any], field: str) -> str:
    custom = payload.get("customData") or {}
    for source in (custom, payload):
        lowered = {str(k).strip().lower(): v for k, v in source.items()}
        for alias in _ALIASES[field]:
            v = lowered.get(alias)
            if v not in (None, "", []):
                return str(v).strip()
    return ""


def _number(raw: str) -> int | None:
    digits = re.sub(r"[^\d.]", "", raw)
    try:
        return int(float(digits)) if digits else None
    except ValueError:
        return None


def _choice(raw: str, options: dict[str, str], default: str) -> str:
    raw = raw.strip().lower()
    for word, value in options.items():
        if word in raw:
            return value
    return default


def lead_from_webhook(payload: dict[str, Any], settings: Settings) -> tuple[dict[str, Any] | None, list[str]]:
    """(lead contract payload, []) or (None, missing field names)."""
    contact_id = str(payload.get("contact_id") or payload.get("contactId") or "").strip()
    phone = str(payload.get("phone") or "").strip()
    fields = {f: _pick(payload, f) for f in _ALIASES}
    missing = [f for f in REQUIRED if not fields[f]]
    if not contact_id:
        missing.insert(0, "contact_id")
    if not phone:
        missing.append("phone")
    numbers = {k: _number(fields[k]) for k in ("year", "odometer_km", "asking_price")}
    missing += [k for k, v in numbers.items() if fields[k] and v is None]
    if missing:
        return None, missing

    location_id = settings.ghl_location_id or str((payload.get("location") or {}).get("id") or "")
    name = (
        str(payload.get("full_name") or "").strip()
        or " ".join(str(payload.get(k) or "").strip() for k in ("first_name", "last_name")).strip()
        or "Seller"
    )
    state = fields["rego_state"].upper()
    # Same contact, same car → same lead (a workflow that fires twice). Same contact, another car → a new one.
    car_key = "|".join(fields[k].strip().lower() for k in ("make", "model")) + f"|{numbers['year']}"
    lead = {
        "schema_version": "1.0",
        "lead_id": str(uuid.uuid5(_LEAD_NAMESPACE, f"ghl:{location_id}:{contact_id}:{car_key}")),
        "source": "ghl",
        "listing_url": fields["listing_url"]
        or f"https://app.gohighlevel.com/v2/location/{location_id}/contacts/detail/{contact_id}",
        "seller": {"platform_id": f"ghl-contact:{contact_id}", "display_name": name[:128], "phone": phone},
        "vehicle_claimed": {
            "make": fields["make"],
            "model": fields["model"],
            "variant": fields["variant"] or None,
            "year": numbers["year"],
            "odometer_km": numbers["odometer_km"],
            "rego": fields["rego"] or None,
            "vin": fields["vin"] or None,
            "transmission": _choice(fields["transmission"], {"man": "manual"}, "auto"),
            "fuel": _choice(
                fields["fuel"], {"diesel": "diesel", "hybrid": "hybrid", "elec": "ev", "ev": "ev"}, "petrol"
            ),
            "asking_price_aud": numbers["asking_price"],
            "description_raw": fields["notes"],
            "images": [],
        },
        "location": {
            "suburb": fields["suburb"] or "Not given",
            "state": state if state in ("VIC", "NSW", "QLD", "SA", "WA", "TAS", "NT", "ACT") else "VIC",
            "postcode": fields["postcode"],
        },
        "qualified_at": datetime.now(UTC).isoformat(),
        "qualification_notes": f"From GoHighLevel ({fields['lead_source'] or 'workflow'}).",
    }
    return lead, []


def link_inbound(session: Session, lead_id: uuid.UUID, contact_id: str) -> None:
    """Remember the GHL contact a webhook lead came from, so the sync updates it instead of upserting."""
    if session.get(CrmLink, lead_id) is None:
        session.add(CrmLink(lead_id=lead_id, contact_id=contact_id))
        session.flush()


def flag_missing(session: Session, contact_id: str, missing: list[str]) -> None:
    """Tag the contact so staff see why the bot did not pick the lead up."""
    if contact_id and get_settings().ghl_enabled:
        enqueue(
            session,
            "ghl_tag_contact",
            {
                "contact_id": contact_id,
                "tag": MISSING_DETAILS_TAG,
                "note": "Acquisition bot could not start: missing " + ", ".join(missing) + ".",
            },
            dedupe_key=f"ghl_missing:{contact_id}",
        )
