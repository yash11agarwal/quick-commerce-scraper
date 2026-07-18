#!/usr/bin/env python3
"""Local dashboard for browsing scraped price/stock history.

Zero extra dependencies — Python's standard library serves a single-page
frontend (web/index.html) plus three small JSON endpoints backed by the
same SQLite database the scraper writes.

Usage:
    python dashboard.py            # serve data/observations.db on port 8000
    python dashboard.py --demo     # seed + serve synthetic demo data instead
    python dashboard.py --port 9000

Then open http://127.0.0.1:8000 (it opens automatically).
"""

from __future__ import annotations

import argparse
import json
import random
import sqlite3
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

WEB_DIR = Path(__file__).parent / "web"

# ---------------------------------------------------------------------- #
# Queries
# ---------------------------------------------------------------------- #

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _since(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")


def get_meta(db_path: str) -> dict:
    conn = _connect(db_path)
    try:
        return {
            "pincodes": [r[0] for r in conn.execute(
                "SELECT DISTINCT pincode FROM observations ORDER BY pincode")],
            "queries": [r[0] for r in conn.execute(
                "SELECT DISTINCT search_query FROM observations "
                "WHERE search_query IS NOT NULL ORDER BY search_query")],
            "platforms": [r[0] for r in conn.execute(
                "SELECT DISTINCT platform FROM observations ORDER BY platform")],
            "observation_count": conn.execute(
                "SELECT COUNT(*) FROM observations").fetchone()[0],
            "last_scrape": (conn.execute(
                "SELECT MAX(timestamp) FROM observations").fetchone()[0]),
        }
    finally:
        conn.close()


def get_summary(db_path: str, pincode: str, query: str, days: int) -> list[dict]:
    """Latest observation per (platform, product) for one pincode + query."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT * FROM (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY platform, product_id
                    ORDER BY timestamp DESC) AS rn
                FROM observations
                WHERE pincode = ? AND search_query = ? AND timestamp >= ?
            ) WHERE rn = 1
            ORDER BY platform, price IS NULL, price
            """,
            (pincode, query, _since(days)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_best_price_series(db_path: str, pincode: str, query: str, days: int) -> list[dict]:
    """Cheapest in-stock price per platform per day — the comparison chart."""
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT platform, substr(timestamp, 1, 10) AS day, MIN(price) AS best_price
            FROM observations
            WHERE pincode = ? AND search_query = ? AND timestamp >= ?
              AND in_stock = 1 AND price IS NOT NULL
            GROUP BY platform, day
            ORDER BY day
            """,
            (pincode, query, _since(days)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_product_history(db_path: str, platform: str, product_id: str,
                        pincode: str, days: int) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            """
            SELECT timestamp, price, mrp, in_stock, stock_estimate,
                   stock_granularity, raw_stock_label
            FROM observations
            WHERE platform = ? AND product_id = ? AND pincode = ? AND timestamp >= ?
            ORDER BY timestamp
            """,
            (platform, product_id, pincode, _since(days)),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------- #
# Demo data (so the dashboard can be previewed before the first real scrape)
# ---------------------------------------------------------------------- #

_DEMO_PRODUCTS = {
    "amul butter 500g": [("Amul Butter 500 g", "Amul", "500 g", 305, 270)],
    "maggi noodles": [("Maggi 2-Minute Noodles 280 g", "Nestle", "280 g", 56, 52)],
    "coca cola 750ml": [("Coca-Cola Soft Drink 750 ml", "Coca-Cola", "750 ml", 45, 40)],
}
_DEMO_PLATFORMS = ["blinkit", "swiggy_instamart", "zepto",
                   "bigbasket_now", "amazon_now", "flipkart_minutes"]


def seed_demo_db(db_path: str, days: int = 21) -> None:
    from qc_scraper.schema import ProductRecord
    from qc_scraper.storage import ObservationStore

    rng = random.Random(42)
    with ObservationStore(db_path) as store:
        for pincode in ("110001", "560001"):
            for query, products in _DEMO_PRODUCTS.items():
                for name, brand, unit, mrp, base_price in products:
                    for i, platform in enumerate(_DEMO_PLATFORMS):
                        price = base_price * rng.uniform(0.95, 1.10)
                        pid = f"demo-{abs(hash((platform, name))) % 100000}"
                        records = []
                        for d in range(days * 2):  # two observations/day
                            ts = (datetime.now(timezone.utc)
                                  - timedelta(hours=12 * (days * 2 - d)))
                            price = min(mrp, max(base_price * 0.85,
                                                 price + rng.uniform(-2.5, 2.5)))
                            in_stock = rng.random() > 0.12
                            est = rng.choice([None, 3, 5, 8]) if (
                                platform == "blinkit" and in_stock) else None
                            records.append(ProductRecord(
                                platform=platform, product_name=name, brand=brand,
                                price=round(price, 2), mrp=float(mrp),
                                discount_pct=None, quantity_unit=unit,
                                in_stock=in_stock, stock_estimate=est,
                                stock_granularity="estimate" if est is not None else "boolean",
                                pincode=pincode,
                                timestamp=ts.isoformat(timespec="seconds"),
                                product_id=pid, search_query=query,
                            ).finalize())
                        store.insert_many(records)
    print(f"seeded demo data into {db_path}")


# ---------------------------------------------------------------------- #
# HTTP server
# ---------------------------------------------------------------------- #

class DashboardHandler(BaseHTTPRequestHandler):
    db_path: str = "data/observations.db"

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urlparse(self.path)
        params = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        try:
            if parsed.path in ("/", "/index.html"):
                self._send_file(WEB_DIR / "index.html", "text/html; charset=utf-8")
            elif parsed.path == "/api/meta":
                self._send_json(get_meta(self.db_path))
            elif parsed.path == "/api/summary":
                self._send_json(get_summary(
                    self.db_path, params["pincode"], params["query"],
                    int(params.get("days", 30))))
            elif parsed.path == "/api/best-price":
                self._send_json(get_best_price_series(
                    self.db_path, params["pincode"], params["query"],
                    int(params.get("days", 30))))
            elif parsed.path == "/api/product-history":
                self._send_json(get_product_history(
                    self.db_path, params["platform"], params["product_id"],
                    params["pincode"], int(params.get("days", 30))))
            elif parsed.path == "/api/export.xlsx":
                self._send_xlsx(params)
            else:
                self.send_error(404)
        except KeyError as exc:
            self.send_error(400, f"missing query parameter: {exc}")
        except Exception as exc:  # noqa: BLE001
            self.send_error(500, str(exc))

    def _send_xlsx(self, params: dict) -> None:
        """Build and stream the Excel export for the given filters."""
        from io import BytesIO

        from qc_scraper.excel_io import build_export_workbook

        wb, _count = build_export_workbook(
            self.db_path,
            pincode=params.get("pincode") or None,
            query=params.get("query") or None,
            days=int(params["days"]) if params.get("days") else None,
        )
        buf = BytesIO()
        wb.save(buf)
        body = buf.getvalue()
        filename = f"qc_export_{datetime.now():%Y%m%d_%H%M}.xlsx"
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, data) -> None:
        body = json.dumps(data).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args) -> None:  # quieter default logging
        pass


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", default="data/observations.db")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--demo", action="store_true",
                        help="serve synthetic demo data (data/demo.db)")
    parser.add_argument("--no-browser", action="store_true",
                        help="don't auto-open the browser")
    args = parser.parse_args()

    db_path = "data/demo.db" if args.demo else args.db
    if args.demo and not Path(db_path).exists():
        seed_demo_db(db_path)
    if not Path(db_path).exists():
        raise SystemExit(
            f"{db_path} not found — run `python main.py` first to collect data, "
            "or preview with `python dashboard.py --demo`")

    DashboardHandler.db_path = db_path
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DashboardHandler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Dashboard on {url}  (db: {db_path})  — Ctrl+C to stop")
    if not args.no_browser:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")


if __name__ == "__main__":
    main()
