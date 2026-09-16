"""HTML for the human console, as plain functions.

No template engine and no build step on purpose: this is one back-office screen on a Windows
machine managed by uv, and a Node toolchain or a template directory would cost more than it
returns. Everything that reaches the page goes through `esc()`; there is no other way to write a
value into the markup, which is the property that makes hand-built HTML safe.
"""

from __future__ import annotations

import html
from datetime import UTC, datetime
from typing import Any

# What each queue reason means to the person reading it, and what they are expected to do.
# The raw reason is a system word; a sales floor needs the sentence.
REASONS: dict[str, tuple[str, str]] = {
    "verification_review": ("Confirm the odometer", "Read it off the dash photo and record it."),
    "offer_presentation": ("Ready to price", "The valuation is done. Present an offer."),
    "offer_response_needed": ("Seller countered", "Decide the reply — concede, hold, or hand back."),
    "handoff": ("Deal agreed", "Book the inspection and confirm the details."),
    "offer_expired": (
        "Offer lapsed",
        "The 48 hours are up and the seller has been told. Re-offer or let it go.",
    ),
    "send_window_closed": ("Cannot message", "The 24-hour Messenger window has closed."),
    "ceiling_approval": (
        "Ceiling needs your approval",
        "The automated steps are spent and the seller is still within the ceiling. Approve it or hold.",
    ),
    "above_authorised_ladder": ("Wants more than the ceiling", "Only a person can go above the ladder."),
    "handoff_sla_expired": ("Deal packet went unclaimed", "Nobody picked it up in time. Assign it now."),
    "legal": ("Legal threat", "Do not reply automatically. Read the message first."),
    "deceased_estate": ("Deceased estate", "Needs a person, carefully."),
    "distress": ("Seller in distress", "Needs a person, carefully."),
    "minor_or_no_authority": ("Not the owner", "The seller may be under 18 or selling someone else's car."),
    "hostile": ("Hostile seller", "Read it and decide whether to continue."),
    "human_requested": ("Asked for a person", "They were told they are talking to an assistant."),
    "no_progression": ("Conversation stalled", "Three turns without progress."),
    "stalled_no_reply": (
        "Gone quiet",
        "Nudged and heard nothing back. Archive it, or try them another way.",
    ),
    "high_value": ("High-value vehicle", "Above the escalation threshold — check before proceeding."),
    "encumbered_above_offer": ("Finance exceeds the offer", "Settlement would cost more than the car."),
    "template_failed_gate": ("System fault", "A scripted message failed its own validation gate."),
    "transport_rejected": ("Send failed", "The channel refused the message."),
}


def reason_text(reason: str) -> tuple[str, str]:
    if reason.startswith("model_flagged:"):
        inner = reason.split(":", 1)[1]
        title, _ = REASONS.get(inner, (inner.replace("_", " "), ""))
        return (title, "The model asked for a person rather than sending a message.")
    return REASONS.get(reason, (reason.replace("_", " "), ""))


# The order a person walks up the ladder, not the order JSONB happens to return.
LADDER_ORDER = ("opening", "step_1", "step_2", "floor")
LADDER_LABEL = {
    "opening": "opening",
    "step_1": "first concession",
    "step_2": "second concession",
    "floor": "ceiling",
}


def fact_value(key: str, rendered: str | None, raw: Any) -> str:
    """One value cell in the fact table, in words a person would use.

    `_render_fact` writes prose for the fields it knows and falls back to "key: value" for the rest,
    which doubles the label column. Everything past that is raw storage — a Python bool, a list
    repr, an unformatted dollar figure — and none of it belongs on a screen someone reads between
    phone calls."""
    prefix = key.replace("_", " ") + ":"
    if rendered and not rendered.lower().startswith(prefix.lower()):
        return rendered  # prose written for this field
    if isinstance(raw, bool):
        return "yes" if raw else "no"
    if isinstance(raw, (list, tuple)):
        return ", ".join(str(x) for x in raw) if raw else "none"
    if key.endswith("_aud"):
        return money(raw)
    return str(raw)


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def money(amount: Any) -> str:
    try:
        return f"${float(amount):,.0f}"
    except (TypeError, ValueError):
        return "—"


