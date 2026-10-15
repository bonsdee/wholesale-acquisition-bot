"""Business parameters a person can change from the console without a deploy.

The .env value is the default; the latest console change, if any, overrides it. Changes only affect
leads screened and valuations computed afterwards — an offer already made keeps its own figures,
and every valuation records the margin it used in its inputs snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from acqbot.config import Settings, get_settings
from acqbot.models import BusinessSetting


@dataclass(frozen=True)
class Spec:
    key: str
    label: str
    minimum: float
    maximum: float
    step: float


THRESHOLD = Spec("high_value_threshold_aud", "High-value threshold", 5_000, 500_000, 1_000)
MARGIN = Spec("target_margin_pct", "Target margin", 0.0, 0.30, 0.005)
SPECS = {s.key: s for s in (THRESHOLD, MARGIN)}


@dataclass(frozen=True)
class Effective:
    high_value_threshold_aud: int
    target_margin_pct: float
    changed: dict[str, BusinessSetting]  # key -> the console change in force; absent = .env default


def _latest(session: Session, key: str) -> BusinessSetting | None:
    return session.scalars(
        select(BusinessSetting)
        .where(BusinessSetting.key == key)
        .order_by(BusinessSetting.changed_at.desc())
        .limit(1)
    ).first()


def effective(session: Session, cfg: Settings | None = None) -> Effective:
    cfg = cfg or get_settings()
    changed = {k: row for k in SPECS if (row := _latest(session, k)) is not None}
    threshold = changed.get(THRESHOLD.key)
    margin = changed.get(MARGIN.key)
    return Effective(
        high_value_threshold_aud=int(threshold.value) if threshold else cfg.high_value_threshold_aud,
        target_margin_pct=float(margin.value) if margin else cfg.target_margin_pct,
        changed=changed,
    )


def change(session: Session, key: str, value: float, by: str) -> bool:
    """Record a new value. False when it equals what is already in force (nothing written)."""
    spec = SPECS.get(key)
    if spec is None:
        raise ValueError(f"unknown setting {key!r}")
    if not spec.minimum <= value <= spec.maximum:
        raise ValueError(f"{spec.label} must be between {spec.minimum:g} and {spec.maximum:g}")
    by = by.strip()[:64]
    if not by:
        raise ValueError("say who is making the change")
    if abs(float(getattr(effective(session), key)) - value) < 1e-9:
        return False
    session.add(BusinessSetting(key=key, value=value, changed_by=by, changed_at=datetime.now().astimezone()))
    session.flush()
    return True


def history(session: Session, limit: int = 20) -> list[BusinessSetting]:
    return list(
        session.scalars(select(BusinessSetting).order_by(BusinessSetting.changed_at.desc()).limit(limit))
    )
