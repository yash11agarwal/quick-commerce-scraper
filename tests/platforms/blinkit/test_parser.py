"""Blinkit parser, offline, against the committed fixtures. See tests/fixtures/blinkit/README.md
for what the fixtures are (synthesised from the playbook table) and are not (live captures)."""

from __future__ import annotations

import copy
import json
from decimal import Decimal
from pathlib import Path

import pytest

from qcom.core.clock import now_utc
from qcom.core.errors import ParseError, SchemaDriftError
from qcom.core.models import CaptureSource, EffectiveLocation, Job, RawCapture
from qcom.core.quality import finalise_listings
from qcom.platforms.blinkit.adapter import BlinkitAdapter
from qcom.platforms.blinkit.parser import EMPTY_SIGNATURE_UNCONFIRMED

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "blinkit"


def capture(body: bytes) -> RawCapture:
    return RawCapture(platform="blinkit", strategy="redux_store", source=CaptureSource.PAGE_STATE, url="https://blinkit.com/s/?q=Mango", body=body, captured_at_utc=now_utc())


def fixture(name: str) -> RawCapture:
    return capture((FIXTURES / name).read_bytes())


def doc(name: str = "normal.json") -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def product(d: dict, i: int = 0) -> dict:
    """The i-th product snippet's data in a document."""
    return [s for s in d["searchProductBffData"]["snippets"] if s.get("data", {}).get("product_id") is not None][i]["data"]


def parse(d: dict):
    return BlinkitAdapter().parse(capture(json.dumps(d).encode("utf-8")))


# ----------------------------------------------------------------------------- fixtures


def test_normal_fixture_yields_the_playbook_rows_in_order():
    rows = BlinkitAdapter().parse(fixture("normal.json"))
    assert [r.platform_product_id for r in rows] == ["800171", "368933", "780291", "298", "5440"]
    assert [r.result_rank for r in rows] == [1, 2, 3, 4, 5]
    maaza = rows[3]
    assert maaza.product_name == "Maaza Mango Drink 600 ml" and maaza.pack_size == "600 ml"
    assert maaza.selling_price_paise == 3400 and maaza.mrp_paise == 3500
    assert maaza.in_stock is True and maaza.stock_qty == 9 and maaza.store_or_seller_id == "30872"
    assert maaza.currency == "INR" and maaza.base_selling_price_paise is None
    assert maaza.category_path is None and maaza.product_url is None and maaza.image_url is None
    for r in rows:
        assert isinstance(r.platform_product_id, str)
        assert r.discount_pct is None and r.match_score is None and r.unit_normalised is None  # core derives these
        assert r.anomalies == []


def test_non_product_snippets_are_ignored_not_rows():
    d = doc()
    assert d["searchProductBffData"]["snippets"][0]["widget_type"] == "image_text_vr_type_header"
    assert len(parse(d)) == 5


def test_out_of_stock_row_is_inventory_zero_not_is_sold_out():
    (row,) = BlinkitAdapter().parse(fixture("out_of_stock.json"))
    assert row.platform_product_id == "368933" and row.in_stock is False and row.stock_qty == 0
    assert row.selling_price_paise == 14500 and row.mrp_paise == 17500  # price stays populated at zero inventory


def test_missing_mrp_is_none_without_anomaly():
    (row,) = BlinkitAdapter().parse(fixture("missing_mrp.json"))
    assert row.platform_product_id == "5440" and row.mrp_paise is None and row.selling_price_paise == 4000
    assert row.anomalies == []


def test_voucher_row_keeps_its_own_merchant():
    rows = BlinkitAdapter().parse(fixture("normal.json"))
    assert rows[2].product_name == "Mango Instant Voucher" and rows[2].store_or_seller_id == "35940"


def test_corrupted_payload_is_parse_error():
    with pytest.raises(ParseError):
        BlinkitAdapter().parse(fixture("corrupted.json"))


