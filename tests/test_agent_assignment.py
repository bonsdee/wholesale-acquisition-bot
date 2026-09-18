"""Which named buyer a seller deals with, and how many of them one person can be.

Section 1 keeps "multiple named buyers under one identified dealership" after dropping the fake
competition mechanism. That permission comes with an obligation nobody wrote down: the names have
to be *plausible*. One "Alex" holding forty live conversations is not a person, and two sellers who
compare notes will work that out faster than any compliance review.

The two properties that matter pull against each other. Spreading the load means the choice depends
on how busy each name is, which changes minute to minute — and a name that changes underneath a
seller mid-conversation is worse than the crowding it was meant to fix. So: decided once, on load,
and then immutable. These tests are that sentence, taken apart.
"""

from sqlalchemy import select

from acqbot.conversation.service import agent_for, agent_load
from acqbot.ingestion.service import ingest_lead
from acqbot.models import AgentAssignment, Lead, LeadState
from acqbot.observability import checks
from acqbot.simulator import make_lead


def _lead(session, seed: int) -> Lead:
    result = ingest_lead(session, make_lead("clean", seed=seed))
    session.commit()
    return session.get(Lead, result.lead_id)


def _leads(session, n: int, start: int = 100) -> list[Lead]:
    return [_lead(session, seed=start + i) for i in range(n)]


def _check(session, name: str):
    return next(c for c in checks(session).checks if c.name == name)


# ------------------------------------------------------------------ the name never moves


def test_the_name_a_seller_is_given_never_changes(session, settings_env):
    settings_env(agent_names='["Alex","Sam","Jo"]', max_concurrent_per_agent=2)
    lead = _lead(session, seed=1)

    first = agent_for(session, lead, __import__("acqbot.config", fromlist=["get_settings"]).get_settings())
    session.commit()

    # Fill the roster right up so that a fresh decision would certainly land elsewhere.
    from acqbot.config import get_settings

    for other in _leads(session, 6, start=200):
        agent_for(session, other, get_settings())
    session.commit()

    assert agent_for(session, lead, get_settings()) == first


def test_an_assignment_cannot_be_rewritten_even_from_sql(session):
    from acqbot.config import get_settings

    lead = _lead(session, seed=2)
    agent_for(session, lead, get_settings())
    session.commit()

    row = session.get(AgentAssignment, lead.lead_id)
    row.agent = "Someone Else"
    try:
        session.commit()
    except Exception as exc:  # the trigger, not our care, is what guarantees this
        session.rollback()
        assert "immutable" in str(exc).lower() or "forbid" in str(exc).lower()
    else:
        raise AssertionError("agent_assignments accepted an UPDATE; the append-only trigger is missing")


# ------------------------------------------------------------------ the load actually spreads


def test_leads_spread_across_the_roster_instead_of_piling_on_one_name(session, settings_env):
    settings_env(agent_names='["Alex","Sam","Jo"]', max_concurrent_per_agent=12)
    from acqbot.config import get_settings

    for lead in _leads(session, 9, start=300):
        agent_for(session, lead, get_settings())
    session.commit()

    load = agent_load(session, get_settings())
    assert sum(load.values()) == 9
    # Three names, nine leads: nobody should be holding more than a third plus rounding.
    assert max(load.values()) <= 4, load
    assert min(load.values()) >= 2, load


def test_the_cap_binds_before_a_name_is_overloaded(session, settings_env):
    settings_env(agent_names='["Alex","Sam"]', max_concurrent_per_agent=3)
    from acqbot.config import get_settings

    for lead in _leads(session, 6, start=400):
        agent_for(session, lead, get_settings())
    session.commit()

    load = agent_load(session, get_settings())
    assert load == {"Alex": 3, "Sam": 3}


def test_a_finished_conversation_gives_the_slot_back(session, settings_env):
    settings_env(agent_names='["Alex","Sam"]', max_concurrent_per_agent=2)
    from acqbot.config import get_settings

    leads = _leads(session, 4, start=500)
    for lead in leads:
        agent_for(session, lead, get_settings())
    session.commit()
    assert agent_load(session, get_settings()) == {"Alex": 2, "Sam": 2}

    # Two deals close. The names are free again — the assignment rows stay, because they are the
    # record of who said what to whom, but they no longer count as hands that are full.
    for lead in leads[:2]:
        lead.state = LeadState.ACCEPTED
    session.commit()

    load = agent_load(session, get_settings())
    assert sum(load.values()) == 2
    assert session.scalar(select(AgentAssignment).where(AgentAssignment.lead_id == leads[0].lead_id))


# ------------------------------------------------------------------ when there is nowhere to put it


def test_a_full_roster_still_takes_the_seller(session, settings_env):
    # The cap is a preference, not a queue. A seller who has just been asked for six photos must
    # not go unanswered because the dealership is understaffed — that is the failure the whole
    # system exists to prevent.
    settings_env(agent_names='["Alex"]', max_concurrent_per_agent=1)
    from acqbot.config import get_settings

    a, b = _leads(session, 2, start=600)
    assert agent_for(session, a, get_settings()) == "Alex"
    assert agent_for(session, b, get_settings()) == "Alex"
    session.commit()

    assert session.get(AgentAssignment, a.lead_id).over_cap is False
    assert session.get(AgentAssignment, b.lead_id).over_cap is True, "the overflow must be recorded"


def test_a_full_roster_is_reported_rather_than_hidden(session, settings_env):
    settings_env(agent_names='["Alex","Sam"]', max_concurrent_per_agent=1)
    from acqbot.config import get_settings

    c = _check(session, "agent_load")
    assert c.ok, "an empty system is not overloaded"

    for lead in _leads(session, 2, start=700):
        agent_for(session, lead, get_settings())
    session.commit()

    c = _check(session, "agent_load")
    assert not c.ok
    assert "cap" in c.detail and "Alex:1" in c.detail and "Sam:1" in c.detail


def test_one_configured_name_says_so_instead_of_blaming_the_intake(session, settings_env):
    # With a single name there is no spreading to be done, so "add another name" is the only
    # honest advice. Telling a dealership to slow its intake when it has one buyer configured
    # sends them looking in the wrong place.
    settings_env(agent_names='["Alex"]', max_concurrent_per_agent=1)
    from acqbot.config import get_settings

    agent_for(session, _lead(session, seed=800), get_settings())
    session.commit()

    c = _check(session, "agent_load")
    assert not c.ok and "only one name is configured" in c.detail


def test_the_cap_can_be_switched_off(session, settings_env):
    settings_env(agent_names='["Alex","Sam"]', max_concurrent_per_agent=0)
    from acqbot.config import get_settings

    for lead in _leads(session, 4, start=900):
        agent_for(session, lead, get_settings())
    session.commit()

    assert sum(agent_load(session, get_settings()).values()) == 4
    assert all(not r.over_cap for r in session.scalars(select(AgentAssignment)))
    assert not any(c.name == "agent_load" for c in checks(session).checks)
