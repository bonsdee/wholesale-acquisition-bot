"""Console transport — in-memory channel for local testing and the scripted demo."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from acqbot.models import Channel
from acqbot.transport.protocol import InboundMessage, MessageReceipt, ThreadInfo


class ConsoleTransport:
    channel = Channel.CONSOLE

    def __init__(self, *, echo: bool = False) -> None:
        self.echo = echo
        self.outbox: list[tuple[str, str]] = []  # (external_id, body)
        self._inbox: list[InboundMessage] = []

    # --- Transport protocol ---

    def send(self, thread: ThreadInfo, body: str) -> MessageReceipt:
        self.outbox.append((thread.external_id, body))
        if self.echo:
            print(f"\n[bot → {thread.external_id}]\n{body}\n")
        return MessageReceipt(external_msg_id=f"console-{uuid.uuid4()}", sent_at=datetime.now(UTC))

    def poll(self, since: datetime) -> list[InboundMessage]:
        msgs = [m for m in self._inbox if m.received_at >= since]
        self._inbox = [m for m in self._inbox if m.received_at < since]
        return msgs

    def send_window_open(self, thread: ThreadInfo) -> bool:
        return True

    @property
    def max_body_length(self) -> int:
        return 2000

    # --- test / CLI helpers ---

    def inject(
        self,
        external_id: str,
        body: str,
        *,
        referral_ref: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> InboundMessage:
        msg = InboundMessage(
            channel=self.channel,
            external_id=external_id,
            body=body,
            external_msg_id=f"console-in-{uuid.uuid4()}",
            attachments=attachments or [],
            referral_ref=referral_ref,
        )
        self._inbox.append(msg)
        return msg

    def last_sent(self, external_id: str | None = None) -> str | None:
        for ext, body in reversed(self.outbox):
            if external_id is None or ext == external_id:
                return body
        return None
