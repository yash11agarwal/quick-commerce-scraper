import pytest

from qcom.core.errors import LocationNotSetError
from qcom.core.location import check_readback, choose_suggestion, expected_states, extract_pincode, find_states, make_expectation

BLINKIT = ["PatipukurKolkata, West Bengal 700048, India", "Purani Basti, Patehra, Maihar, Madhya Pradesh 700048", "Dum Dum, West Bengal 700048", "Something else"]
SWIGGY = ["700048, Kolkata Station Road700048, Kolkata Station Road", "700048, Purani Basti, Patehra700048, Purani Basti, Patehra"]


def test_zone_table():
    assert "West Bengal" in expected_states("700048")
    assert expected_states("110001") == ("Delhi",)
    assert "Karnataka" in expected_states("560001")
    assert expected_states("000000") == ()


def test_find_states_handles_aliases_and_order():
    assert find_states("Bhubaneswar, Orissa 751001") == ("Odisha",)
    assert find_states("Kolkata, West Bengal and then Madhya Pradesh") == ("West Bengal", "Madhya Pradesh")
    assert find_states("Goa Velha") == ("Goa",)
    assert find_states("no state here") == ()


def test_decoy_state_is_rejected_without_any_city():
    choice = choose_suggestion(BLINKIT, make_expectation("700048"))
    assert choice.index == 0
    assert choice.ambiguous  # two West Bengal candidates remain
    assert any("Madhya Pradesh" in r[0] for r in choice.rejected)


def test_city_makes_it_unambiguous():
    choice = choose_suggestion(BLINKIT, make_expectation("700048", city="Kolkata"))
    assert choice.index == 0 and not choice.ambiguous


def test_swiggy_shape_without_state_is_ambiguous_and_exclusion_moves_on():
    first = choose_suggestion(SWIGGY, make_expectation("700048"))
    assert first.index == 0 and first.ambiguous
    second = choose_suggestion(SWIGGY, make_expectation("700048", exclude_suggestions=(SWIGGY[0],)))
    assert second.index == 1
    with_city = choose_suggestion(SWIGGY, make_expectation("700048", city="Kolkata"))
    assert with_city.index == 0 and not with_city.ambiguous


def test_no_candidate_raises_with_the_texts():
    with pytest.raises(LocationNotSetError) as exc:
        choose_suggestion(["Somewhere 400001"], make_expectation("700048"))
    assert exc.value.detail["suggestions"] == ["Somewhere 400001"]
    with pytest.raises(LocationNotSetError):
        choose_suggestion([BLINKIT[1]], make_expectation("700048"))


def test_user_state_overrides_zone_table():
    exp = make_expectation("700048", state="Madhya Pradesh")
    assert exp.expected_states == ("Madhya Pradesh",)
    assert choose_suggestion(BLINKIT, exp).index == 1


def test_readback_checks():
    exp = make_expectation("700048")
    assert check_readback("30 Mins Delivery to 700048, Kolkata Station Rd, South Dumdum, West Bengal 700048, India", exp).ok
    bad = check_readback("Delivery to Patehra, Madhya Pradesh 700048", exp)
    assert not bad.ok and bad.pincode_found and "Madhya Pradesh" in bad.reason
    assert not check_readback("Doddakannelli, Bengaluru, Karnataka 560035", exp).ok
    assert not check_readback(None, exp).ok
    assert check_readback("Delivery in 6 mins  700048, Kolkata", exp).ok  # no state named: pincode alone suffices


def test_extract_pincode():
    assert extract_pincode("x 700048 y", "700048") == "700048"
    assert extract_pincode("x 700049 y", "700048") is None
