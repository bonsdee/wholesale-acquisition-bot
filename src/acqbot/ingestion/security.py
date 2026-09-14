"""Webhook signing for POST /leads.

Header:  X-Acqbot-Signature: t=<unix seconds>,v1=<hex hmac-sha256(secret, "<t>.<raw body>")>

The timestamp is part of the signed material so a captured request cannot be replayed outside
the tolerance window. The same helper signs outbound requests from the lead simulator.
"""

from __future__ import annotations

import hashlib
import hmac
import time

SIGNATURE_HEADER = "X-Acqbot-Signature"


class SignatureError(ValueError):
    pass


def sign(secret: str, body: bytes, timestamp: int | None = None) -> str:
    ts = int(timestamp if timestamp is not None else time.time())
    mac = hmac.new(secret.encode("utf-8"), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


def verify(secret: str, body: bytes, header_value: str | None, *, tolerance_seconds: int = 300) -> None:
    """Raise SignatureError unless the header is a valid, fresh signature over body."""
    if not header_value:
        raise SignatureError("missing signature header")
    parts = dict(p.split("=", 1) for p in header_value.split(",") if "=" in p)
    try:
        ts = int(parts["t"])
        provided = parts["v1"]
    except (KeyError, ValueError) as exc:
        raise SignatureError("malformed signature header") from exc
    if abs(time.time() - ts) > tolerance_seconds:
        raise SignatureError("signature timestamp outside tolerance")
    expected = hmac.new(secret.encode("utf-8"), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, provided):
        raise SignatureError("signature mismatch")
