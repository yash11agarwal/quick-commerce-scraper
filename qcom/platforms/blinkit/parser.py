"""Blinkit parser: a pure function over one stored ``redux_store`` capture.

The capture body is ``{"searchProductBffData": <the Redux slice>}`` exactly as
``JSON.stringify`` produced it in the page (docs/platform-specs/blinkit.md section 6). Every
path read here is one the spec lists as always present; a missing or mistyped path is
``SchemaDriftError`` naming that path. A present-but-unparseable value on a single row
leaves the field ``None`` and records an anomaly, so one odd row never discards the rest.
"""

from __future__ import annotations

from typing import Any

from qcom.core.errors import SchemaDriftError
from qcom.core.models import ProductListing, RawCapture
from qcom.core.normalise import rupee_text_to_paise
from qcom.platforms.base import load_json, require

PLATFORM = "blinkit"
PRODUCT_STATES = ("available", "out_of_stock")
SNIPPETS_PATH = "searchProductBffData.snippets"
#: Reason string carried by the drift raised for a slice with no product rows (spec section 9).
EMPTY_SIGNATURE_UNCONFIRMED = "empty_signature_unconfirmed"


def _int_not_bool(value: Any, path: str, *, what: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaDriftError(f"{what} must be an integer, got {type(value).__name__}", path=path)
    return value


def product_snippets(doc: Any) -> tuple[list[tuple[int, dict[str, Any]]], list[Any]]:
    """(index, data) for every snippet carrying ``data.product_id``, plus every widget_type seen."""
    slice_ = require(doc, "searchProductBffData", expect=dict)
    if slice_ is None:
        raise SchemaDriftError("searchProductBffData is null", path="searchProductBffData")
    snippets = require(slice_, "snippets", expect=list, base="searchProductBffData")
    if snippets is None:
        raise SchemaDriftError("snippets is null", path=SNIPPETS_PATH)
    rows: list[tuple[int, dict[str, Any]]] = []
    widget_types: list[Any] = []
    for i, snippet in enumerate(snippets):
        if not isinstance(snippet, dict):
            raise SchemaDriftError("snippet is not an object", path=f"{SNIPPETS_PATH}[{i}]")
        widget_types.append(snippet.get("widget_type"))
        data = snippet.get("data")
        if isinstance(data, dict) and data.get("product_id") is not None:
            rows.append((i, data))
    return rows, widget_types


def parse_search_capture(raw: RawCapture) -> list[ProductListing]:
    doc = load_json(raw)
    rows, widget_types = product_snippets(doc)
    if not rows:
        # Spec section 9: no empty search has been captured yet, so "no product rows" cannot be
        # told apart from a reshaped payload. Loud on purpose until the signature is confirmed.
        raise SchemaDriftError(
            f"no snippet carries data.product_id; the empty-result signature is unconfirmed ({EMPTY_SIGNATURE_UNCONFIRMED})",
            path=f"{SNIPPETS_PATH}[].data.product_id",
            detail={
                "reason": EMPTY_SIGNATURE_UNCONFIRMED,
                "snippet_count": len(widget_types),
                "widget_types": sorted({str(w) for w in widget_types}),
            },
        )

    out: list[ProductListing] = []
    first_by_id: dict[str, ProductListing] = {}
    for i, data in rows:
        base = f"{SNIPPETS_PATH}[{i}].data"
        listing = _parse_row(data, base)
        seen = first_by_id.get(listing.platform_product_id)
        if seen is not None:
            # Spec section 6: de-duplicate on product_id, keep the first occurrence, but say so.
            seen.anomalies.append(f"duplicate_in_capture:{listing.platform_product_id} repeated at {base}")
            continue
        first_by_id[listing.platform_product_id] = listing
        listing.result_rank = len(out) + 1
        out.append(listing)
    return out


def _parse_row(data: dict[str, Any], base: str) -> ProductListing:
    product_id = require(data, "product_id", base=base)
    if not isinstance(product_id, str) or not product_id:
        raise SchemaDriftError(f"product_id must be a non-empty string, got {type(product_id).__name__}", path=f"{base}.product_id")

    name = require(data, "name.text", expect=str, base=base)
    if not name.strip():
        raise SchemaDriftError("name.text is blank", path=f"{base}.name.text")
    pack = require(data, "variant.text", expect=str, base=base)
    price_text = require(data, "normal_price.text", expect=str, base=base)

    mrp_node = require(data, "mrp", base=base)
    if mrp_node is None:
        mrp_text: str | None = None  # documented: null on rows that show no MRP, not an anomaly
    elif isinstance(mrp_node, dict):
        mrp_text = require(mrp_node, "text", base=f"{base}.mrp")
        if mrp_text is not None and not isinstance(mrp_text, str):
            raise SchemaDriftError(f"mrp.text must be a string or null, got {type(mrp_text).__name__}", path=f"{base}.mrp.text")
    else:
        raise SchemaDriftError(f"mrp must be an object or null, got {type(mrp_node).__name__}", path=f"{base}.mrp")

    brand = require(data, "brand_name", base=base)
    if brand is not None and not isinstance(brand, str):
        raise SchemaDriftError(f"brand_name must be a string or null, got {type(brand).__name__}", path=f"{base}.brand_name")

    inventory = _int_not_bool(require(data, "inventory", base=base), f"{base}.inventory", what="inventory")
    if inventory < 0:
        raise SchemaDriftError(f"inventory is negative ({inventory})", path=f"{base}.inventory")
    state = require(data, "product_state", expect=str, base=base)
    if state not in PRODUCT_STATES:
        raise SchemaDriftError(f"product_state {state!r} is not one of {PRODUCT_STATES}", path=f"{base}.product_state")

    merchant = require(data, "merchant_id", base=base)
    if isinstance(merchant, bool) or not isinstance(merchant, (int, str)) or (isinstance(merchant, str) and not merchant):
        raise SchemaDriftError(f"merchant_id must be an integer or string, got {type(merchant).__name__}", path=f"{base}.merchant_id")
    _int_not_bool(require(data, "group_id", base=base), f"{base}.group_id", what="group_id")

    anomalies: list[str] = []
    try:
        selling = rupee_text_to_paise(price_text)
    except ValueError as exc:
        selling = None
        anomalies.append(f"selling_price_unparseable:{price_text!r}: {exc}")
    mrp: int | None = None
    if mrp_text is not None:
        try:
            mrp = rupee_text_to_paise(mrp_text)
        except ValueError as exc:
            anomalies.append(f"mrp_unparseable:{mrp_text!r}: {exc}")

    in_stock = inventory > 0
    if (state == "available") != in_stock:
        anomalies.append(f"state_inventory_disagree:product_state={state} inventory={inventory}; inventory wins")

    return ProductListing(
        platform=PLATFORM,
        result_rank=1,  # re-assigned by the caller after de-duplication
        platform_product_id=product_id,
        product_name=name,
        brand=brand or None,
        pack_size=pack,
        mrp_paise=mrp,
        selling_price_paise=selling,
        base_selling_price_paise=None,  # Blinkit serves no pre-promotion price (spec section 7)
        in_stock=in_stock,
        stock_qty=inventory,
        store_or_seller_id=str(merchant),
        category_path=None,  # OPEN in the spec; never guessed
        product_url=None,  # OPEN: click_action shape undocumented
        image_url=None,  # OPEN: image shape undocumented
        anomalies=anomalies,
    )
