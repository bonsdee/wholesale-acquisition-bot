"""What the conversation model is allowed to say about process.

Everything here is a placeholder until the business confirms it (Appendix A.1). The model may
only answer process questions from this text; anything not covered is deferred to the named
agent. Keep it short — it is in every prompt — and version it with PROMPT_VERSION when it changes.
"""

from __future__ import annotations

PROCESS_NOTES = """\
- Any offer is subject to an inspection, which {agent} arranges once a price is agreed.
- Payment, transfer paperwork and pick-up are handled by {agent} after the inspection.
- The seller does not need to do anything to the car before the inspection.
- A person from {dealership} is available at any point if the seller asks.
- Photos are used to confirm the car's condition and the odometer reading; nothing else.
- The dealership runs a PPSR (finance and write-off) check and a registration check on every car.
If the seller asks about anything else — timing, price, how much the car is worth, what the
dealership does with cars, pick-up location — say {agent} will confirm it, and carry on."""


def process_notes(*, agent: str, dealership: str) -> str:
    return PROCESS_NOTES.format(agent=agent, dealership=dealership)
