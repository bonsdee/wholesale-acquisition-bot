"""Five photos, as Direct Auto's own sell form asks (21 Sep).

The question, the chase-up and the gating count all have to agree on what "five" means. The old
wording asked for "six" and then listed eight shots — six exterior angles, the interior and the dash.
"""

import pytest
from sqlalchemy import select

from acqbot.config import Settings
from acqbot.conversation import templates as T
from acqbot.conversation.compose import photos_partial_instruction
from acqbot.db import session_scope
from acqbot.demo import ANSWERS, _asked_field, _seller_says, _state
from acqbot.facts.fields import PHOTO_SHOTS, PHOTO_SHOTS_COUNT, photos_needed, spec_for
from acqbot.facts.store import fact_sheet
from acqbot.ingestion.service import ingest_lead
from acqbot.models import Message
from acqbot.queue.worker import drain
from acqbot.simulator import make_lead
from acqbot.transport.registry import reset_console_transport

FIVE = "the front, the back, both sides, and the dash with the ignition on so the odometer shows"


def test_five_is_the_default_and_the_list_is_five_shots():
    assert Settings.model_fields["min_photos"].default == PHOTO_SHOTS_COUNT == 5
    assert PHOTO_SHOTS == FIVE  # front, back, left, right, dash


def test_the_question_names_the_shots_and_no_count():
    ask = spec_for("photos").ask
    assert ask == f"Last thing — can you send photos of {FIVE}?"
    # A number in the question would contradict ACQBOT_MIN_PHOTOS the day someone changes it.
    assert not any(ch.isdigit() for ch in ask)
    assert "six" not in ask and "interior" not in ask


def test_the_chase_up_counts_exactly_what_it_lists():
    assert T.photos_partial(3, 5) == f"Got 3 — could you send the rest? I need 5 in total: {FIVE}."


def test_another_minimum_turns_the_list_into_a_guide_rather_than_miscounting_it():
    assert photos_needed(8) == f"8 in total, ideally including {FIVE}"
    assert "8 in total, ideally including" in photos_partial_instruction(2, 8)


@pytest.mark.parametrize("bad", [0, -1])
def test_a_minimum_below_one_is_refused(bad):
    with pytest.raises(ValueError):
        Settings(min_photos=bad)


def _outbound_bodies(lead_id) -> list[tuple[str, str]]:
    with session_scope() as s:
        msgs = s.scalars(
            select(Message)
            .where(Message.lead_id == lead_id, Message.direction == "outbound")
            .order_by(Message.sent_at, Message.msg_id)
        )
        return [((m.validation_notes or {}).get("template"), m.body) for m in msgs]


def test_three_photos_are_chased_for_two_more_and_five_move_the_seller_on():
    console = reset_console_transport()
    payload = make_lead("clean", seed=5)
    seller_id = "seller-photos"
    payload["seller"]["platform_id"] = seller_id
    with session_scope() as s:
        lead_id = ingest_lead(s, payload).lead_id
    drain("test")
    vc = payload["vehicle_claimed"]
    fill = {
        "variant": vc.get("variant") or "GX",
        "rego": vc.get("rego") or "1AB2CD",
        "odometer": f"{vc['odometer_km'] + 400:,}",
    }

    def say(text, **kw):
        return _seller_says(console, lead_id, text, seller_id=seller_id, **kw)

    say("Hi, saw you're keen on my car", ref=True)
    for _ in range(20):  # answer everything until the photo question arrives
        asked = _asked_field(lead_id)
        if asked == "photos":
            break
        say(ANSWERS.get(asked, "Sure").format(**fill))
    assert _asked_field(lead_id) == "photos"

    shot = lambda i: {"type": "image", "url": f"https://example.invalid/{lead_id}/{i}.jpg"}  # noqa: E731
    say("Here's a few", attachments=[shot(i) for i in range(3)])
    template, body = _outbound_bodies(lead_id)[-1]
    assert template == "photos_partial"
    assert body == f"Got 3 — could you send the rest? I need 5 in total: {FIVE}."

    say("And the rest", attachments=[shot(i) for i in range(3, 5)])
    with session_scope() as s:
        assert len(fact_sheet(s, lead_id).get("photos") or []) == 5
    assert _asked_field(lead_id) != "photos"  # five is enough: discovery is done with photos
    assert _state(lead_id) in {"VERIFICATION", "PRICED", "DISCOVERY"}
    assert not any(t == "photos_partial" for t, _ in _outbound_bodies(lead_id)[-1:])
