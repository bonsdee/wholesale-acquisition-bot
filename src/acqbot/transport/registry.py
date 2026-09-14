"""Build transports from configuration. The console transport is a process-wide singleton so the
CLI and tests can inject seller messages and read what was sent."""

from __future__ import annotations

from functools import lru_cache

from acqbot.config import get_settings
from acqbot.models import Channel
from acqbot.transport.console import ConsoleTransport
from acqbot.transport.messenger import MessengerTransport
from acqbot.transport.protocol import Transport
from acqbot.transport.sms import TwilioSmsTransport

_console: ConsoleTransport | None = None


def console_transport() -> ConsoleTransport:
    global _console
    if _console is None:
        _console = ConsoleTransport()
    return _console


def reset_console_transport(*, echo: bool = False) -> ConsoleTransport:
    global _console
    _console = ConsoleTransport(echo=echo)
    return _console


@lru_cache
def _messenger() -> MessengerTransport:
    s = get_settings()
    if not s.messenger_page_access_token:
        raise RuntimeError("ACQBOT_MESSENGER_PAGE_ACCESS_TOKEN is not set")
    return MessengerTransport(s.messenger_page_access_token)


@lru_cache
def _sms() -> TwilioSmsTransport:
    s = get_settings()
    if not (s.twilio_account_sid and s.twilio_auth_token and s.twilio_from_number):
        raise RuntimeError("Twilio settings are not set")
    return TwilioSmsTransport(s.twilio_account_sid, s.twilio_auth_token, s.twilio_from_number)


def get_transport(channel: Channel) -> Transport:
    if channel == Channel.CONSOLE:
        return console_transport()
    if channel == Channel.MESSENGER:
        return _messenger()
    if channel == Channel.SMS:
        return _sms()
    raise ValueError(f"unknown channel {channel}")


def reset_transport_caches() -> None:
    _messenger.cache_clear()
    _sms.cache_clear()
