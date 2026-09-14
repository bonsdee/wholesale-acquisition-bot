"""SMS transport (Stage 3 of Section 4.2) — Twilio-shaped. MessageMedia can implement the same class.

Not exercised end to end in Phase 3 (needs an Australian number and credentials). Sender
identification for Australian SMS is satisfied by the disclosure in the first message (Section 8).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

from acqbot.models import Channel
from acqbot.transport.protocol import InboundMessage, MessageReceipt, ThreadInfo, TransportError

SMS_MAX_LENGTH = 1000  # keep well under Twilio's 1600-char concatenation limit


class TwilioSmsTransport:
    channel = Channel.SMS

    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        *,
        http: httpx.Client | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._sid = account_sid
        self._auth = (account_sid, auth_token)
        self._from = from_number
        self._http = http or httpx.Client(timeout=timeout)

    @property
    def _url(self) -> str:
        return f"https://api.twilio.com/2010-04-01/Accounts/{self._sid}/Messages.json"

    def send(self, thread: ThreadInfo, body: str) -> MessageReceipt:
        try:
            r = self._http.post(
                self._url,
                auth=self._auth,
                data={"From": self._from, "To": thread.external_id, "Body": body[:SMS_MAX_LENGTH]},
            )
        except httpx.HTTPError as exc:
            raise TransportError(f"sms send failed: {exc}") from exc
        if r.status_code >= 500:
            raise TransportError(f"sms send failed: {r.status_code}")
        if r.status_code >= 400:
            raise TransportError(f"sms send rejected: {r.status_code} {r.text[:200]}", retryable=False)
        data = r.json()
        return MessageReceipt(external_msg_id=data.get("sid"), sent_at=datetime.now(UTC), raw=data)

    def poll(self, since: datetime) -> list[InboundMessage]:
        return []  # webhook-driven

    def send_window_open(self, thread: ThreadInfo) -> bool:
        return True  # no platform window; courtesy hours are a nudge-policy concern (Phase 6)

    @property
    def max_body_length(self) -> int:
        return SMS_MAX_LENGTH


def parse_twilio_webhook(form: dict[str, Any]) -> InboundMessage | None:
    sender, body = form.get("From"), form.get("Body", "")
    if not sender:
        return None
    n = int(form.get("NumMedia", 0) or 0)
    attachments = [
        {"type": "image", "url": form.get(f"MediaUrl{i}")}
        for i in range(n)
        if str(form.get(f"MediaContentType{i}", "")).startswith("image")
    ]
    return InboundMessage(
        channel=Channel.SMS,
        external_id=str(sender),
        body=str(body),
        external_msg_id=form.get("MessageSid"),
        attachments=attachments,
        received_at=datetime.now(UTC),
        raw=dict(form),
    )
