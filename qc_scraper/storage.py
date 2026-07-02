"""SQLite persistence.

Every scrape appends rows (never updates), so the table is a time series:
querying ``(platform, product_id)`` ordered by ``timestamp`` gives the full
price/stock history for a SKU at each pincode.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

from .schema import ProductRecord

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    platform          TEXT NOT NULL,
    product_id        TEXT,
    product_name      TEXT NOT NULL,
    brand             TEXT,
    price             REAL,
    mrp               REAL,
    discount_pct      REAL,
    quantity_unit     TEXT,
    in_stock          INTEGER NOT NULL,          -- 0/1
    stock_estimate    INTEGER,                   -- NULL when granularity='boolean'
    stock_granularity TEXT NOT NULL,             -- boolean | estimate | count
    raw_stock_label   TEXT,
    pincode           TEXT NOT NULL,
    search_query      TEXT,
    timestamp         TEXT NOT NULL              -- ISO-8601 UTC
);

CREATE INDEX IF NOT EXISTS idx_obs_platform_product_time
    ON observations (platform, product_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_obs_pincode_time
    ON observations (pincode, timestamp);
"""

_INSERT = """
INSERT INTO observations (
    platform, product_id, product_name, brand, price, mrp, discount_pct,
    quantity_unit, in_stock, stock_estimate, stock_granularity,
    raw_stock_label, pincode, search_query, timestamp
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


class ObservationStore:
    def __init__(self, db_path: str | Path):
        path = Path(db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def insert_many(self, records: Iterable[ProductRecord]) -> int:
        rows = [
            (
                r.platform, r.product_id, r.product_name, r.brand, r.price,
                r.mrp, r.discount_pct, r.quantity_unit, int(r.in_stock),
                r.stock_estimate, r.stock_granularity, r.raw_stock_label,
                r.pincode, r.search_query, r.timestamp,
            )
            for r in records
        ]
        if not rows:
            return 0
        with self._conn:
            self._conn.executemany(_INSERT, rows)
        return len(rows)

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ObservationStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
