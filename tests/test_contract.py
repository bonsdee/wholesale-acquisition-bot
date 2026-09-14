import pytest

from acqbot.contracts import LeadContractError, UnsupportedSchemaVersion, parse_lead
from acqbot.simulator import make_lead


def test_simulated_lead_parses():
    lead = parse_lead(make_lead("clean", seed=1))
    assert lead.schema_version == "1.0"
    assert lead.location.state == "VIC"
    assert lead.vehicle_claimed.odometer_km >= 0


def test_unknown_field_is_rejected_loudly():
    payload = make_lead("clean", seed=2)
    payload["vehicle_claimed"]["colour"] = "white"
    with pytest.raises(LeadContractError) as exc:
        parse_lead(payload)
    assert any(e["loc"].endswith("colour") for e in exc.value.errors)


def test_bad_vin_is_rejected():
    payload = make_lead("clean", seed=3)
    payload["vehicle_claimed"]["vin"] = "IOQ1234567890ABCD"  # contains I, O, Q
    with pytest.raises(LeadContractError):
        parse_lead(payload)


def test_vin_and_rego_are_normalised():
    payload = make_lead("clean", seed=4)
    payload["vehicle_claimed"]["vin"] = " jhmge8h50dc012345 "
    payload["vehicle_claimed"]["rego"] = "1ab-2cd"
    lead = parse_lead(payload)
    assert lead.vehicle_claimed.vin == "JHMGE8H50DC012345"
    assert lead.vehicle_claimed.rego == "1AB2CD"


def test_phone_is_normalised_to_e164():
    payload = make_lead("clean", seed=5)
    payload["seller"]["phone"] = "0412 345 678"
    assert parse_lead(payload).seller.phone == "+61412345678"
    payload["seller"]["phone"] = "123"
    with pytest.raises(LeadContractError):
        parse_lead(payload)


def test_unsupported_schema_version():
    payload = make_lead("clean", seed=6)
    payload["schema_version"] = "2.0"
    with pytest.raises(UnsupportedSchemaVersion):
        parse_lead(payload)


def test_non_object_payload():
    with pytest.raises(LeadContractError):
        parse_lead(["not", "an", "object"])
