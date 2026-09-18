"""Reading the bot the way a seller would.

The review harness already answers "did the machine work" — ten facts collected, right final
state, escalated when it should have. Every one of those can pass while the conversation reads
badly, and the gate will not catch it: the gate refuses what is *prohibited*, not what is merely
graceless. A bot that opens four messages in a row with "Thanks for that" has broken no rule.

These checks are the other half. They are deliberately mechanical — no judgement, no second model
marking the first one's homework — because a check that needs interpreting is a check that gets
argued with at 5pm.

They apply ONLY to messages the model wrote. Scripted templates are fixed wording by design, and
their repetition is the point; they are reviewed once, by a person, in the legal pack.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

# A model asked to ask about tyres will often ask about keys in the same breath. Section 5.1 wants
# one question at a time: a seller answering on a phone answers the last one and the first is lost.
MAX_QUESTIONS_PER_MESSAGE = 1

# Twice is a clarification. Three times means the question is not landing and something else is
# wrong — the answer is being misread, or the seller has declined and not been heard.
MAX_TIMES_TO_ASK_ONE_FIELD = 2

# An opening clause is a tic once it is the third one. Below that it is a house style.
MAX_REPEATS_OF_AN_OPENING = 2

# Three words, because that is the length of the habit: "Thanks for that", "No worries at", "How
# are the". Take five and the tic hides behind whatever varies at word four.
OPENING_WORDS = 3

# Two messages sharing a whole sentence, word for word, is copy-paste — noticeable to anyone
# reading their own thread back.
MIN_WORDS_FOR_A_SENTENCE = 6

# Messages growing as the conversation goes is the classic long-context drift. A 60% rise in mean
# length between the first third and the last third is well beyond ordinary variation.
LENGTH_DRIFT_RATIO = 1.6
LENGTH_DRIFT_FLOOR_CHARS = 160  # below this, a big ratio is just two short sentences


class TurnLike(Protocol):
    direction: str
    body: str
    generator: str | None
    asked_field: str | None


@dataclass
class Finding:
    check: str
    detail: str
    quote: str = ""  # the offending text, so the report can show rather than assert

    def __str__(self) -> str:
        return self.detail


def _model_written(turns: Sequence[TurnLike]) -> list[TurnLike]:
    return [t for t in turns if t.direction == "outbound" and t.generator == "model"]


def _sentences(body: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", body) if s.strip()]


def _normalise(text: str) -> str:
    """Lowercase, strip punctuation and collapse whitespace, so that "Thanks, that's great!" and
    "Thanks that's great" are recognised as the same sentence said twice."""
    return re.sub(r"[^a-z0-9' ]+", "", text.lower()).strip()


def _opening(body: str) -> str:
    """The first few words, normalised — enough to catch a habitual opener without flagging two
    messages that merely start with the same word. Returns "" for a message too short to have one."""
    words = _normalise(body).split()
    return " ".join(words[:OPENING_WORDS]) if len(words) >= OPENING_WORDS else ""


# ------------------------------------------------------------------ the checks


def repeated_wording(turns: Sequence[TurnLike]) -> list[Finding]:
    """The same opener, or the same whole sentence, more than once in one conversation."""
    out: list[Finding] = []
    written = _model_written(turns)

    openings: dict[str, int] = {}
    for t in written:
        key = _opening(t.body)
        if key:
            openings[key] = openings.get(key, 0) + 1
    for key, n in sorted(openings.items(), key=lambda kv: -kv[1]):
        if n > MAX_REPEATS_OF_AN_OPENING:
            out.append(
                Finding("repetition", f"opens {n} messages the same way", quote=f"“{key}…”")
            )

    seen: dict[str, int] = {}
    for t in written:
        for s in _sentences(t.body):
            norm = _normalise(s)
            if len(norm.split()) >= MIN_WORDS_FOR_A_SENTENCE:
                seen[norm] = seen.get(norm, 0) + 1
    for norm, n in sorted(seen.items(), key=lambda kv: -kv[1]):
        if n > 1:
            out.append(
                Finding("repetition", f"says the same sentence {n} times", quote=f"“{norm}”")
            )
    return out[:4]  # the top few; a wall of these tells you nothing more than three do


def stacked_questions(turns: Sequence[TurnLike]) -> list[Finding]:
    """More than one question in a single message.

    Not pedantry: a seller replying on a phone answers the last question asked and the earlier one
    is silently dropped, which then reads as the bot ignoring their answer when it asks again."""
    out: list[Finding] = []
    for t in _model_written(turns):
        # A rhetorical "?" inside a sentence is rare enough in this register to ignore the
        # distinction; count question marks that actually end a clause.
        n = len(re.findall(r"\?(?:\s|$)", t.body))
        if n > MAX_QUESTIONS_PER_MESSAGE:
            out.append(
                Finding("stacked questions", f"asks {n} questions in one message", quote=t.body)
            )
    return out


def repeated_asking(turns: Sequence[TurnLike]) -> list[Finding]:
    """The same field asked a third time. Something is not landing."""
    counts: dict[str, int] = {}
    for t in turns:
        if t.direction == "outbound" and t.asked_field:
            counts[t.asked_field] = counts.get(t.asked_field, 0) + 1
    return [
        Finding("re-asking", f"asked for {field} {n} times")
        for field, n in sorted(counts.items())
        if n > MAX_TIMES_TO_ASK_ONE_FIELD
    ]


def length_drift(turns: Sequence[TurnLike]) -> list[Finding]:
    """The model padding its questions more and more as the conversation goes on.

    The history grows every turn, and a model with more to read tends to write more. A seller who
    got two lines at the start and six by the end has watched the bot become harder work.

    Measured as INFLATION over the scripted wording, not raw length. The fields have prompts of
    very different lengths and are asked in a fixed order, so raw length rises through every
    healthy conversation; comparing a late question to an early one flags all of them and the
    finding stops meaning anything. What matters is how much longer the model's version is than
    the question it was handed — and whether that is growing."""
    ratios: list[float] = []
    for t in _model_written(turns):
        if not t.asked_field:
            continue
        spec = _spec_for(t.asked_field)
        if spec is None or not spec.ask:
            continue
        ratios.append(len(t.body) / len(spec.ask))
    if len(ratios) < 6:
        return []
    third = len(ratios) // 3
    a = sum(ratios[:third]) / third
    b = sum(ratios[-third:]) / third
    if a > 0 and b / a >= LENGTH_DRIFT_RATIO and b >= 1.5:
        return [
            Finding(
                "length drift",
                f"questions started {a:.1f}× the scripted wording and ended {b:.1f}× — "
                "the model is padding more as the history grows",
            )
        ]
    return []


def _spec_for(field_key: str):
    """The scripted question for a field, or None. Imported lazily so the checks can be unit-tested
    against made-up field names without dragging in the fact store."""
    try:
        from acqbot.facts.fields import spec_for
    except ImportError:  # pragma: no cover
        return None
    return spec_for(field_key)


CHECKS = (repeated_wording, stacked_questions, repeated_asking, length_drift)


def findings(turns: Sequence[TurnLike]) -> list[Finding]:
    """Every quality finding for one conversation, in the order the checks are declared."""
    out: list[Finding] = []
    for check in CHECKS:
        out.extend(check(turns))
    return out
