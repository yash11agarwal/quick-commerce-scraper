from datetime import datetime, timedelta, timezone

from dashboard import get_best_price_series, get_meta, get_summary
from qc_scraper.schema import ProductRecord
from qc_scraper.storage import ObservationStore


def _rec(ts_offset_hours: int, price: float, in_stock: bool = True) -> ProductRecord:
    ts = datetime.now(timezone.utc) - timedelta(hours=ts_offset_hours)
    return ProductRecord(
        platform="blinkit", product_name="Amul Butter 500 g", brand="Amul",
        price=price, mrp=305.0, discount_pct=None, quantity_unit="500 g",
        in_stock=in_stock, stock_estimate=None, stock_granularity="boolean",
        pincode="110001", timestamp=ts.isoformat(timespec="seconds"),
        product_id="101", search_query="amul butter",
    ).finalize()


def test_summary_returns_only_latest_row_per_product(tmp_path):
    db = str(tmp_path / "obs.db")
    with ObservationStore(db) as store:
        store.insert_many([_rec(48, 270.0), _rec(24, 275.0), _rec(1, 280.0)])

    rows = get_summary(db, "110001", "amul butter", days=30)
    assert len(rows) == 1
    assert rows[0]["price"] == 280.0  # newest observation wins

    meta = get_meta(db)
    assert meta["observation_count"] == 3
    assert meta["pincodes"] == ["110001"]


def test_best_price_excludes_out_of_stock(tmp_path):
    db = str(tmp_path / "obs.db")
    with ObservationStore(db) as store:
        store.insert_many([
            _rec(2, 270.0, in_stock=True),
            _rec(1, 100.0, in_stock=False),  # cheap but OOS — must not win
        ])
    series = get_best_price_series(db, "110001", "amul butter", days=30)
    assert len(series) == 1
    assert series[0]["best_price"] == 270.0
