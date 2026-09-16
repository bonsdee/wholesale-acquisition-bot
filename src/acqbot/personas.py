"""Seller personas for the review harness — the awkward sellers, not the cooperative one.

`demo.py` proves the pipeline with a seller who answers exactly what was asked. That is the wrong
seller to tune prompts on. These are the ones that break things: the seller who answers three
questions at once, the one who answers none, the one who asks a question every turn, the one who
types like a human on a phone. Each persona declares what the system is supposed to end up with,
so a run is scored rather than read.

Adding a persona is the cheapest way to lock in a bug you have just fixed.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Persona:
    name: str
    why: str  # what this persona is testing — printed in the report
    # field key → answers to cycle through. {odometer} {rego} {variant} are filled from the lead.
    answers: dict[str, tuple[str, ...]] = field(default_factory=dict)
    opener: str = "Hi, saw you're keen on my car"
    # Sent INSTEAD of answering, on these inbound turn numbers (1-based, counting the opener as 1).
    interjections: tuple[tuple[int, str], ...] = ()
    offer_script: tuple[str, ...] = ("Yes, deal",)
    # --- what should be true at the end ---
    expect_facts: frozenset[str] = frozenset()
    expect_templates: frozenset[str] = frozenset()  # template ids that must appear at least once
    expect_escalation: str | None = None
    expect_final: frozenset[str] = frozenset({"HANDOFF"})
    # Questions the bot should need to finish discovery. A seller who answers four fields at once
    # should be asked fewer; if the count blows out, the extractor missed what it was given.
    expect_max_questions: int | None = None
    # True when the rule-based extractor cannot handle this seller and only a real model can.
    # `--model fake` reports these as expected failures rather than noise.
    needs_model: bool = False
    max_turns: int = 40

    def answer_for_any(self, field_key: str, nth: int) -> str:
        """The persona's line for this field, or a plain fallback so a run never stalls."""
        return self.answer_for(field_key, nth) or FALLBACK_ANSWERS.get(field_key, "Not sure on that one")

    def answer_for(self, field_key: str, nth: int) -> str | None:
        variants = self.answers.get(field_key)
        if not variants:
            return None
        return variants[min(nth, len(variants) - 1)]


# Used when a persona has no line for a field it is asked — usually because it thought it had
# already answered it in a multi-field message. Keeps the conversation moving so the report shows
# how MANY questions it took rather than just stalling.
FALLBACK_ANSWERS: dict[str, str] = {
    "service_history": "Full logbook",
    "finance_owing": "No finance",
    "write_off_status": "Never written off",
    "panel_paint_condition": "Good",
    "mechanical_faults": "None",
    "tyre_condition": "Good",
    "keys_count": "Two",
    "seller_phone": "0412 345 678",
    "rego_status": "Current until March next year",
}

# Fields every cooperative persona should end up having answered.
FULL_DISCOVERY = frozenset(
    {
        "odometer_km",
        "service_history",
        "finance_owing",
        "write_off_status",
        "panel_paint_condition",
        "mechanical_faults",
        "tyre_condition",
        "keys_count",
        "rego_status",
        "photos",
    }
)

# A plain, cooperative baseline — if this one is not clean, nothing else matters.
PLAIN = Persona(
    name="plain",
    why="Baseline. Straight answers, one field at a time. Anything wrong here is a prompt bug, not a seller.",
    answers={
        "variant": ("It's the {variant}",),
        "rego": ("Rego is {rego}",),
        "odometer_km": ("{odometer} km",),
        "service_history": ("Full logbook",),
        "finance_owing": ("No finance owing",),
        "write_off_status": ("Never been written off",),
        "panel_paint_condition": ("Good condition",),
        "mechanical_faults": ("None",),
        "tyre_condition": ("Tyres are good",),
        "keys_count": ("Two keys",),
        "rego_status": ("Registered until March next year",),
    },
    expect_facts=FULL_DISCOVERY,
)

