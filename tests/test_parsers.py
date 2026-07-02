"""Parser unit tests against synthetic payloads shaped like the real internal
APIs (captured shapes, invented values). These lock in normalization behavior
and — because the endpoints are reverse-engineered — act as the canary: when a
platform reshuffles its payloads, update the fixture AND the parser together.
"""

from qc_scraper.schema import ProductRecord, StockGranularity
from qc_scraper.scrapers.bigbasket_now import parse_search_payload as parse_bb
from qc_scraper.scrapers.blinkit import parse_search_payload as parse_blinkit
from qc_scraper.scrapers.flipkart_minutes import parse_page_fetch_payload as parse_fk
from qc_scraper.scrapers.swiggy_instamart import parse_search_payload as parse_im
from qc_scraper.scrapers.zepto import parse_search_payload as parse_zepto


def test_blinkit_capped_inventory_is_estimate_not_count():
    payload = {
        "response": {"snippets": [{"data": {"products": [
            {
                "product_id": 101, "name": "Amul Butter 500 g", "brand": "Amul",
                "price": 275, "mrp": 305, "unit": "500 g", "inventory": 5,
            },
            {
                "product_id": 102, "name": "Amul Butter 100 g", "brand": "Amul",
                "price": 62, "mrp": 62, "unit": "100 g", "inventory": 0,
            },
        ]}}]}
    }
    recs = parse_blinkit(payload, pincode="110001", query="amul butter")
    assert len(recs) == 2
    r = recs[0]
    assert r.platform == "blinkit" and r.product_id == "101"
    assert r.in_stock and r.stock_estimate == 5
    # Blinkit's inventory int is capped → must be flagged estimate, not count.
    assert r.stock_granularity == StockGranularity.ESTIMATE.value
    assert r.discount_pct == round((1 - 275 / 305) * 100, 2)
    oos = recs[1]
    assert not oos.in_stock and oos.stock_estimate == 0


def test_zepto_paise_conversion_and_boolean_stock():
    payload = {"layout": [{"data": {"resolver": {"data": {"items": [
        {"productResponse": {
            "id": "pv-abc", "outOfStock": False,
            "sellingPrice": 28000, "discountedSellingPrice": 26500, "mrp": 31000,
            "product": {"name": "Amul Butter", "brand": "Amul"},
            "productVariant": {"formattedPacksize": "500 g"},
        }},
        {"productResponse": {
            "id": "pv-def", "outOfStock": True, "mrp": 6200,
            "sellingPrice": 6200,
            "product": {"name": "Amul Butter 100g", "brand": "Amul"},
            "productVariant": {"formattedPacksize": "100 g"},
        }},
    ]}}}}]}
    recs = parse_zepto(payload, pincode="560001", query="amul butter")
    assert len(recs) == 2
    r = recs[0]
    assert r.price == 265.0 and r.mrp == 310.0  # paise → INR
    assert r.in_stock and r.stock_granularity == StockGranularity.BOOLEAN.value
    assert r.stock_estimate is None  # boolean-only: never invent a number
    assert not recs[1].in_stock


def test_instamart_variation_pricing_and_inventory_flag():
    payload = {"data": {"widgets": [{"data": [
        {
            "product_id": "IM1", "display_name": "Maggi Noodles 70 g",
            "brand": "Nestle",
            "variations": [{
                "id": "v1", "quantity": "70 g",
                "price": {"mrp": 14, "offer_price": 13},
                "inventory": {"in_stock": True},
            }],
        },
    ]}]}}
    recs = parse_im(payload, pincode="110001", query="maggi")
    assert len(recs) == 1
    r = recs[0]
    assert r.price == 13.0 and r.mrp == 14.0 and r.quantity_unit == "70 g"
    assert r.in_stock and r.stock_granularity == StockGranularity.BOOLEAN.value


def test_bigbasket_avail_status_codes_and_string_prices():
    payload = {"tabs": [{"product_info": {"products": [
        {
            "id": 40023, "desc": "Coca Cola Soft Drink", "w": "750 ml",
            "brand": {"name": "Coca Cola"},
            "pricing": {"discount": {"mrp": "45.00", "prim_price": {"sp": "40.00"}}},
            "availability": {"avail_status": "001"},
        },
        {
            "id": 40024, "desc": "Thums Up", "w": "750 ml",
            "brand": {"name": "Thums Up"},
            "pricing": {"discount": {"mrp": "45.00", "prim_price": {"sp": "45.00"}}},
            "availability": {"avail_status": "002"},
        },
    ]}}]}
    recs = parse_bb(payload, pincode="560001", query="cola")
    assert len(recs) == 2
    r = recs[0]
    assert r.price == 40.0 and r.mrp == 45.0
    assert r.in_stock and r.brand == "Coca Cola"
    assert not recs[1].in_stock


def test_flipkart_availability_intent_and_scarcity_label():
    payload = {"RESPONSE": {"slots": [{"widget": {"data": {"products": [
        {"productInfo": {"value": {
            "id": "FKPID1",
            "titles": {"title": "Amul Butter", "subtitle": "500 g"},
            "pricing": {"finalPrice": {"value": 273}, "mrp": {"value": 305}},
            "availability": {"intent": "IN_STOCK", "label": "Hurry, only 2 left!"},
        }},
        },
        {"productInfo": {"value": {
            "id": "FKPID2",
            "titles": {"title": "Amul Ghee", "subtitle": "1 L"},
            "pricing": {"finalPrice": {"value": 599}, "mrp": {"value": 640}},
            "availability": {"intent": "OUT_OF_STOCK"},
        }},
        },
    ]}}}]}}
    recs = parse_fk(payload, pincode="110001", query="amul")
    assert len(recs) == 2
    r = recs[0]
    assert r.product_id == "FKPID1" and r.in_stock
    # "only 2 left" label → estimate granularity with the parsed hint.
    assert r.stock_estimate == 2
    assert r.stock_granularity == StockGranularity.ESTIMATE.value
    assert not recs[1].in_stock


def test_finalize_derives_discount_and_zeroes_oos_estimate():
    r = ProductRecord(
        platform="x", product_name="p", brand=None, price=80.0, mrp=100.0,
        discount_pct=None, quantity_unit=None, in_stock=False,
        stock_estimate=7, stock_granularity="estimate", pincode="110001",
    ).finalize()
    assert r.discount_pct == 20.0
    assert r.stock_estimate == 0  # out-of-stock rows can't claim stock
