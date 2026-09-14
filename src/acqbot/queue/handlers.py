"""Import every module that registers a job handler. The worker imports this once at startup."""

from __future__ import annotations

import acqbot.conversation.service  # noqa: F401  (registers maybe_send_opening, on_priced, expire_offer)
import acqbot.enrichment.pipeline  # noqa: F401  (registers enrich_lead)
import acqbot.valuation.service  # noqa: F401  (registers value_lead)

__all__: list[str] = []