TERSE = Persona(
    name="terse",
    why="Two-word answers with no units or context. The extractor has to read '84500' as kilometres.",
    opener="yeah?",
    answers={
        "variant": ("{variant}",),
        "rego": ("{rego}",),
        "odometer_km": ("{odometer}",),
        "service_history": ("full",),
        "finance_owing": ("nope",),
        "write_off_status": ("no",),
        "panel_paint_condition": ("good",),
        "mechanical_faults": ("none",),
        "tyre_condition": ("good",),
        "keys_count": ("2",),
        "rego_status": ("yep current",),
    },
    expect_facts=FULL_DISCOVERY,
)

CHATTY = Persona(
    name="chatty",
    why="The fact is buried in a paragraph of life story. Tests that the extractor finds it and the writer does not match the length.",
    opener="Hi there! Yes the car is still available, we're selling because we just had our second and the Tucson is getting tight with two car seats, my wife wants something bigger",
    answers={
        "variant": (
            "Honestly I'd have to check the papers but I'm fairly sure it's the {variant}, that's what the dealer told us",
        ),
        "rego": (
            "Sure, the rego is {rego} — it's on the plate obviously but I've also got the rego papers "
            "somewhere in the glovebox if you need the VIN as well, just let me know",
        ),
        "odometer_km": (
            "So I just went out to the garage and had a look, it's sitting on {odometer} km at the moment, "
            "though I do drive it to work most days so it creeps up a bit each week",
        ),
        "service_history": (
            "We've been really good with servicing, every single one done at the Hyundai dealer in Ringwood, "
            "full logbook with all the stamps, I've got the book right here",
        ),
        "finance_owing": ("No no, we paid that off about three years ago now, owned outright",),
        "write_off_status": ("Definitely not, never been in anything more than a car park scrape",),
        "panel_paint_condition": (
            "It's in good nick overall, there's a couple of small scratches on the rear bumper from a "
            "shopping trolley and a tiny stone chip on the bonnet but nothing you'd notice",
        ),
        "mechanical_faults": ("Runs beautifully, no lights on the dash, no noises, nothing at all",),
        "tyre_condition": ("Tyres are good, we put new ones on the front about a year ago",),
        "keys_count": ("Two keys, both work fine",),
        "rego_status": ("Current, registered through to March next year",),
    },
    expect_facts=FULL_DISCOVERY,
)

MULTI_ANSWER = Persona(
    name="multi-answer",
    why="Answers four fields in one message. The next question must skip what was already given.",
    needs_model=True,
    expect_max_questions=8,
    answers={
        "odometer_km": ("{odometer} km, full logbook, two keys, and there's no finance on it",),
        "write_off_status": ("Never written off, no hail damage either",),
        "panel_paint_condition": ("Good, and the tyres are good too",),
        "mechanical_faults": ("None at all",),
        "rego_status": ("Rego's current until March",),
        "rego": ("{rego}",),
        "variant": ("{variant}",),
    },
    expect_facts=FULL_DISCOVERY,
)

VAGUE = Persona(
    name="vague",
    why="Hedged, unusable answers. Should be recorded as low confidence and asked again, not guessed at.",
    needs_model=True,
    answers={
        "odometer_km": ("somewhere around 200 thousand ish, maybe a bit over", "{odometer} exactly"),
        "service_history": ("yeah it's been serviced I think, mostly", "full logbook"),
        "panel_paint_condition": ("it's alright I guess", "good"),
        "mechanical_faults": ("nothing major really", "none"),
        "tyre_condition": ("they're ok I think", "good"),
        "keys_count": ("a couple I think", "two"),
        "rego_status": ("should be current", "current until March next year"),
        "finance_owing": ("no I don't think so", "no finance"),
        "write_off_status": ("not that I know of", "never written off"),
        "rego": ("{rego}",),
        "variant": ("{variant}",),
    },
    expect_facts=FULL_DISCOVERY,
    expect_templates=frozenset({"clarify"}),
)

