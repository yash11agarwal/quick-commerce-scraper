"""Pack size normalisation, money parsing and derived prices.

Rules from CLAUDE.md: populate ``unit_normalised`` and ``price_per_unit`` only when the pack
size parses unambiguously; ``discount_pct`` only when mrp and selling price are both present
and mrp >= selling price. Anything else is ``None``, never an assumed conversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

_UNIT_ALIASES: dict[str, str] = {
    "g": "g", "gm": "g", "gms": "g", "gram": "g", "grams": "g",
    "kg": "kg", "kgs": "kg",
    "ml": "ml",
    "l": "l", "ltr": "l", "lt": "l", "litre": "l", "liter": "l", "litres": "l", "liters": "l",
    "pc": "pcs", "pcs": "pcs", "piece": "pcs", "pieces": "pcs", "unit": "pcs", "units": "pcs",
}
_BASE_OF = {"g": ("g", Decimal(1)), "kg": ("g", Decimal(1000)), "ml": ("ml", Decimal(1)), "l": ("ml", Decimal(1000)), "pcs": ("pcs", Decimal(1))}

_PACK_RE = re.compile(
    r"^\s*(?:(?P<count>\d+)\s*[x×]\s*)?(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>[a-zA-Z]+)\s*$"
)


@dataclass(frozen=True)
class PackSize:
    quantity: Decimal  # in base unit
    base_unit: str  # g | ml | pcs

    @property
    def label(self) -> str:
        q = self.quantity.normalize()
        text = format(q, "f")
        return f"{text} {self.base_unit}"


def parse_pack_size(text: str | None) -> PackSize | None:
    """``500 g``, ``1.75 L``, ``6 x 200 ml``, ``8 pcs`` parse. Everything else is None."""
    if not text:
        return None
    m = _PACK_RE.match(text)
    if not m:
        return None
    unit = _UNIT_ALIASES.get(m.group("unit").lower())
    if unit is None:
        return None
    base, factor = _BASE_OF[unit]
    try:
        qty = Decimal(m.group("qty")) * factor
    except InvalidOperation:
        return None
    if m.group("count"):
        qty *= Decimal(m.group("count"))
    if qty <= 0:
        return None
    return PackSize(quantity=qty, base_unit=base)


def price_per_unit_paise(selling_price_paise: int | None, pack: PackSize | None) -> int | None:
    """Selling price per 1 kg, 1 L or 1 piece, in paise, rounded half up. None if not derivable."""
    if selling_price_paise is None or pack is None or pack.quantity <= 0:
        return None
    per = Decimal(1000) if pack.base_unit in ("g", "ml") else Decimal(1)
    value = Decimal(selling_price_paise) * per / pack.quantity
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def discount_pct(mrp_paise: int | None, selling_price_paise: int | None) -> tuple[Decimal | None, str | None]:
    """(discount, anomaly). Discount only when both present and mrp >= selling price."""
    if mrp_paise is None or selling_price_paise is None:
        return None, None
    if mrp_paise <= 0:
        return None, "mrp_not_positive"
    if mrp_paise < selling_price_paise:
        return None, "mrp_below_selling"
    pct = (Decimal(mrp_paise - selling_price_paise) * 100 / Decimal(mrp_paise)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return pct, None


_MONEY_STRIP = re.compile(r"[₹,\s]|Rs\.?|INR", re.IGNORECASE)


def rupee_text_to_paise(text: str | int | float | Decimal | None) -> int | None:
    """``"₹34"``, ``"74.25"``, ``"1,299"`` -> paise. Raises ValueError on anything else.

    Floats are refused on purpose: a platform that serves floats must be handled in its own
    parser with an explicit decision, not silently rounded here.
    """
    if text is None:
        return None
    if isinstance(text, bool):
        raise ValueError("boolean is not a price")
    if isinstance(text, float):
        raise ValueError("float prices are refused; decide the conversion in the parser")
    if isinstance(text, int):
        return text * 100
    if isinstance(text, Decimal):
        value = text
    else:
        cleaned = _MONEY_STRIP.sub("", str(text))
        if not cleaned:
            raise ValueError(f"no number in price text {text!r}")
        try:
            value = Decimal(cleaned)
        except InvalidOperation as exc:
            raise ValueError(f"price text {text!r} is not a number") from exc
    paise = value * 100
    if paise != paise.to_integral_value():
        raise ValueError(f"price {text!r} has a fractional paisa")
    if paise < 0:
        raise ValueError(f"price {text!r} is negative")
    return int(paise)


def units_nanos_to_paise(units: int | str | None, nanos: int | None) -> int | None:
    """Swiggy style ``{units, nanos}`` -> paise. ``units`` may arrive as a string."""
    if units is None and nanos is None:
        return None
    u = int(units) if units not in (None, "") else 0
    n = int(nanos or 0)
    if n % 10_000_000 != 0:
        raise ValueError(f"nanos {n} is not a whole paisa")
    if u < 0 or n < 0:
        raise ValueError("negative price")
    return u * 100 + n // 10_000_000