def test_no_product_rows_is_schema_drift_until_the_empty_signature_is_captured():
    with pytest.raises(SchemaDriftError) as exc:
        BlinkitAdapter().parse(fixture("no_product_rows.json"))
    assert EMPTY_SIGNATURE_UNCONFIRMED in exc.value.message
    assert exc.value.detail["reason"] == EMPTY_SIGNATURE_UNCONFIRMED
    assert exc.value.detail["snippet_count"] == 2
    assert exc.value.path == "searchProductBffData.snippets[].data.product_id"


def test_parse_is_pure():
    a = BlinkitAdapter().parse(fixture("normal.json"))
    b = BlinkitAdapter().parse(fixture("normal.json"))
    assert [x.model_dump() for x in a] == [x.model_dump() for x in b]


# ----------------------------------------------------------------------------- structure: drift


@pytest.mark.parametrize(
    "body,path",
    [
        (b"{}", "searchProductBffData"),
        (b'{"searchProductBffData": null}', "searchProductBffData"),
        (b'{"searchProductBffData": {}}', "searchProductBffData.snippets"),
        (b'{"searchProductBffData": {"snippets": {}}}', "searchProductBffData.snippets"),
        (b'{"searchProductBffData": {"snippets": [1]}}', "searchProductBffData.snippets[0]"),
    ],
)
def test_reshaped_envelope_names_the_path(body, path):
    with pytest.raises(SchemaDriftError) as exc:
        BlinkitAdapter().parse(capture(body))
    assert exc.value.path == path


def _mutated(mutate):
    d = doc()
    mutate(product(d, 3))  # the Maaza row, snippet index 5
    return d


@pytest.mark.parametrize(
    "mutate,path_suffix",
    [
        (lambda p: p.pop("inventory"), "inventory"),
        (lambda p: p.__setitem__("inventory", "9"), "inventory"),
        (lambda p: p.__setitem__("inventory", True), "inventory"),
        (lambda p: p.__setitem__("inventory", -1), "inventory"),
        (lambda p: p.__setitem__("product_state", "sold_out"), "product_state"),
        (lambda p: p.pop("product_state"), "product_state"),
        (lambda p: p.__setitem__("product_id", 298), "product_id"),
        (lambda p: p.__setitem__("product_id", ""), "product_id"),
        (lambda p: p.pop("name"), "name"),
        (lambda p: p.__setitem__("name", {"title": "x"}), "name.text"),
        (lambda p: p.__setitem__("name", {"text": "  "}), "name.text"),
        (lambda p: p.pop("variant"), "variant"),
        (lambda p: p.__setitem__("normal_price", {"text": 34}), "normal_price.text"),
        (lambda p: p.pop("mrp"), "mrp"),
        (lambda p: p.__setitem__("mrp", "₹35"), "mrp"),
        (lambda p: p.__setitem__("mrp", {"value": 35}), "mrp.text"),
        (lambda p: p.pop("brand_name"), "brand_name"),
        (lambda p: p.__setitem__("brand_name", 12), "brand_name"),
        (lambda p: p.pop("merchant_id"), "merchant_id"),
        (lambda p: p.__setitem__("merchant_id", None), "merchant_id"),
        (lambda p: p.pop("group_id"), "group_id"),
        (lambda p: p.__setitem__("group_id", "1951318"), "group_id"),
    ],
)
def test_missing_or_mistyped_documented_key_is_schema_drift_naming_the_row(mutate, path_suffix):
    with pytest.raises(SchemaDriftError) as exc:
        parse(_mutated(mutate))
    assert exc.value.path == f"searchProductBffData.snippets[5].data.{path_suffix}"


# ----------------------------------------------------------------------------- values: None plus anomaly


def test_unparseable_price_nulls_the_field_and_flags_it_keeping_the_row():
    d = _mutated(lambda p: p.__setitem__("normal_price", {"text": "₹--"}))
    rows = parse(d)
    row = rows[3]
    assert row.selling_price_paise is None and row.mrp_paise == 3500
    assert any(a.startswith("selling_price_unparseable:") for a in row.anomalies)
    assert len(rows) == 5