TYPOS = Persona(
    name="typos",
    why="Real phone typing. Nothing here should defeat the extractor.",
    needs_model=True,
    opener="hi yeh its still availbale",
    answers={
        "variant": ("{variant} i think",),
        "rego": ("rego is {rego}",),
        "odometer_km": ("{odometer}klm",),
        "service_history": ("fulll logbook all stamped",),
        "finance_owing": ("nah nothin owing on it",),
        "write_off_status": ("nope never writen off",),
        "panel_paint_condition": ("prety good just a scratch on the reer bumper",),
        "mechanical_faults": ("no faults runs grear",),
        "tyre_condition": ("tyres r good plenty of tred",),
        "keys_count": ("2 keys",),
        "rego_status": ("rego current til march",),
    },
    expect_facts=FULL_DISCOVERY,
)

QUESTIONER = Persona(
    name="questioner",
    why="Asks something every turn. The writer must answer from the process notes or defer, then still ask its question.",
    answers={
        "variant": ("{variant}. Why do you need to know the variant?",),
        "rego": ("{rego}. What do you do with the rego?",),
        "odometer_km": ("{odometer} km. Do you come and pick it up or do I have to drive it somewhere?",),
        "service_history": ("Full logbook. How long does this whole process take?",),
        "finance_owing": ("No finance. Do you do a credit check on me or something?",),
        "write_off_status": ("Never written off. What's a PPSR check?",),
        "panel_paint_condition": ("Good. Do I need to get it detailed before you look at it?",),
        "mechanical_faults": ("None. Would a service history gap matter?",),
        "tyre_condition": ("Good. Do you pay on the spot?",),
        "keys_count": ("Two. Is the inspection at my place?",),
        "rego_status": ("Current until March. Who actually does the inspection?",),
    },
    expect_facts=FULL_DISCOVERY,
)

PRICE_FIRST = Persona(
    name="price-first",
    why="Wants a number before answering anything. The bot must not produce one, and must not stall either.",
    opener="How much will you give me for it?",
    interjections=(
        (2, "Just give me a ballpark first"),
        (4, "Come on, roughly what are we talking? Ten grand?"),
    ),
    answers={
        "variant": ("{variant}",),
        "rego": ("{rego}",),
        "odometer_km": ("{odometer} km",),
        "service_history": ("Full",),
        "finance_owing": ("None",),
        "write_off_status": ("No",),
        "panel_paint_condition": ("Good",),
        "mechanical_faults": ("None",),
        "tyre_condition": ("Good",),
        "keys_count": ("Two",),
        "rego_status": ("Current till March",),
    },
    expect_facts=FULL_DISCOVERY,
)

DEFERRER = Persona(
    name="deferrer",
    why="Keeps saying they'll check later. The field must move to the back of the queue, not stall the conversation.",
    answers={
        "odometer_km": ("I'll have to go out and check the dash tonight", "{odometer} km"),
        "service_history": ("Need to dig out the logbook, give me a day", "Full logbook"),
        "rego": ("{rego}",),
        "variant": ("{variant}",),
        "finance_owing": ("None",),
        "write_off_status": ("No",),
        "panel_paint_condition": ("Good",),
        "mechanical_faults": ("None",),
        "tyre_condition": ("Good",),
        "keys_count": ("Two",),
        "rego_status": ("Current till March",),
    },
    expect_facts=FULL_DISCOVERY,
)

