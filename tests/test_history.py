"""Section 7.3: last N turns verbatim, older turns as a rolling summary, nothing dropped."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from acqbot.config import get_settings
from acqbot.conversation.history import build_history, to_api_messages
from acqbot.db import session_scope
from acqbot.ingestion.service import ingest_lead
from acqbot.llm.client import ModelError
from acqbot.llm.fake import ScriptedFakeClient
from acqbot.models import Channel, Direction, Lead, Message, ModelCall, Thread
from acqbot.simulator import make_lead


def _msg(thread, lead, direction, body, at, attachments=None):
    return Message(
        thread_id=thread.thread_id,
        lead_id=lead.lead_id,
        direction=direction,
        body=body,
        sent_at=at,
        attachments=attachments,
    )


def _thread_with_turns(session, n_turns, seed=50):
    lead_id = ingest_lead(session, make_lead("clean", seed=seed)).lead_id
    lead = session.get(Lead, lead_id)
    thread = Thread(lead_id=lead.lead_id, channel=Channel.CONSOLE, external_id=f"c-{seed}")
    session.add(thread)
    session.flush()
    t0 = datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    for i in range(n_turns):
        direction = Direction.OUTBOUND if i % 2 == 0 else Direction.INBOUND
        session.add(_msg(thread, lead, direction, f"turn {i}", t0 + timedelta(minutes=i)))
    session.flush()
    return thread, lead


def test_api_messages_alternate_and_end_on_the_seller():
    class M:
        def __init__(self, d, body, attachments=None):
            self.direction, self.body, self.attachments = d, body, attachments

    turns = [
        M(Direction.OUTBOUND, "Hi, question one?"),
        M(Direction.INBOUND, "answer"),
        M(Direction.INBOUND, "", [{"type": "image"}, {"type": "image"}]),
        M(Direction.OUTBOUND, "thanks, question two?"),
    ]
    out = to_api_messages("earlier stuff", turns)
    assert [m["role"] for m in out] == ["user", "assistant", "user", "assistant", "user"]
    assert out[0]["content"].startswith("[Earlier in this conversation: earlier stuff]")
    assert out[2]["content"] == "answer\n\n[sent 2 photos]"
    assert out[-1]["content"] == "[the seller has not replied yet]"
    assert to_api_messages(None, [])[0]["role"] == "user"


def test_short_conversations_go_in_verbatim_with_no_summary_call(session):
    thread, lead = _thread_with_turns(session, 12)
    client = ScriptedFakeClient()
    h = build_history(session, thread, lead, client=client, settings=get_settings())
    assert h.summary is None and len(h.turns) == 12 and not client.requests


def test_long_conversations_fold_the_oldest_turns_into_a_summary(session):
    cfg = get_settings()
    thread, lead = _thread_with_turns(session, cfg.history_verbatim_turns + cfg.history_summary_batch + 3)
    client = ScriptedFakeClient().queue(
        "summarise", {"summary": "Seller has a 2015 Outlander; odometer given."}
    )
    h = build_history(session, thread, lead, client=client, settings=cfg)
    assert h.summarised_now and h.summary == "Seller has a 2015 Outlander; odometer given."
    assert len(h.turns) == cfg.history_verbatim_turns
    assert h.turns[0].body == f"turn {cfg.history_summary_batch + 3}"
    assert thread.history_summary_through == h.turns[0].sent_at - timedelta(minutes=1)
    session.commit()
    # The folded turns all reached the summariser, nothing skipped.
    req = client.requests[0]
    assert req.purpose == "summarise" and "turn 0" in req.messages[0]["content"]
    assert f"turn {cfg.history_summary_batch + 2}" in req.messages[0]["content"]
    assert f"turn {cfg.history_summary_batch + 3}" not in req.messages[0]["content"]
    # Second build: nothing new to fold, no second call, summary reused.
    h2 = build_history(session, thread, lead, client=client, settings=cfg)
    assert not h2.summarised_now and h2.summary == h.summary and len(client.requests) == 1
    assert h2.messages[0]["content"].startswith("[Earlier in this conversation:")
    with session_scope() as s:
        assert s.scalars(select(ModelCall).where(ModelCall.purpose == "summarise")).one()


def test_failed_summary_keeps_everything_verbatim(session):
    cfg = get_settings()
    n = cfg.history_verbatim_turns + cfg.history_summary_batch + 1
    thread, lead = _thread_with_turns(session, n, seed=51)
    client = ScriptedFakeClient().queue("summarise", ModelError("down", retryable=True))
    h = build_history(session, thread, lead, client=client, settings=cfg)
    assert h.summary is None and len(h.turns) == n and thread.history_summary_through is None