def ago(when: datetime | None) -> str:
    if when is None:
        return "—"
    delta = datetime.now(UTC) - (when if when.tzinfo else when.replace(tzinfo=UTC))
    mins = int(delta.total_seconds() // 60)
    if mins < 1:
        return "just now"
    if mins < 60:
        return f"{mins}m ago"
    hours = mins // 60
    if hours < 24:
        return f"{hours}h {mins % 60}m ago"
    return f"{hours // 24}d ago"


def hours_since(when: datetime | None) -> float:
    if when is None:
        return 0.0
    delta = datetime.now(UTC) - (when if when.tzinfo else when.replace(tzinfo=UTC))
    return delta.total_seconds() / 3600


STYLE = """
:root {
  --bg:#0f1216; --panel:#171b21; --line:#242c36; --dim:#79828e; --text:#e8eaed;
  --soft:#9aa4b0; --accent:#d1603d; --ok:#6a9a78; --warn:#c9a227; --bad:#c05c4e;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--text); font:15px/1.5 "IBM Plex Sans",system-ui,sans-serif; }
a { color:var(--accent); text-decoration:none; } a:hover { text-decoration:underline; }
header { border-bottom:1px solid var(--line); padding:14px 24px; display:flex; gap:20px; align-items:baseline;
         position:sticky; top:0; background:var(--bg); z-index:5; }
header h1 { font-size:16px; margin:0; letter-spacing:-.01em; }
header nav { display:flex; gap:16px; font-size:14px; } header .spacer { flex:1; }
main { padding:24px; max-width:1100px; margin:0 auto; }
h2 { font-size:14px; letter-spacing:.14em; text-transform:uppercase; color:var(--dim); margin:28px 0 12px; font-weight:600; }
h2:first-child { margin-top:0; }
.card { background:var(--panel); border:1px solid var(--line); border-left:2px solid var(--line); padding:14px 18px; margin-bottom:10px; }
.card.now { border-left-color:var(--bad); } .card.soon { border-left-color:var(--warn); }
.card.stopped { border-left-color:var(--accent); }
.row { display:flex; gap:16px; align-items:baseline; flex-wrap:wrap; }
.grow { flex:1; min-width:200px; }
.title { font-weight:600; } .sub { color:var(--soft); font-size:14px; }
.meta { color:var(--dim); font-size:13px; font-family:"IBM Plex Mono",ui-monospace,monospace; white-space:nowrap; }
.tag { font-size:11px; letter-spacing:.1em; text-transform:uppercase; padding:3px 8px; border:1px solid var(--line);
       color:var(--dim); white-space:nowrap; }
.tag.stopped { color:var(--accent); border-color:var(--accent); }
.tag.running { color:var(--ok); border-color:var(--ok); }
.tag.late { color:var(--bad); border-color:var(--bad); }
table { width:100%; border-collapse:collapse; font-size:14px; }
td,th { text-align:left; padding:7px 10px 7px 0; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--dim); font-weight:500; font-size:13px; }
.msg { margin:0 0 10px; padding:10px 14px; border:1px solid var(--line); background:#13171c; }
.msg.out { border-left:2px solid var(--ok); } .msg.in { border-left:2px solid var(--dim); }
.msg .who { font-size:12px; color:var(--dim); margin-bottom:4px; display:flex; gap:10px; }
.msg p { margin:0; white-space:pre-wrap; }
form.inline { display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:0; }
input,select,button,textarea { font:inherit; background:#0d1014; color:var(--text); border:1px solid var(--line);
       padding:7px 10px; border-radius:0; }
input:focus,select:focus,textarea:focus { outline:1px solid var(--accent); }
button { background:var(--accent); border-color:var(--accent); color:#11140f; font-weight:600; cursor:pointer; }
button.ghost { background:transparent; color:var(--soft); border-color:var(--line); font-weight:400; }
button:hover { filter:brightness(1.08); }
label { color:var(--soft); font-size:14px; display:inline-flex; gap:6px; align-items:center; }
.empty { color:var(--dim); padding:18px 0; }
.flash { border:1px solid var(--ok); color:var(--ok); padding:10px 14px; margin-bottom:16px; background:#131a15; }
.flash.bad { border-color:var(--bad); color:var(--bad); background:#1a1413; }
.ladder { display:flex; gap:10px; flex-wrap:wrap; }
.ladder div { border:1px solid var(--line); padding:8px 12px; font-family:"IBM Plex Mono",monospace; font-size:13px; }
.ladder div b { display:block; font-size:16px; color:var(--text); font-family:"IBM Plex Sans",sans-serif; }
/* The ceiling is not a step: nothing automated ever offers it, so it must not look like one. */
.ladder div.ceiling { border-style:dashed; color:var(--dim); }
.ladder div.ceiling b { color:var(--dim); }
.warnbox { border:1px solid var(--warn); color:var(--warn); padding:10px 14px; margin-bottom:12px; background:#1a1710; }
.login { max-width:380px; margin:16vh auto; }
footer { color:var(--dim); font-size:13px; padding:28px 24px; max-width:1100px; margin:0 auto; border-top:1px solid var(--line); }
"""


def page(title: str, body: str, *, nav: bool = True, flash: tuple[str, str] | None = None) -> str:
    bar = ""
    if nav:
        bar = (
            "<header><h1>Acquisition console</h1>"
            '<nav><a href="/console">Queue</a><a href="/console/handoffs">Handoffs</a>'
            '<a href="/console/threads">Unlinked</a></nav><span class="spacer"></span>'
            '<form class="inline" method="post" action="/console/logout">'
            '<button class="ghost" type="submit">Sign out</button></form></header>'
        )
    note = ""
    if flash:
        kind, text = flash
        note = f'<div class="flash{" bad" if kind == "bad" else ""}">{esc(text)}</div>'
    return (
        "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width,initial-scale=1'>"
        "<meta name='robots' content='noindex'>"
        f"<title>{esc(title)}</title><style>{STYLE}</style></head><body>"
        f"{bar}<main>{note}{body}</main>"
        "<footer>Every figure here comes from the valuation engine. Offers above the ceiling are a "
        "human decision and are recorded as one.</footer></body></html>"
    )


def login_page(error: str | None = None) -> str:
    err = f'<div class="flash bad">{esc(error)}</div>' if error else ""
    return page(
        "Sign in",
        f"""<div class="login"><h2>Acquisition console</h2>{err}
        <form method="post" action="/console/login">
          <p class="sub">Enter the admin token.</p>
          <input type="password" name="token" autofocus style="width:100%" autocomplete="current-password">
          <p><button type="submit">Sign in</button></p>
        </form></div>""",
        nav=False,
    )
