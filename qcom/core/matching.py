"""match_score: how well a listing matches the input row. Written, never used to filter."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from qcom.core.normalise import parse_pack_size

_TOKEN_RE = re.compile(r"[^a-z0-9]+")


def tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {t for t in _TOKEN_RE.split(text.lower()) if t}


def match_score(
    *,
    input_name: str,
    input_brand: str | None,
    input_pack: str | None,
    listing_name: str,
    listing_brand: str | None,
    listing_pack: str | None,
) -> Decimal:
    """0.6 name overlap + 0.2 brand + 0.2 pack; weights fold into name when the input lacks a field."""
    q = tokens(input_name)
    if not q:
        return Decimal("0.00")
    hay = tokens(listing_name) | tokens(listing_brand)
    name_overlap = Decimal(len(q & hay)) / Decimal(len(q))

    w_name, w_brand, w_pack = Decimal("0.6"), Decimal("0.2"), Decimal("0.2")
    score = Decimal(0)

    if input_brand:
        brand_ok = bool(listing_brand) and input_brand.strip().lower() == (listing_brand or "").strip().lower()
        score += w_brand * (1 if brand_ok else 0)
    else:
        w_name += w_brand

    if input_pack:
        a, b = parse_pack_size(input_pack), parse_pack_size(listing_pack)
        pack_ok = a is not None and b is not None and a.base_unit == b.base_unit and a.quantity == b.quantity
        score += w_pack * (1 if pack_ok else 0)
    else:
        w_name += w_pack

    score += w_name * name_overlap
    return score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
