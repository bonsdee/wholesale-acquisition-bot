import uuid
from datetime import UTC, datetime

from acqbot.conversation.state import Signals, compute_stage
from acqbot.facts.fields import DISCOVERY_REQUIRED_KEYS
from acqbot.facts.store import FactSheet, FactView
from acqbot.models import FactSource, LeadState


def sheet(**facts) -> FactSheet:
    fs = FactSheet()
    for key, spec in facts.items():
        value, source, verified, conf = (
            spec if isinstance(spec, tuple) else (spec, FactSource.SELLER, False, 0.9)
        )
        fv = FactView(uuid.uuid4(), key, value, source, verified, conf, datetime.now(UTC))
        fs.facts[key] = fv
        (fs.confirmed if verified else fs.claimed)[key] = value
    return fs


def full_discovery(**over):
    base = {
        "rego": "1AB2CD",
        "odometer_km": 84_000,
        "service_history": "full",
        "finance_owing": ({"owing": False}, FactSource.PPSR, True, 1.0),
        "write_off_status": ({"written_off": False}, FactSource.PPSR, True, 1.0),
        "panel_paint_condition": "good",
        "mechanical_faults": {"none": True},
        "tyre_condition": "good",
        "keys_count": 2,
        "rego_status": ({"status": "current"}, FactSource.REGO, True, 1.0),
        "photos": (["u1", "u2", "u3", "u4", "u5", "u6"], FactSource.PHOTO, True, 1.0),
    }
    base.update(over)
    return sheet(**base)


def test_new_then_contacted_then_discovery():
    assert compute_stage(LeadState.NEW, sheet(), Signals()).stage == LeadState.NEW
    assert compute_stage(LeadState.NEW, sheet(), Signals(outbound_count=1)).stage == LeadState.CONTACTED
    view = compute_stage(
        LeadState.CONTACTED, sheet(make="Toyota"), Signals(outbound_count=1, inbound_after_first_outbound=1)
    )
    assert view.stage == LeadState.DISCOVERY
    assert view.next_field.key == "rego"  # identifier first
    assert set(view.outstanding) == set(DISCOVERY_REQUIRED_KEYS)


def test_listing_claims_do_not_satisfy_discovery_but_stated_ones_do():
    listing = sheet(odometer_km=(84_000, FactSource.SELLER, False, 0.6), vin="JHMGE8H50DC012345")
    sig = Signals(outbound_count=1, inbound_after_first_outbound=1)
    view = compute_stage(LeadState.DISCOVERY, listing, sig)
    assert "odometer_km" in view.outstanding and "rego" not in view.outstanding  # VIN counts as identifier
    stated = sheet(odometer_km=84_000, vin="JHMGE8H50DC012345")
    assert "odometer_km" not in compute_stage(LeadState.DISCOVERY, stated, sig).outstanding


def test_verification_requires_authoritative_sources():
    sig = Signals(outbound_count=1, inbound_after_first_outbound=1)
    claimed_ppsr = full_discovery(finance_owing={"owing": False}, write_off_status={"written_off": False})
    view = compute_stage(LeadState.DISCOVERY, claimed_ppsr, sig)
    assert view.stage == LeadState.VERIFICATION
    assert set(view.verification_outstanding) == {"finance_owing", "write_off_status"}
    verified = full_discovery()
    view = compute_stage(LeadState.DISCOVERY, verified, sig)
    assert (
        view.stage == LeadState.VERIFICATION
        and view.verification_outstanding == []
        and view.note == "awaiting valuation"
    )


def test_priced_offer_negotiating():
    sig = Signals(outbound_count=1, inbound_after_first_outbound=1, has_verified_valuation=True)
    assert compute_stage(LeadState.VERIFICATION, full_discovery(), sig).stage == LeadState.PRICED
    sig.offers_presented = 1
    assert compute_stage(LeadState.PRICED, full_discovery(), sig).stage == LeadState.OFFER_MADE
    sig.inbound_after_last_offer = 1
    assert compute_stage(LeadState.OFFER_MADE, full_discovery(), sig).stage == LeadState.NEGOTIATING


def test_contradiction_reenters_discovery_until_acknowledged():
    fs = full_discovery()
    fs.contradicted["year"] = {"claimed": 2020, "actual": 2019, "source": "vin"}
    sig = Signals(outbound_count=1, inbound_after_first_outbound=1, has_verified_valuation=True)
    view = compute_stage(LeadState.VERIFICATION, fs, sig)
    assert view.stage == LeadState.DISCOVERY and view.pending_contradictions == ["year"]
    sig.acknowledged_contradictions = {"year"}
    assert compute_stage(LeadState.VERIFICATION, fs, sig).stage == LeadState.PRICED


def test_photos_need_the_minimum_count():
    sig = Signals(outbound_count=1, inbound_after_first_outbound=1, min_photos=6)
    few = full_discovery(photos=(["u1", "u2"], FactSource.PHOTO, True, 1.0))
    view = compute_stage(LeadState.DISCOVERY, few, sig)
    assert view.stage == LeadState.DISCOVERY and view.next_field.key == "photos"


def test_sticky_states_are_never_recomputed():
    for st in (LeadState.HUMAN, LeadState.TERMINATED, LeadState.HANDOFF, LeadState.ARCHIVED):
        assert (
            compute_stage(
                st, full_discovery(), Signals(outbound_count=5, inbound_after_first_outbound=5)
            ).stage
            == st
        )
