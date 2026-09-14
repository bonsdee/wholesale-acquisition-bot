"""Transport abstraction — Section 4.3.

The orchestrator must not know which channel carried a message. Every channel implements this
narrow interface; the conversation layer sees InboundMessage in and MessageReceipt out.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from acqbot.models import Channel


@dataclass
class InboundMessage:
    channel: Channel
    external_id: str  # who sent it: PSID / E.164 phone / console id
    body: str
    external_msg_id: str | None = None
    attachments: list[dict[str, Any]] = field(default_factory=list)  # [{"type": "image", "url": "..."}]
    received_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    referral_ref: str | None = None  # the ?ref= value from an m.me link
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class MessageReceipt:
    external_msg_id: str | None
    sent_at: datetime
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class ThreadInfo:
    external_id: str
    last_inbound_at: datetime | None


class TransportError(Exception):
    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class Transport(Protocol):
    channel: Channel

    def send(self, thread: ThreadInfo, body: str) -> MessageReceipt: ...

    def poll(self, since: datetime) -> list[InboundMessage]:
        """Pull-based channels return what arrived since `since`; webhook channels return []."""
        ...

    def send_window_open(self, thread: ThreadInfo) -> bool:
        """False when the channel forbids unsolicited outbound (e.g. Messenger 24h window elapsed)."""
        ...

    @property
    def max_body_length(self) -> int: ...


def within_window(last_inbound_at: datetime | None, hours: int = 24) -> bool:
    if last_inbound_at is None:
        return False
    return datetime.now(UTC) - last_inbound_at < timedelta(hours=hours)
