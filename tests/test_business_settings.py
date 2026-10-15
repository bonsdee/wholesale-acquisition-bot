"""The threshold and margin are set from the console, default to .env, and every change is kept."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from acqbot import business_settings as bs
from acqbot.api.app import create_app
from acqbot.db import session_scope
from acqbot.enrichment.pipeline import enrich_lead
from acqbot.ingestion.service import ingest_lead
from acqbot.models import BusinessSetting, Lead, LeadState
from acqbot.simulator import make_lead
from acqbot.valuation.service import engine_config_from_settings

TOKEN = "console-test-token"


@pytest.fixture
def signed_in(settings_env):
    settings_env(admin_token=TOKEN)
    client = TestClient(create_app(), follow_redirects=False)
    assert client.post("/console/login", data={"token": TOKEN}).status_code == 303
    return client


def _save(client, threshold, margin, by="Vic"):
    return client.post(
        "/console/settings",
        data={"high_value_threshold_aud": str(threshold), "target_margin_pct": str(margin), "by": by},
    )


def test_defaults_come_from_config_until_someone_changes_them(session):
    eff = bs.effective(session)
    assert eff.high_value_threshold_aud == 60_000 and eff.target_margin_pct == pytest.approx(0.10)
    assert eff.changed == {}


def test_a_change_from_the_console_is_in_force_and_on_the_record(signed_in):
    r = _save(signed_in, 50_000, 12.5)
    assert r.status_code == 303
    with session_scope() as s:
        eff = bs.effective(s)
        assert eff.high_value_threshold_aud == 50_000 and eff.target_margin_pct == pytest.approx(0.125)
        assert {row.changed_by for row in bs.history(s)} == {"Vic"}
    page = signed_in.get("/console/settings").text
    assert 'value="50000"' in page and 'value="12.5"' in page and "Vic" in page


def test_saving_the_same_values_writes_nothing(signed_in):
    _save(signed_in, 50_000, 12)
    _save(signed_in, 50_000, 12)
    with session_scope() as s:
        assert len(bs.history(s)) == 2  # one row per setting, not four


def test_out_of_range_or_nameless_changes_are_refused_and_nothing_is_saved(signed_in):
    for threshold, margin, by in [
        (50_000, 45, "Vic"),
        (100, 10, "Vic"),
        (50_000, 10, "  "),
        ("lots", 10, "Vic"),
    ]:
        r = _save(signed_in, threshold, margin, by)
        assert r.status_code == 303 and "m=bad:" in r.headers["location"]
    with session_scope() as s:
        assert bs.history(s) == []


def test_the_console_threshold_decides_which_leads_go_to_a_person(session):
    payload = make_lead("clean", seed=71)
    payload["vehicle_claimed"]["asking_price_aud"] = 55_000  # under the 60k default
    bs.change(session, bs.THRESHOLD.key, 50_000, "Vic")
    session.commit()
    lead_id = ingest_lead(session, payload).lead_id
    session.commit()
    summary = enrich_lead(session, lead_id)
    session.commit()
    assert summary.get("escalated") == "high_value"
    assert session.get(Lead, lead_id).state == LeadState.HUMAN


def test_the_console_margin_is_what_the_valuation_engine_uses(session):
    assert engine_config_from_settings(session).target_margin_pct == pytest.approx(0.10)
    bs.change(session, bs.MARGIN.key, 0.15, "Vic")
    assert engine_config_from_settings(session).target_margin_pct == pytest.approx(0.15)
    assert engine_config_from_settings().target_margin_pct == pytest.approx(0.10)  # no session: .env


def test_the_record_of_changes_cannot_be_rewritten(session):
    bs.change(session, bs.MARGIN.key, 0.12, "Vic")
    session.commit()
    row = session.query(BusinessSetting).one()
    with pytest.raises(DBAPIError, match=r"append-only|write-once"):
        session.execute(
            text("UPDATE business_settings SET value = 0.5 WHERE setting_id = :id"), {"id": row.setting_id}
        )
    session.rollback()
    with pytest.raises(DBAPIError, match=r"append-only|write-once"):
        session.execute(text("DELETE FROM business_settings WHERE setting_id = :id"), {"id": row.setting_id})
    session.rollback()
