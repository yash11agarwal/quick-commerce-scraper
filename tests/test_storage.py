from qc_scraper.schema import ProductRecord
from qc_scraper.storage import ObservationStore


def _rec(**overrides) -> ProductRecord:
    base = dict(
        platform="blinkit", product_name="Amul Butter 500 g", brand="Amul",
        price=275.0, mrp=305.0, discount_pct=None, quantity_unit="500 g",
        in_stock=True, stock_estimate=5, stock_granularity="estimate",
        pincode="110001", product_id="101", search_query="amul butter",
    )
    base.update(overrides)
    return ProductRecord(**base).finalize()


def test_roundtrip_appends_time_series(tmp_path):
    db = tmp_path / "obs.db"
    with ObservationStore(db) as store:
        assert store.insert_many([_rec(), _rec(price=280.0)]) == 2
        # Appending (not upserting) is what builds the history.
        assert store.insert_many([_rec(price=285.0)]) == 1

    import sqlite3
    conn = sqlite3.connect(db)
    rows = conn.execute(
        "SELECT price, in_stock, stock_granularity, timestamp FROM observations "
        "WHERE platform='blinkit' AND product_id='101' ORDER BY id"
    ).fetchall()
    assert [r[0] for r in rows] == [275.0, 280.0, 285.0]
    assert all(r[1] == 1 and r[2] == "estimate" and r[3] for r in rows)
