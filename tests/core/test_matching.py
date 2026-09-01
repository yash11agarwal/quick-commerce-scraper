from decimal import Decimal

from qcom.core.matching import match_score


def test_name_only_uses_full_weight_on_token_overlap():
    s = match_score(input_name="amul butter", input_brand=None, input_pack=None, listing_name="Amul Butter 500 g", listing_brand="Amul", listing_pack="500 g")
    assert s == Decimal("1.00")
    s = match_score(input_name="amul butter", input_brand=None, input_pack=None, listing_name="Mother Dairy Butter", listing_brand="Mother Dairy", listing_pack="500 g")
    assert s == Decimal("0.50")


def test_brand_and_pack_add_their_weights():
    kwargs = dict(input_name="butter", input_brand="Amul", input_pack="500 g", listing_name="Butter Classic", listing_brand="Amul")
    assert match_score(**kwargs, listing_pack="500 g") == Decimal("1.00")
    assert match_score(**kwargs, listing_pack="100 g") == Decimal("0.80")
    assert match_score(input_name="butter", input_brand="Amul", input_pack="500 g", listing_name="Butter", listing_brand="Nutralite", listing_pack="500 g") == Decimal("0.80")


def test_brand_token_in_listing_brand_counts_for_overlap():
    s = match_score(input_name="frooti mango", input_brand=None, input_pack=None, listing_name="Mango Drink", listing_brand="Frooti", listing_pack="600 ml")
    assert s == Decimal("1.00")


def test_empty_input_scores_zero():
    assert match_score(input_name="  ", input_brand=None, input_pack=None, listing_name="x", listing_brand=None, listing_pack=None) == Decimal("0.00")
