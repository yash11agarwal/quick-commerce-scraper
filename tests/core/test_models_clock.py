from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from qcom.core.clock import IST, iso, new_run_id, now_utc, parse_iso, to_ist
from qcom.core.models import PincodeInput, ProductListing


def test_timestamps_are_aware_and_ist_is_plus_530():
    t = now_utc()
    assert t.tzinfo is not None
    ist = to_ist(t)
    assert ist.utcoffset().total_seconds() == 5.5 * 3600
    assert parse_iso(iso(t)) == t.replace(microsecond=0)
    with pytest.raises(ValueError):
        iso(datetime(2026, 1, 1))


def test_run_id_is_ist_stamped_and_sortable():
    fixed = datetime(2026, 9, 1, 20, 6, 0, tzinfo=timezone.utc)
    rid = new_run_id(fixed)
    assert rid.startswith("20260902-013600-")  # 20:06 UTC is 01:36 IST next day
    assert len(rid) == len("20260902-013600-") + 6
    assert to_ist(fixed).tzinfo is IST


def test_pincode_must_be_six_digit_text():
    assert PincodeInput(input_row_id=2, pincode="700048").pincode == "700048"
    with pytest.raises(ValidationError):
        PincodeInput(input_row_id=2, pincode="70004")
    with pytest.raises(ValidationError):
        PincodeInput(input_row_id=2, pincode=700048)  # type: ignore[arg-type]


def test_listing_refuses_non_string_ids_and_negative_money():
    ok = ProductListing(platform="x", result_rank=1, platform_product_id="298", product_name="n")
    assert ok.platform_product_id == "298" and ok.stock_qty is None and ok.in_stock is None
    with pytest.raises(ValidationError):
        ProductListing(platform="x", result_rank=1, platform_product_id=298, product_name="n")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ProductListing(platform="x", result_rank=1, platform_product_id="1", product_name="n", mrp_paise=-1)