def test_unparseable_mrp_text_flags_only_that_field():
    row = parse(_mutated(lambda p: p.__setitem__("mrp", {"text": "35 approx"})))[3]
    assert row.mrp_paise is None and row.selling_price_paise == 3400
    assert any(a.startswith("mrp_unparseable:") for a in row.anomalies)


def test_mrp_text_null_is_documented_not_an_anomaly():
    row = parse(_mutated(lambda p: p.__setitem__("mrp", {"text": None})))[3]
    assert row.mrp_paise is None and row.anomalies == []


def test_state_and_inventory_disagreement_is_flagged_and_inventory_wins():
    row = parse(_mutated(lambda p: p.__setitem__("inventory", 0)))[3]
    assert row.in_stock is False and row.stock_qty == 0
    assert any(a.startswith("state_inventory_disagree:") for a in row.anomalies)


def test_is_sold_out_is_never_read():
    row = parse(_mutated(lambda p: p.__setitem__("is_sold_out", True)))[3]
    assert row.in_stock is True and row.stock_qty == 9


def test_brand_name_string_passes_through_and_blank_is_none():
    assert parse(_mutated(lambda p: p.__setitem__("brand_name", "Maaza")))[3].brand == "Maaza"
    assert parse(_mutated(lambda p: p.__setitem__("brand_name", "")))[3].brand is None


def test_merchant_id_string_or_int_becomes_text():
    assert parse(_mutated(lambda p: p.__setitem__("merchant_id", "30872")))[3].store_or_seller_id == "30872"
    assert parse(doc())[3].store_or_seller_id == "30872"


def test_duplicate_product_id_keeps_first_and_says_so():
    d = doc()
    snips = d["searchProductBffData"]["snippets"]
    snips.append(copy.deepcopy(snips[5]))  # Maaza again, at the end
    rows = parse(d)
    assert [r.platform_product_id for r in rows] == ["800171", "368933", "780291", "298", "5440"]
    assert rows[3].anomalies == ["duplicate_in_capture:298 repeated at searchProductBffData.snippets[7].data"]


def test_fractional_paisa_is_refused_not_rounded():
    row = parse(_mutated(lambda p: p.__setitem__("normal_price", {"text": "₹34.005"})))[3]
    assert row.selling_price_paise is None and any("fractional" in a for a in row.anomalies)


# ----------------------------------------------------------------------------- through core


def test_core_derives_units_discount_and_score_from_parsed_rows():
    adapter = BlinkitAdapter()
    cap = fixture("normal.json")
    cap.capture_id = "t:000001"
    job = Job(job_id="j", run_id="t", platform="blinkit", requested_pincode="700048", search_term="Mango", input_row_id=2, pincode_row_id=2, max_results=20)
    loc = EffectiveLocation(platform="blinkit", requested_pincode="700048", effective_pincode="700048", eta_minutes=20, address_text="Delivery in 20 minutes Patipukur, Kolkata, West Bengal 700048, India", verified_at_utc=now_utc())
    rows, events = finalise_listings(job, [(cap, adapter.parse(cap))], loc)
    maaza = rows[3]
    assert maaza.unit_normalised == "600 ml" and maaza.price_per_unit_paise == 5667  # 34 rupees per 600 ml -> 56.67 per litre
    assert maaza.discount_pct == Decimal("2.86") and maaza.effective_pincode == "700048" and maaza.eta_minutes == 20
    assert maaza.capture_id == "t:000001" and maaza.strategy == "redux_store"
    assert rows[4].mrp_paise is None and rows[4].discount_pct is None
    assert [e.kind for e in events] == ["missing_mrp"]  # the Paper Boat row; every pack size in the fixture parses
    assert rows[2].unit_normalised == "1 pcs" and rows[2].price_per_unit_paise == 49500
