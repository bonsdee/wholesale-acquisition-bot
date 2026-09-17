"""Phase 6 — surviving the Messenger window: the number, the channel switch, the nudges.

Section 4.2 is three stages and the gap between them is where leads die: Messenger goes quiet after
24 hours, and a conversation with nowhere to go is a conversation that ends. These tests are the
three things that have to work for it not to.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from acqbot.conversation import templates as T
from acqbot.conversation.gate import GateContext, validate
from acqbot.conversation.service import WAITING_STATES, Conversation
from acqbot.db import session_scope
from acqbot.demo import run_demo
from acqbot.facts.fields import DISCOVERY_REQUIRED_KEYS, SELLER_PHONE
from acqbot.models import Channel, Escalation, Job, Lead, LeadState, Message, Thread
from acqbot.transport.registry import console_transport


def _outbound(lead_id):
    with session_scope() as s:
        return list(
            s.scalars(
                select(Message)
                .where(Message.lead_id == lead_id, Message.direction == "outbound")
                .order_by(Message.sent_at, Message.msg_id)
            )
        )


def _asked(lead_id):
    return [(m.validation_notes or {}).get("asked_field") for m in _outbound(lead_id)]


def _templates(lead_id):
    return [(m.validation_notes or {}).get("template") for m in _outbound(lead_id)]


# ------------------------------------------------------------------ the number


def test_the_number_is_asked_for_but_never_gates_pricing():
    # 4.2 Stage 2 wants a mobile early. 5.1 lists what gates DISCOVERY, and this is not on it — a
    # seller who won't hand over their number still gets priced and still gets an offer.
    assert SELLER_PHONE.key not in DISCOVERY_REQUIRED_KEYS
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    assert SELLER_PHONE.key in _asked(run.lead_id)
    assert run.final_state == "HANDOFF"
    with session_scope() as s:
        from acqbot.facts.store import fact_sheet

        assert fact_sheet(s, run.lead_id).get("seller_phone")


def test_it_is_asked_once_and_then_let_go():
    # A seller who ignores the question gets asked for photos next, not asked again. Pressing twice
    # for a phone number is how a cooperative seller becomes an ex-seller.
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    asks = [a for a in _asked(run.lead_id) if a == SELLER_PHONE.key]
    assert len(asks) == 1


def test_the_ask_says_why_and_does_not_read_as_a_data_grab():
    body = T.ask_field(SELLER_PHONE)
    assert "offer" in body.lower()  # the reason, not just the request
    assert validate(body, GateContext(stage=LeadState.DISCOVERY)).ok


# ------------------------------------------------------------------ the channel switch


class _ShutWindow:
    """Messenger after 24 hours of silence: it will take nothing else."""

    channel = Channel.MESSENGER
    max_body_length = 2000

    def __init__(self):
        self.sent = []

    def send_window_open(self, thread):
        return False

    def send(self, thread, body):
        raise AssertionError("sent into a closed window")

    def poll(self, since):
        return []


def test_a_shut_window_with_a_number_on_file_moves_to_sms(settings_env, monkeypatch):
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    sent = []

    class _Sms:
        channel = Channel.SMS
        max_body_length = 1000

        def send_window_open(self, thread):
            return True

        def send(self, thread, body):
            from acqbot.transport.protocol import MessageReceipt

            sent.append((thread.external_id, body))
            return MessageReceipt(external_msg_id="sms-1", sent_at=datetime.now(UTC))

        def poll(self, since):
            return []

    monkeypatch.setattr("acqbot.transport.registry.get_transport", lambda ch: _Sms())
    with session_scope() as s:
        lead = s.get(Lead, run.lead_id)
        lead.state = LeadState.DISCOVERY  # put it back in a state that still talks
        conv = Conversation(s, _ShutWindow())
        thread = s.scalars(select(Thread).where(Thread.lead_id == run.lead_id)).first()
        msg = conv._send(lead, thread, "Just checking in on the car.", "nudge_discovery")
        assert msg is not None, "the message was dropped instead of moving to SMS"

    # Normalised to E.164 on the way into the fact store, which is what an SMS gateway wants.
    assert sent and sent[0][0] == "+61412345678"
    with session_scope() as s:
        sms_threads = list(
            s.scalars(select(Thread).where(Thread.lead_id == run.lead_id, Thread.channel == Channel.SMS))
        )
        assert len(sms_threads) == 1
        # The message is recorded against the SMS thread, which is the record of where it switched.
        assert s.get(Message, msg.msg_id).thread_id == sms_threads[0].thread_id


def test_a_shut_window_with_no_number_asks_a_person_instead():
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    with session_scope() as s:
        lead = s.get(Lead, run.lead_id)
        lead.state = LeadState.DISCOVERY
        # Wipe the number: nothing to migrate to.
        conv = Conversation(s, _ShutWindow())
        conv.cfg.sms_migration = False
        thread = s.scalars(select(Thread).where(Thread.lead_id == run.lead_id)).first()
        assert conv._send(lead, thread, "Just checking in.", "nudge_discovery") is None
        reasons = [
            e.reason
            for e in s.scalars(
                select(Escalation).where(Escalation.lead_id == run.lead_id, Escalation.resolved_at.is_(None))
            )
        ]
        assert "send_window_closed" in reasons


# ------------------------------------------------------------------ the nudges


def test_a_nudge_is_scheduled_whenever_the_ball_is_in_the_sellers_court():
    run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    with session_scope() as s:
        jobs = list(s.scalars(select(Job).where(Job.kind == "nudge")))
    assert jobs, "nothing was scheduled to chase a seller who goes quiet"
    assert all(j.run_at > datetime.now(UTC) for j in jobs), "a nudge was scheduled to fire immediately"


def test_a_nudge_that_fires_early_reschedules_itself_instead_of_pestering(settings_env):
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    with session_scope() as s:
        lead = s.get(Lead, run.lead_id)
        lead.state = LeadState.DISCOVERY
        thread = s.scalars(select(Thread).where(Thread.lead_id == run.lead_id)).first()
        thread.last_inbound_at = datetime.now(UTC) - timedelta(hours=1)
        before = len(_outbound(run.lead_id))
        r = Conversation(s, console_transport()).nudge(lead, thread)
    assert r.action == "nudge_deferred"
    assert len(_outbound(run.lead_id)) == before, "it nudged before the silence was long enough"


def test_a_seller_who_replied_is_not_chased():
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    with session_scope() as s:
        lead = s.get(Lead, run.lead_id)
        lead.state = LeadState.DISCOVERY
        thread = s.scalars(select(Thread).where(Thread.lead_id == run.lead_id)).first()
        thread.last_inbound_at = datetime.now(UTC) + timedelta(minutes=1)  # replied after our last
        r = Conversation(s, console_transport()).nudge(lead, thread)
    assert r.action == "nudge_not_needed"


def test_the_cadence_runs_out_and_the_lead_stalls_rather_than_nagging(settings_env):
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    # Collapse the cadence to zero AFTER the run, so the nudges are the only thing being exercised
    # and the conversation itself happened at the real spacing.
    settings_env(nudge_before_window_closes_hours=0, sms_nudge_hours="[0,0]", max_nudges=2)
    console = console_transport()
    with session_scope() as s:
        lead = s.get(Lead, run.lead_id)
        lead.state = LeadState.DISCOVERY
        thread = s.scalars(select(Thread).where(Thread.lead_id == run.lead_id)).first()
        conv = Conversation(s, console)
        assert conv.nudge_schedule() == [0, 0]
        assert conv.nudge(lead, thread).action == "nudged"
        assert conv.nudge(lead, thread).action == "nudged"
        third = conv.nudge(lead, thread)
    assert third.action == "stalled"
    with session_scope() as s:
        assert s.get(Lead, run.lead_id).state == LeadState.STALLED
        reasons = [
            e.reason
            for e in s.scalars(
                select(Escalation).where(Escalation.lead_id == run.lead_id, Escalation.resolved_at.is_(None))
            )
        ]
        assert "stalled_no_reply" in reasons
    tmpl = _templates(run.lead_id)
    assert tmpl.count("nudge_discovery") == 2 and tmpl[-1] == "stalled_close"


def test_nudges_never_carry_pressure_a_figure_or_a_new_deadline():
    identity = T.Identity(agent="Alex", dealership="Placeholder Motors", lmct="00000")
    ladder = {"opening": 16_150, "step_1": 17_100, "step_2": 17_800, "floor": 18_350}
    for body, stage in (
        (T.nudge_discovery(identity, "service history"), LeadState.DISCOVERY),
        (T.nudge_discovery(identity, None), LeadState.DISCOVERY),
        (T.nudge_offer(identity), LeadState.OFFER_MADE),
        (T.stalled_close(identity), LeadState.DISCOVERY),
    ):
        r = validate(body, GateContext(stage=stage, ladder=ladder))
        assert r.ok, (body, r.violations)
        assert "$" not in body
        for word in ("last chance", "act now", "hurry", "final", "expires today", "other buyers"):
            assert word not in body.lower()
    # And an easy way out is always offered — a nudge without one is a nag.
    assert "fine" in T.nudge_discovery(identity, None).lower()
    assert "no hard feelings" in T.nudge_offer(identity).lower()


def test_states_where_the_ball_is_ours_are_never_nudged():
    # Nudging a seller while WE are the ones running the PPSR check reads as incompetent.
    assert LeadState.VERIFICATION not in WAITING_STATES
    assert LeadState.PRICED not in WAITING_STATES
    assert LeadState.HUMAN not in WAITING_STATES
    for terminal in (LeadState.HANDOFF, LeadState.ARCHIVED, LeadState.REJECTED, LeadState.STALLED):
        assert terminal not in WAITING_STATES


def test_the_first_sms_identifies_the_sender_and_offers_an_opt_out(monkeypatch):
    # Spam Act 2003: a commercial electronic message must identify the sender and carry a low-cost
    # way to stop it. The Section 8 disclosure is in the first MESSENGER message — a seller whose
    # conversation migrates gets a text from an unknown number and has been told neither.
    run = run_demo("clean", "accept", seed=42, human_presents=False, model="off")
    sent = []

    class _Sms:
        channel = Channel.SMS
        max_body_length = 1000

        def send_window_open(self, thread):
            return True

        def send(self, thread, body):
            from acqbot.transport.protocol import MessageReceipt

            sent.append(body)
            return MessageReceipt(external_msg_id=f"sms-{len(sent)}", sent_at=datetime.now(UTC))

        def poll(self, since):
            return []

    monkeypatch.setattr("acqbot.transport.registry.get_transport", lambda ch: _Sms())
    with session_scope() as s:
        lead = s.get(Lead, run.lead_id)
        lead.state = LeadState.DISCOVERY
        conv = Conversation(s, _ShutWindow())
        thread = s.scalars(select(Thread).where(Thread.lead_id == run.lead_id)).first()
        conv._send(lead, thread, "Just checking in on the car.", "nudge_discovery")
        conv._send(lead, thread, "And one more thing.", "nudge_discovery")

    assert len(sent) == 2
    first, second = sent
    assert "LMCT" in first and "STOP" in first  # who it is, and how to make it stop
    assert "Just checking in on the car." in first
    # Said once, not stapled to every text.
    assert "LMCT" not in second and "STOP" not in second


def test_the_sms_preamble_passes_the_gate_and_fits_a_text():
    from acqbot.conversation import templates as T

    identity = T.Identity(agent="Alex", dealership="Placeholder Motors", lmct="00000")
    body = T.sms_first_contact(identity) + "No rush — just checking in on the car."
    r = validate(body, GateContext(stage=LeadState.DISCOVERY, max_length=1000))
    assert r.ok, r.violations
    assert len(body) < 320  # two SMS segments at most
