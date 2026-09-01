from decimal import Decimal

import pytest

from qcom.core.normalise import discount_pct, parse_pack_size, price_per_unit_paise, rupee_text_to_paise, units_nanos_to_paise


@pytest.mark.parametrize(
    "text,qty,unit",
    [
        ("500 g", 500, "g"), ("500g", 500, "g"), ("1.75 L", 1750, "ml"), ("1 ltr", 1000, "ml"), ("600 ml", 600, "ml"),
        ("6 x 200 ml", 1200, "ml"), ("10 x 150 ml", 1500, "ml"), ("8 pcs", 8, "pcs"), ("2 Pieces", 2, "pcs"), ("1 unit", 1, "pcs"),
        ("1 kg", 1000, "g"), ("250 gm", 250, "g"),
    ],
)
def test_pack_sizes_that_parse(text, qty, unit):
    p = parse_pack_size(text)
    assert p is not None
    assert p.quantity == Decimal(qty) and p.base_unit == unit


@pytest.mark.parametrize("text", ["1 pc (120 ml)", "500 g - 1 kg", "combo", "", None, "approx 500 g", "2 x 3 x 100 g", "0 g", "1 pack"])
def test_pack_sizes_that_do_not_parse(text):
    assert parse_pack_size(text) is None


def test_unit_normalised_label():
    assert parse_pack_size("1.75 L").label == "1750 ml"
    assert parse_pack_size("500 g").label == "500 g"
    assert parse_pack_size("0.5 kg").label == "500 g"


def test_price_per_unit_is_per_kg_litre_or_piece():
    assert price_per_unit_paise(27500, parse_pack_size("500 g")) == 55000  # 275 per 500 g -> 550 per kg
    assert price_per_unit_paise(3400, parse_pack_size("600 ml")) == 5667  # rounded half up
    assert price_per_unit_paise(9500, parse_pack_size("10 x 150 ml")) == 6333
    assert price_per_unit_paise(4000, parse_pack_size("8 pcs")) == 500
    assert price_per_unit_paise(None, parse_pack_size("500 g")) is None
    assert price_per_unit_paise(100, None) is None


def test_discount_only_when_both_present_and_mrp_not_below():
    assert discount_pct(30500, 27500) == (Decimal("9.84"), None)
    assert discount_pct(None, 27500) == (None, None)
    assert discount_pct(30500, None) == (None, None)
    assert discount_pct(4000, 4500) == (None, "mrp_below_selling")
    assert discount_pct(0, 0) == (None, "mrp_not_positive")
    assert discount_pct(6200, 6200) == (Decimal("0.00"), None)


@pytest.mark.parametrize("text,paise", [("₹34", 3400), ("74.25", 7425), ("1,299", 129900), ("Rs. 12", 1200), (" ₹ 1,050.50 ", 105050), (0, 0), (Decimal("10.10"), 1010)])
def test_rupee_text_to_paise(text, paise):
    assert rupee_text_to_paise(text) == paise


@pytest.mark.parametrize("bad", ["", "abc", "12.345", "-5", 12.5, True])
def test_rupee_text_refuses_garbage_floats_and_fractional_paisa(bad):
    with pytest.raises(ValueError):
        rupee_text_to_paise(bad)


def test_rupee_none_is_none():
    assert rupee_text_to_paise(None) is None


def test_units_nanos():
    assert units_nanos_to_paise(109, 0) == 10900
    assert units_nanos_to_paise("109", 500000000) == 10950
    assert units_nanos_to_paise(None, None) is None
    with pytest.raises(ValueError):
        units_nanos_to_paise(1, 5)  # fractional paisa
