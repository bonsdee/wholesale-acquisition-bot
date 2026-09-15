"""The model proposes; code validates. Nothing reaches the fact store without coercion."""

from datetime import date

from acqbot.llm.schemas import (
    EXTRACTION_SCHEMA,
    GENERATION_SCHEMA,
    coerce_fact,
    parse_extraction,
    parse_generation,
)

TODAY = date(2026, 9, 15)


def test_enum_fields_accept_only_the_vocabulary():
    assert coerce_fact("service_history", "full") == ("service_history", "full")
    assert coerce_fact("service_history", "Full") == ("service_history", "full")
    assert coerce_fact("service_history", "mostly") is None
    assert coerce_fact("panel_paint_condition", "good, a few scratches") == ("panel_paint_condition", "good")
    assert coerce_fact("tyre_condition", "bald") is None
    assert coerce_fact("tyre_condition", "replace") == ("tyre_condition", "replace")


def test_numbers_are_ranged_and_normalised():
    assert coerce_fact("odometer_km", "84500") == ("odometer_km", 84500)
    assert coerce_fact("odometer_km", "84,500 km") == ("odometer_km", 84500)
    assert coerce_fact("odometer_km", "85k") == ("odometer_km", 85000)
    assert coerce_fact("odometer_km", "12") is None  # below the plausible floor
    assert coerce_fact("keys_count", "2") == ("keys_count", 2)
    assert coerce_fact("keys_count", "two") == ("keys_count", 2)
    assert coerce_fact("keys_count", "40") is None
    assert coerce_fact("year", "2015", today=TODAY) == ("year", 2015)
    assert coerce_fact("year", "2031", today=TODAY) is None


def test_structured_fields_follow_the_wire_format():
    assert coerce_fact("finance_owing", "none") == ("finance_owing", {"owing": False, "amount_aud": None})
    assert coerce_fact("finance_owing", "owing:5000") == (
        "finance_owing",
        {"owing": True, "amount_aud": 5000.0},
    )
    assert coerce_fact("finance_owing", "owing") == ("finance_owing", {"owing": True, "amount_aud": None})
    assert coerce_fact("finance_owing", "maybe") is None
    assert coerce_fact("write_off_status", "written_off:hail") == (
        "write_off_status",
        {"written_off": True, "type": "hail"},
    )
    assert coerce_fact("write_off_status", "none") == (
        "write_off_status",
        {"written_off": False, "type": None},
    )
    assert coerce_fact("mechanical_faults", "none") == ("mechanical_faults", {"none": True})
    assert coerce_fact("mechanical_faults", "slow oil leak; abs warning light") == (
        "mechanical_faults",
        {"items": ["slow oil leak", "abs warning light"], "warning_lights": True},
    )
    assert coerce_fact("rego_status", "current:2027-03") == (
        "rego_status",
        {"status": "current", "expiry": "2027-03-01"},
    )
    assert coerce_fact("rego_status", "unregistered") == (
        "rego_status",
        {"status": "unregistered", "expiry": None},
    )
    assert coerce_fact("rego_status", "soon") is None


def test_identifiers_are_validated_and_a_vin_given_as_rego_is_relabelled():
    assert coerce_fact("rego", "1ab2cd") == ("rego", "1AB2CD")
    assert coerce_fact("rego", "MK2") is None
    assert coerce_fact("rego", "JTDKN3DU5A0123456") == ("vin", "JTDKN3DU5A0123456")
    assert coerce_fact("vin", "not a vin") is None


def test_parse_extraction_drops_bad_values_but_keeps_the_rest():
    data = {
        "facts": [
            {"field": "odometer_km", "value": "84,500", "confidence": 1.0},
            {"field": "service_history", "value": "sort of", "confidence": 0.9},
            {"field": "colour", "value": "white", "confidence": 1.0},
            {"field": "keys_count", "value": 2, "confidence": 1.0},
        ],
        "answered_pending": True,
        "intents": ["Question", "bogus"],
        "flags": ["DISTRESS"],
        "counter_price_aud": 7000,
        "phone": "call me on 0412 345 678",
        "seller_question": "  do you   pick up? ",
        "notes": ["second owner", "", 5],
    }
    out = parse_extraction(data, today=TODAY)
    assert [(f.field, f.value) for f in out.facts] == [("odometer_km", 84500)]
    assert {r["field"] for r in out.rejected} == {"service_history", "colour", "keys_count"}
    assert out.intents == {"question"} and out.flags == {"distress"}
    assert out.counter_price_aud == 7000 and out.phone == "+61412345678"
    assert out.seller_question == "do you pick up?" and out.notes == ["second owner"]
    assert parse_extraction(None) is None and parse_extraction({"facts": "no"}) is not None


def test_parse_generation_is_lenient_about_case_and_strict_about_shape():
    out = parse_generation(
        {
            "message": " Thanks. ",
            "proposed_state": "discovery",
            "confidence": 2,
            "escalate": False,
            "escalate_reason": "x",
        }
    )
    assert out.message == "Thanks." and out.proposed_state == "DISCOVERY" and out.confidence == 1.0
    assert out.escalate is False and out.escalate_reason is None
    out = parse_generation({"message": "x", "escalate": True, "escalate_reason": "Distress"})
    assert out.escalate and out.escalate_reason == "distress"
    assert parse_generation({"proposed_state": "DISCOVERY"}) is None


def test_schemas_stay_within_structured_output_limits():
    def walk(node, acc):
        if isinstance(node, dict):
            if "properties" in node:
                req = set(node.get("required", []))
                acc["optional"] += len([p for p in node["properties"] if p not in req])
                assert node.get("additionalProperties") is False
            t = node.get("type")
            if isinstance(t, list) or "anyOf" in node:
                acc["unions"] += 1
            for v in node.values():
                walk(v, acc)
        elif isinstance(node, list):
            for v in node:
                walk(v, acc)

    for schema in (EXTRACTION_SCHEMA, GENERATION_SCHEMA):
        acc = {"optional": 0, "unions": 0}
        walk(schema, acc)
        assert acc["optional"] <= 24 and acc["unions"] <= 16
