"""Messenger Platform transport (Stage 1 of Section 4.2).

Sanctioned Send API against a dealership Page. Sellers arrive via an `m.me/<page>?ref=<lead_id>`
link, so the referral webhook carries the lead id that links the thread. Outbound is limited to the
24-hour window after the seller's last message; `send_window_open` tells the orchestrator so.

Webhook payloads and Graph API calls are isolated here so the rest of the system never sees them.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Any

import httpx

from acqbot.models import Channel
from acqbot.transport.protocol import (
    InboundMessage,
    MessageReceipt,
    ThreadInfo,
    TransportError,
    within_window,
)

GRAPH_URL = "https://graph.facebook.com/v21.0/me/messages"
MESSENGER_MAX_LENGTH = 2000
MESSENGER_WINDOW_HOURS = 24


class MessengerTransport:
    channel = Channel.MESSENGER

    def __init__(
        self,
        page_access_token: str,
        *,
        http: httpx.Client | None = None,
        graph_url: str = GRAPH_URL,
        timeout: float = 15.0,
    ) -> None:
        self._token = page_access_token
        self._http = http or httpx.Client(timeout=timeout)
        self._url = graph_url

    def send(self, thread: ThreadInfo, body: str) -> MessageReceipt:
        if not self.send_window_open(thread):
            raise TransportError("messenger 24h window closed", retryable=False)
        payload = {
            "recipient": {"id": thread.external_id},
            "messaging_type": "RESPONSE",
            "message": {"text": body[:MESSENGER_MAX_LENGTH]},
        }
        try:
            r = self._http.post(self._url, params={"access_token": self._token}, json=payload)
        except httpx.HTTPError as exc:
            raise TransportError(f"messenger send failed: {exc}") from exc
        if r.status_code >= 500:
            raise TransportError(f"messenger send failed: {r.status_code} {r.text[:200]}")
        if r.status_code >= 400:
            raise TransportError(f"messenger send rejected: {r.status_code} {r.text[:200]}", retryable=False)
        data = r.json()
        return MessageReceipt(external_msg_id=data.get("message_id"), sent_at=datetime.now(UTC), raw=data)

    def poll(self, since: datetime) -> list[InboundMessage]:
        return []  # webhook-driven

    def send_window_open(self, thread: ThreadInfo) -> bool:
        return within_window(thread.last_inbound_at, MESSENGER_WINDOW_HOURS)

    @property
    def max_body_length(self) -> int:
        return MESSENGER_MAX_LENGTH


# --- webhook helpers -------------------------------------------------------------------------


def verify_subscription(
    mode: str | None, token: str | None, challenge: str | None, expected_token: str
) -> str:
    if mode == "subscribe" and token and hmac.compare_digest(token, expected_token) and challenge is not None:
        return challenge
    raise TransportError("webhook verification failed", retryable=False)


def verify_signature(app_secret: str, body: bytes, header_value: str | None) -> bool:
    """X-Hub-Signature-256: sha256=<hex hmac(app_secret, body)>"""
    if not header_value or not header_value.startswith("sha256="):
        return False
    expected = hmac.new(app_secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, header_value[len("sha256=") :])


def parse_webhook(payload: dict[str, Any]) -> list[InboundMessage]:
    """Turn a Messenger webhook payload into InboundMessages (messages, attachments, referrals)."""
    out: list[InboundMessage] = []
    if payload.get("object") != "page":
        return out
    for entry in payload.get("entry", []):
        for ev in entry.get("messaging", []):
            sender = (ev.get("sender") or {}).get("id")
            if not sender:
                continue
            ts = ev.get("timestamp")
            received = datetime.fromtimestamp(ts / 1000, tz=UTC) if ts else datetime.now(UTC)
            ref = None
            if "referral" in ev:  # messaging_referrals event (existing conversation, clicked m.me link)
                ref = ev["referral"].get("ref")
            elif "postback" in ev and ev["postback"].get("referral"):
                ref = ev["postback"]["referral"].get("ref")
            msg = ev.get("message")
            if msg:
                if msg.get("is_echo"):
                    continue  # our own outbound echoed back
                if msg.get("referral"):
                    ref = msg["referral"].get("ref") or ref
                attachments = [
                    {"type": a.get("type"), "url": (a.get("payload") or {}).get("url")}
                    for a in msg.get("attachments", [])
                    if (a.get("payload") or {}).get("url")
                ]
                out.append(
                    InboundMessage(
                        channel=Channel.MESSENGER,
                        external_id=sender,
                        body=msg.get("text") or "",
                        external_msg_id=msg.get("mid"),
                        attachments=attachments,
                        received_at=received,
                        referral_ref=ref,
                        raw=ev,
                    )
                )
            elif ref or "postback" in ev:
                # A referral or Get Started postback with no text still opens the thread.
                out.append(
                    InboundMessage(
                        channel=Channel.MESSENGER,
                        external_id=sender,
                        body="",
                        external_msg_id=None,
                        received_at=received,
                        referral_ref=ref,
                        raw=ev,
                    )
                )
    return out
