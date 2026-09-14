import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from acqbot.transport import messenger, sms
from acqbot.transport.protocol import ThreadInfo, TransportError, within_window

PAGE_EVENT = {
    "object": "page",
    "entry": [
        {
            "id": "page1",
            "messaging": [
                {
                    "sender": {"id": "psid-1"},
                    "recipient": {"id": "page1"},
                    "timestamp": 1_757_800_000_000,
                    "message": {
                        "mid": "m.1",
                        "text": "Hi, saw you're keen on my car",
                        "referral": {"ref": "0b0e2b8c-1111-2222-3333-444455556666", "source": "SHORTLINK"},
                        "attachments": [{"type": "image", "payload": {"url": "https://cdn/1.jpg"}}],
                    },
                },
                {
                    "sender": {"id": "page1"},
                    "recipient": {"id": "psid-1"},
                    "message": {"mid": "m.2", "text": "echo", "is_echo": True},
                },
                {
                    "sender": {"id": "psid-2"},
                    "referral": {"ref": "lead-x", "source": "SHORTLINK", "type": "OPEN_THREAD"},
                },
            ],
        }
    ],
}


def test_parse_webhook_extracts_text_attachments_and_referrals():
    msgs = messenger.parse_webhook(PAGE_EVENT)
    assert len(msgs) == 2  # the echo is dropped
    first, second = msgs
    assert first.external_id == "psid-1" and first.external_msg_id == "m.1"
    assert first.referral_ref == "0b0e2b8c-1111-2222-3333-444455556666"
    assert first.attachments == [{"type": "image", "url": "https://cdn/1.jpg"}]
    assert first.received_at == datetime.fromtimestamp(1_757_800_000, tz=UTC)
    assert second.external_id == "psid-2" and second.body == "" and second.referral_ref == "lead-x"
    assert messenger.parse_webhook({"object": "user"}) == []


def test_signature_and_subscription_verification():
    body = json.dumps(PAGE_EVENT).encode()
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert messenger.verify_signature("secret", body, sig)
    assert not messenger.verify_signature("secret", body, "sha256=deadbeef")
    assert not messenger.verify_signature("secret", body, None)
    assert messenger.verify_subscription("subscribe", "tok", "12345", "tok") == "12345"
    with pytest.raises(TransportError):
        messenger.verify_subscription("subscribe", "wrong", "12345", "tok")


def test_send_uses_graph_api_and_respects_window():
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, json={"recipient_id": "psid-1", "message_id": "m.out.1"})

    t = messenger.MessengerTransport("PAGE_TOKEN", http=httpx.Client(transport=httpx.MockTransport(handler)))
    open_thread = ThreadInfo("psid-1", datetime.now(UTC) - timedelta(hours=1))
    receipt = t.send(open_thread, "hello")
    assert receipt.external_msg_id == "m.out.1"
    sent = json.loads(calls[0].content)
    assert sent["recipient"] == {"id": "psid-1"} and sent["message"]["text"] == "hello"
    assert "access_token=PAGE_TOKEN" in str(calls[0].url)

    closed = ThreadInfo("psid-1", datetime.now(UTC) - timedelta(hours=25))
    assert not t.send_window_open(closed)
    with pytest.raises(TransportError) as exc:
        t.send(closed, "hello")
    assert not exc.value.retryable
    assert t.poll(datetime.now(UTC)) == []
    assert t.max_body_length == 2000


def test_send_errors_are_classified():
    def server_error(request):
        return httpx.Response(503, text="down")

    def client_error(request):
        return httpx.Response(400, json={"error": {"message": "bad recipient"}})

    thread = ThreadInfo("psid-1", datetime.now(UTC))
    t = messenger.MessengerTransport("tok", http=httpx.Client(transport=httpx.MockTransport(server_error)))
    with pytest.raises(TransportError) as exc:
        t.send(thread, "x")
    assert exc.value.retryable
    t = messenger.MessengerTransport("tok", http=httpx.Client(transport=httpx.MockTransport(client_error)))
    with pytest.raises(TransportError) as exc:
        t.send(thread, "x")
    assert not exc.value.retryable


def test_within_window():
    assert within_window(datetime.now(UTC) - timedelta(hours=23))
    assert not within_window(datetime.now(UTC) - timedelta(hours=25))
    assert not within_window(None)


def test_twilio_webhook_parse_and_send():
    msg = sms.parse_twilio_webhook(
        {
            "From": "+61412345678",
            "Body": "hi",
            "MessageSid": "SM1",
            "NumMedia": "1",
            "MediaUrl0": "https://m/1",
            "MediaContentType0": "image/jpeg",
        }
    )
    assert msg.external_id == "+61412345678" and msg.attachments == [{"type": "image", "url": "https://m/1"}]
    assert sms.parse_twilio_webhook({"Body": "no sender"}) is None

    def handler(request: httpx.Request) -> httpx.Response:
        assert "Accounts/AC1/Messages.json" in str(request.url)
        assert b"To=%2B61412345678" in request.content
        return httpx.Response(201, json={"sid": "SM9"})

    t = sms.TwilioSmsTransport(
        "AC1", "tok", "+61400000000", http=httpx.Client(transport=httpx.MockTransport(handler))
    )
    assert t.send(ThreadInfo("+61412345678", None), "hello").external_msg_id == "SM9"
    assert t.send_window_open(ThreadInfo("+61412345678", None))
