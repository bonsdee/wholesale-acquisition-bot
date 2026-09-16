"""The review harness: personas react to what was asked, expectations are scored, the report reads."""

import pytest

from acqbot.personas import ALL, BY_NAME, FULL_DISCOVERY, Persona
from acqbot.review import price_for, render_markdown, run_review


def test_every_persona_declares_what_it_is_testing_and_can_answer_anything():
    assert len(ALL) == len(BY_NAME) >= 12
    for p in ALL:
        assert p.why and p.why[0].isupper(), p.name
        assert p.expect_final, p.name
        # Any discovery field can be answered, whether the persona has its own line or not — a
        # persona must never stall the run just because it lacks a line.
        for key in FULL_DISCOVERY | {"rego", "variant"}:
            assert p.answer_for_any(key, 0), (p.name, key)
        if p.expect_escalation:
            assert p.expect_final == frozenset({"HUMAN"}), p.name


def test_answer_variants_advance_then_hold():
    p = BY_NAME["vague"]
    first, second, third = (p.answer_for_any("odometer_km", n) for n in range(3))
    assert first != second and second == third  # the last variant repeats rather than running out


def test_prices_cover_the_models_we_configure():
    from acqbot.config import Settings

    cfg = Settings()
    for model in (cfg.extraction_model, cfg.conversation_model):
        assert price_for(model, 1_000_000, 0) > 0, model
    assert price_for("some-other-model", 1_000_000, 1_000_000) == 0.0
    assert price_for("claude-haiku-4-5-20251001", 1_000_000, 1_000_000) == pytest.approx(6.0)


def test_a_review_run_scores_and_renders(settings_env):
    settings_env(llm_provider="fake")
    review = run_review(
        [BY_NAME["plain"], BY_NAME["not-the-owner"]], seed=42, model_label="fake", auto_offer=True
    )
    plain, not_owner = review.conversations
    assert plain.final_state == "HANDOFF" and not plain.problems
    assert plain.questions_asked >= 5 and plain.calls > 0
    assert not plain.missing_facts
    assert "minor_or_no_authority" in not_owner.escalations and not not_owner.problems

    md = render_markdown(review)
    assert "# Prompt review" in md and "plain" in md and "not-the-owner" in md
    assert "Transcripts" in md and "automated assistant" in md  # the disclosure is in the transcript


def test_expectation_failures_are_reported_in_plain_english(settings_env):
    settings_env(llm_provider="fake")
    impossible = Persona(
        name="impossible",
        why="Says nothing useful, so discovery cannot finish.",
        answers={},
        opener="hello",
        expect_facts=frozenset({"odometer_km"}),
        expect_final=frozenset({"HANDOFF"}),
        max_turns=6,
    )
    # The fallback answers carry most fields, but the odometer has none, so it is never collected.
    impossible = Persona(**{**impossible.__dict__, "answers": {"odometer_km": ("dunno mate",)}})
    review = run_review([impossible], seed=42, model_label="fake")
    (c,) = review.conversations
    assert c.problems and not c.ok
    assert any("odometer_km" in p or "expected" in p for p in c.problems)
    assert "did not do what they should" in render_markdown(review)


def test_a_run_stops_when_every_model_call_fails(settings_env, monkeypatch):
    """Credits running out mid-run should end the run, not produce fourteen empty conversations."""
    from acqbot.llm.client import ModelError
    from acqbot.llm.fake import ScriptedFakeClient
    from acqbot.llm.registry import set_model_client

    settings_env(llm_provider="fake")

    class Dead:
        name = "fake"

        def complete(self, req):
            raise ModelError("Your credit balance is too low to access the Anthropic API.", retryable=False)

    set_model_client(Dead())
    try:
        review = run_review([BY_NAME["plain"], BY_NAME["terse"]], seed=42, model_label="claude-test")
    finally:
        set_model_client(None)
    assert review.aborted and "every model call failed" in review.aborted
    assert len(review.conversations) == 1  # stopped rather than running the second seller
    assert "Run did not finish" in render_markdown(review)
    assert isinstance(ScriptedFakeClient, type)


def test_probe_explains_an_empty_balance_in_words_you_can_act_on():
    from acqbot.llm.client import ModelError
    from acqbot.llm.registry import probe

    class Broke:
        name = "anthropic"

        def complete(self, req):
            raise ModelError(
                "Error code: 400 - {'message': 'Your credit balance is too low to access the "
                "Anthropic API. Please go to Plans & Billing to upgrade or purchase credits.'}",
                retryable=False,
            )

    ok, detail = probe(Broke(), "claude-haiku-4-5-20251001")
    assert not ok and "no credits" in detail and "Plans & Billing" in detail