BAD_NEWS = Persona(
    name="bad-news",
    why="Finance owing, hail damage, warning light, bald tyres, one key. None of it should soften the questions or the packet.",
    answers={
        "variant": ("{variant}",),
        "rego": ("{rego}",),
        "odometer_km": ("{odometer} km",),
        "service_history": ("None, previous owner never kept the book",),
        "finance_owing": ("Yeah there's about $8,000 still owing to Westpac",),
        "write_off_status": ("It was a hail write-off back in 2021, repairable",),
        "panel_paint_condition": (
            "Poor honestly, dents along the passenger side and the paint's peeling on the roof",
        ),
        "mechanical_faults": ("Engine light's been on for a month and there's a rattle from the front",),
        "tyre_condition": ("They're bald, need replacing",),
        "keys_count": ("Just the one key",),
        "rego_status": ("Rego expired in June",),
    },
    expect_facts=FULL_DISCOVERY,
)

HOSTILE_ISH = Persona(
    name="impatient",
    why="Rude but not abusive. Must NOT escalate — a blunt seller is still a seller.",
    opener="took you long enough",
    answers={
        "variant": ("{variant}, obviously",),
        "rego": ("{rego}. get on with it",),
        "odometer_km": ("{odometer}. this is a lot of questions",),
        "service_history": ("full. are we nearly done",),
        "finance_owing": ("no",),
        "write_off_status": ("no",),
        "panel_paint_condition": ("good",),
        "mechanical_faults": ("none",),
        "tyre_condition": ("good",),
        "keys_count": ("two",),
        "rego_status": ("current",),
    },
    expect_facts=FULL_DISCOVERY,
)

NOT_THE_OWNER = Persona(
    name="not-the-owner",
    why="Selling someone else's car. Must reach a human and stop — this is Section 5.2, not a discovery problem.",
    opener="Hi, it's my mum's car but I'm handling the sale for her",
    answers={},
    expect_escalation="minor_or_no_authority",
    expect_final=frozenset({"HUMAN"}),
    max_turns=6,
)

BOT_QUESTION = Persona(
    name="bot-question",
    why="Section 8: a straight answer, then a person. The answer is scripted and must stay scripted.",
    opener="hang on, am I talking to a bot?",
    answers={},
    expect_escalation="human_requested",
    expect_final=frozenset({"HUMAN"}),
    max_turns=6,
)

NEGOTIATOR = Persona(
    name="negotiator",
    why="Answers cleanly, then pushes twice on the offer. Every figure must be on the ladder.",
    answers=dict(PLAIN.answers),
    offer_script=(
        "That's lower than I hoped, can you do better?",
        "Still a bit light, meet me in the middle?",
        "Alright, done",
    ),
    expect_facts=FULL_DISCOVERY,
)

QUOTE_BACK = Persona(
    name="quote-back",
    why="Repeats our own figure back while pushing. Must read as a counter, never as acceptance.",
    answers=dict(PLAIN.answers),
    offer_script=("${offer:,} is too low honestly", "Ok fine, deal"),
    expect_facts=FULL_DISCOVERY,
)

CEILING = Persona(
    name="ceiling",
    why=(
        "Counters past both authorised concessions. The only seller who reaches the end of the "
        "automated ladder, so the only one who tests what the bot says when it has nothing left to "
        "give: it must not claim finality early, promise more, or invite another counter."
    ),
    answers=dict(PLAIN.answers),
    offer_script=(
        "Too low mate",
        "Come on, you can do better than that",
        "Still not enough",
        "So what's your actual best number?",
    ),
    expect_facts=FULL_DISCOVERY,
    expect_escalation="ceiling_approval",
    expect_final=frozenset({"HUMAN"}),
)

ALL: tuple[Persona, ...] = (
    PLAIN,
    TERSE,
    CHATTY,
    MULTI_ANSWER,
    VAGUE,
    TYPOS,
    QUESTIONER,
    PRICE_FIRST,
    DEFERRER,
    BAD_NEWS,
    HOSTILE_ISH,
    NOT_THE_OWNER,
    BOT_QUESTION,
    NEGOTIATOR,
    QUOTE_BACK,
    CEILING,
)

BY_NAME: dict[str, Persona] = {p.name: p for p in ALL}
