"""Inbound lead contract — Figure 2 of the spec, versioned.

The contract is strict on purpose (`extra="forbid"`): when the upstream platform changes shape,
ingestion fails loudly with a 422 rather than silently persisting corrupted data. Everything under
`vehicle_claimed` is a seller assertion and is recorded as an unverified fact.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

AU_STATES = ("VIC", "NSW", "QLD", "SA", "WA", "TAS", "NT", "ACT")
_VIN_RE = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")  # 17 chars, no I / O / Q
_REGO_RE = re.compile(r"^[A-Z0-9]{2,7}$")


class LeadContractError(ValueError):
    """Payload does not satisfy the contract. `errors` carries the field-level detail."""

    def __init__(self, message: str, errors: list[dict[str, Any]] | None = None) -> None:
        super().__init__(message)
        self.errors = errors or []


class UnsupportedSchemaVersion(LeadContractError):
    pass


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class SellerV1(_Strict):
    platform_id: str = Field(min_length=1, max_length=128)
    display_name: str = Field(min_length=1, max_length=128)
    phone: str | None = None

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str | None) -> str | None:
        if v is None:
            return None
        digits = re.sub(r"[^\d+]", "", v)
        if digits.startswith("04") and len(digits) == 10:
            digits = "+61" + digits[1:]
        if not re.fullmatch(r"\+61[2-9]\d{8}", digits):
            raise ValueError("phone must be an Australian number (04xx xxx xxx or +61...)")
        return digits


class VehicleClaimedV1(_Strict):
    make: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=64)
    variant: str | None = Field(default=None, max_length=64)
    year: int = Field(ge=1980, le=datetime.now().year + 1)
    odometer_km: int = Field(ge=0, le=1_500_000)
    rego: str | None = None
    vin: str | None = None
    transmission: Literal["auto", "manual"]
    fuel: Literal["petrol", "diesel", "hybrid", "ev"]
    asking_price_aud: int = Field(ge=0, le=5_000_000)
    description_raw: str = Field(default="", max_length=20_000)
    images: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("vin")
    @classmethod
    def _vin(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = v.strip().upper()
        if not _VIN_RE.match(v):
            raise ValueError("vin must be 17 characters, excluding I, O and Q")
        return v

    @field_validator("rego")
    @classmethod
    def _rego(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        v = re.sub(r"[\s-]", "", v).upper()
        if not _REGO_RE.match(v):
            raise ValueError("rego must be 2–7 letters/digits")
        return v

    @field_validator("images")
    @classmethod
    def _images(cls, v: list[str]) -> list[str]:
        for url in v:
            if not url.startswith(("http://", "https://")):
                raise ValueError(f"image url must be http(s): {url!r}")
        return v


class LocationV1(_Strict):
    suburb: str = Field(min_length=1, max_length=64)
    state: Literal["VIC", "NSW", "QLD", "SA", "WA", "TAS", "NT", "ACT"]
    postcode: str = Field(pattern=r"^\d{4}$")


class LeadV1(_Strict):
    schema_version: Literal["1.0"]
    lead_id: UUID
    source: str = Field(min_length=1, max_length=64)
    listing_url: str = Field(min_length=1)
    seller: SellerV1
    vehicle_claimed: VehicleClaimedV1
    location: LocationV1
    qualified_at: datetime
    qualification_notes: str = Field(default="", max_length=4000)


SUPPORTED_SCHEMA_VERSIONS: dict[str, type[BaseModel]] = {"1.0": LeadV1}


def parse_lead(payload: Any) -> LeadV1:
    """Validate a raw payload against the contract for its declared schema_version."""
    if not isinstance(payload, dict):
        raise LeadContractError("payload must be a JSON object")
    version = payload.get("schema_version")
    model = SUPPORTED_SCHEMA_VERSIONS.get(str(version))
    if model is None:
        raise UnsupportedSchemaVersion(
            f"unsupported schema_version {version!r}; supported: {sorted(SUPPORTED_SCHEMA_VERSIONS)}"
        )
    try:
        return model.model_validate(payload)  # type: ignore[return-value]
    except ValidationError as exc:
        errors = [
            {"loc": ".".join(str(p) for p in e["loc"]), "msg": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        raise LeadContractError("lead payload failed validation", errors) from exc
