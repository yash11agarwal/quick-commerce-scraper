"""Pydantic models shared by every layer.

Conventions enforced here rather than by convention:
- money is integer paise, never float
- pincodes and platform product ids are strings
- an unknown value is ``None``; never 0, "", or a guess
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

PINCODE_RE = re.compile(r"^\d{6}$")
PLATFORM_NAMES: tuple[str, ...] = ("blinkit", "swiggy_instamart", "zepto", "bigbasket")


class JobStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    OK = "OK"
    NO_RESULTS = "NO_RESULTS"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


TERMINAL_STATUSES = frozenset({JobStatus.OK, JobStatus.NO_RESULTS, JobStatus.FAILED, JobStatus.SKIPPED})


class CaptureSource(str, Enum):
    NETWORK_RESPONSE = "network_response"
    PAGE_STATE = "page_state"
    SSR_DOCUMENT = "ssr_document"
    API_REPLAY = "api_replay"
    FIXTURE = "fixture"


# ----------------------------------------------------------------------------- input


class ProductInput(BaseModel):
    input_row_id: int
    product_name: str
    brand: str | None = None
    pack_size: str | None = None
    category: str | None = None


class PincodeInput(BaseModel):
    input_row_id: int
    pincode: str
    city: str | None = None
    state: str | None = None

    @field_validator("pincode")
    @classmethod
    def _six_digits(cls, v: str) -> str:
        if not PINCODE_RE.match(v):
            raise ValueError(f"pincode must be exactly six digits, got {v!r}")
        return v


class RunSettings(BaseModel):
    platforms: list[str]
    max_results_per_query: int = Field(default=20, ge=1)
    run_label: str | None = None


class InputSpec(BaseModel):
    source_path: str
    sha256: str
    products: list[ProductInput]
    pincodes: list[PincodeInput]
    settings: RunSettings


# ----------------------------------------------------------------------------- jobs


class Job(BaseModel):
    job_id: str
    run_id: str
    platform: str
    requested_pincode: str
    city: str | None = None
    state: str | None = None
    search_term: str
    input_row_id: int
    pincode_row_id: int
    brand: str | None = None
    pack_size: str | None = None
    category: str | None = None
    max_results: int = 20

    @staticmethod
    def make_id(run_id: str, platform: str, pincode: str, input_row_id: int) -> str:
        return f"{run_id}:{platform}:{pincode}:{input_row_id}"


class LocationExpectation(BaseModel):
    """What the adapter may use to pick the right autocomplete suggestion and to judge a readback."""

    pincode: str
    city: str | None = None
    state: str | None = None
    expected_states: tuple[str, ...] = ()
    exclude_suggestions: tuple[str, ...] = ()


class EffectiveLocation(BaseModel):
    """What ``set_location`` proved the site has in effect. Never a guess."""

    platform: str
    requested_pincode: str
    effective_pincode: str | None
    store_id: str | None = None
    eta_minutes: int | None = None
    address_text: str | None = None
    evidence: dict[str, Any] = Field(default_factory=dict)
    verified_at_utc: datetime


# ----------------------------------------------------------------------------- captures


class RawCapture(BaseModel):
    """One stored response. Persisted verbatim, compressed, before any parser sees it."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    capture_id: str | None = None
    seq: int | None = None
    platform: str
    strategy: str
    source: CaptureSource
    method: str = "GET"
    url: str
    http_status: int | None = None
    content_type: str | None = None
    body: bytes
    captured_at_utc: datetime
    request: dict[str, Any] = Field(default_factory=dict)
    parse: bool = True

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.body).hexdigest()

    @property
    def size_bytes(self) -> int:
        return len(self.body)


# ----------------------------------------------------------------------------- listings


def _non_negative(v: int | None) -> int | None:
    if v is not None and v < 0:
        raise ValueError("must not be negative")
    return v


class ProductListing(BaseModel):
    """One row of the ``results`` sheet.

    Parsers fill the platform fields. ``core`` fills the derived fields (normalisation,
    discount, match score) and the run-level fields (effective pincode, capture id).
    """

    platform: str
    result_rank: int = Field(ge=1)
    platform_product_id: str = Field(min_length=1)
    product_name: str = Field(min_length=1)
    brand: str | None = None
    pack_size: str | None = None
    mrp_paise: int | None = None
    selling_price_paise: int | None = None
    base_selling_price_paise: int | None = None
    in_stock: bool | None = None
    stock_qty: int | None = None
    store_or_seller_id: str | None = None
    category_path: str | None = None
    product_url: str | None = None
    image_url: str | None = None
    currency: str = "INR"
    anomalies: list[str] = Field(default_factory=list)

    # derived by core, never by a parser
    unit_normalised: str | None = None
    price_per_unit_paise: int | None = None
    discount_pct: Decimal | None = None
    match_score: Decimal | None = None
    effective_pincode: str | None = None
    eta_minutes: int | None = None
    capture_id: str | None = None
    strategy: str | None = None

    _nn = field_validator("mrp_paise", "selling_price_paise", "base_selling_price_paise", "stock_qty", "price_per_unit_paise")(
        _non_negative
    )

    @field_validator("platform_product_id", mode="before")
    @classmethod
    def _id_is_text(cls, v: Any) -> Any:
        # ids like Blinkit's 298 must stay text; refuse anything that is not already a string
        if isinstance(v, str):
            return v
        raise ValueError("platform_product_id must be a string; cast in the parser, explicitly")


class DataQualityEvent(BaseModel):
    kind: str
    detail: str
    listing_index: int | None = None


# ----------------------------------------------------------------------------- health


class Probe(BaseModel):
    pincode: str
    term: str
    city: str | None = None
    state: str | None = None


class HealthCheck(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class HealthReport(BaseModel):
    platform: str
    adapter_version: str
    ok: bool
    strategy: str | None = None
    checks: list[HealthCheck] = Field(default_factory=list)
    location: EffectiveLocation | None = None
    capture_ids: list[str] = Field(default_factory=list)
    checked_at_utc: datetime
