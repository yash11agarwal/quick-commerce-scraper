"""Normalized output schema shared by every platform adapter.

A note on "inventory levels": these platforms almost never expose true
unit counts publicly. What each one actually exposes (as of writing):

- boolean in-stock / out-of-stock flags (all platforms), and/or
- vague scarcity labels like "Only 2 left" (some platforms, some SKUs), and/or
- an integer inventory-ish field in internal search payloads (e.g. Blinkit's
  ``inventory``) which is typically *capped/truncated* by the backend and is
  at best a lower-bound estimate, not a warehouse count.

``stock_granularity`` records which of those a given row is based on, so
downstream analysis never mistakes a boolean for a count.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class StockGranularity(str, Enum):
    """How much the platform actually told us about stock for this row."""

    #: Platform only exposed an in-stock/out-of-stock flag. ``stock_estimate``
    #: is None and must not be inferred.
    BOOLEAN = "boolean"

    #: Platform exposed a scarcity hint ("Only 2 left") or a capped integer
    #: field. ``stock_estimate`` is a *lower bound / hint*, not a real count.
    ESTIMATE = "estimate"

    #: Platform exposed what appears to be an actual unit count. Rare; treat
    #: with suspicion and re-verify periodically.
    COUNT = "count"


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ProductRecord:
    """One observation of one product on one platform at one pincode."""

    platform: str
    product_name: str
    brand: Optional[str]
    price: Optional[float]          # current selling price (INR)
    mrp: Optional[float]            # printed MRP (INR)
    discount_pct: Optional[float]   # 0-100; derived from mrp/price if absent
    quantity_unit: Optional[str]    # pack size as shown, e.g. "500 g", "6 x 200 ml"
    in_stock: bool
    stock_estimate: Optional[int]   # only meaningful when granularity != boolean
    stock_granularity: str          # StockGranularity value
    pincode: str
    timestamp: str = field(default_factory=utcnow_iso)  # ISO-8601 UTC

    # Extras beyond the required schema (useful for tracking over time):
    product_id: Optional[str] = None       # platform-internal id, for get_inventory()
    raw_stock_label: Optional[str] = None  # verbatim label, e.g. "Only 2 left"
    search_query: Optional[str] = None     # query that surfaced this row

    def finalize(self) -> "ProductRecord":
        """Fill derived fields; call before persisting."""
        if self.discount_pct is None and self.price is not None and self.mrp:
            if self.mrp > 0 and self.price <= self.mrp:
                self.discount_pct = round((1 - self.price / self.mrp) * 100, 2)
        if not self.in_stock:
            # An out-of-stock row never carries a positive estimate.
            self.stock_estimate = 0 if self.stock_estimate is not None else None
        return self

    def as_dict(self) -> dict:
        return asdict(self)


def to_float(value) -> Optional[float]:
    """Lenient numeric coercion for price fields that arrive as str/int/float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        cleaned = str(value).replace("₹", "").replace("Rs.", "").replace(",", "").strip()
        return float(cleaned) if cleaned else None
    except (TypeError, ValueError):
        return None


def to_int(value) -> Optional[int]:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
