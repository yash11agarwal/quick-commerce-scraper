"""Turning parsed listings into output rows: rank, de-duplicate, normalise, score, flag.

Nothing here changes a value the platform served. It adds derived columns under the rules in
CLAUDE.md and records a data quality event for everything it could not derive.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from qcom.core.matching import match_score
from qcom.core.models import DataQualityEvent, EffectiveLocation, Job, ProductListing, RawCapture
from qcom.core.normalise import discount_pct, parse_pack_size, price_per_unit_paise

PreviousPrice = Callable[[str, str, str], tuple[str, int] | None]


def finalise_listings(
    job: Job,
    parsed: list[tuple[RawCapture, list[ProductListing]]],
    location: EffectiveLocation,
    *,
    previous_price: PreviousPrice | None = None,
    price_move_warn_pct: float = 40.0,
) -> tuple[list[ProductListing], list[DataQualityEvent]]:
    events: list[DataQualityEvent] = []
    out: list[ProductListing] = []
    seen: set[str] = set()

    for capture, rows in parsed:
        for row in rows:
            if row.platform_product_id in seen:
                events.append(DataQualityEvent(kind="duplicate_product_id", detail=f"{row.platform_product_id} repeated in {capture.capture_id}"))
                continue
            seen.add(row.platform_product_id)
            row.capture_id = capture.capture_id
            row.strategy = capture.strategy
            out.append(row)

    out = out[: job.max_results]

    for idx, row in enumerate(out):
        row.result_rank = idx + 1
        row.effective_pincode = location.effective_pincode
        if row.eta_minutes is None:
            row.eta_minutes = location.eta_minutes

        for anomaly in row.anomalies:
            events.append(DataQualityEvent(kind=anomaly.split(":", 1)[0], detail=anomaly, listing_index=idx))

        pack = parse_pack_size(row.pack_size)
        if pack is None:
            row.unit_normalised = None
            row.price_per_unit_paise = None
            events.append(DataQualityEvent(kind="pack_size_unparsed", detail=f"{row.pack_size!r}", listing_index=idx))
        else:
            row.unit_normalised = pack.label
            row.price_per_unit_paise = price_per_unit_paise(row.selling_price_paise, pack)

        pct, anomaly = discount_pct(row.mrp_paise, row.selling_price_paise)
        row.discount_pct = pct
        if anomaly:
            events.append(DataQualityEvent(kind=anomaly, detail=f"mrp={row.mrp_paise} selling={row.selling_price_paise}", listing_index=idx))
        if row.mrp_paise is None:
            events.append(DataQualityEvent(kind="missing_mrp", detail=row.platform_product_id, listing_index=idx))
        if row.selling_price_paise is None:
            events.append(DataQualityEvent(kind="missing_selling_price", detail=row.platform_product_id, listing_index=idx))

        row.match_score = match_score(
            input_name=job.search_term,
            input_brand=job.brand,
            input_pack=job.pack_size,
            listing_name=row.product_name,
            listing_brand=row.brand,
            listing_pack=row.pack_size,
        )

        if previous_price is not None and row.selling_price_paise is not None:
            prev = previous_price(job.platform, job.requested_pincode, row.platform_product_id)
            if prev is not None:
                prev_run, prev_paise = prev
                if prev_paise > 0:
                    move = abs(Decimal(row.selling_price_paise - prev_paise)) * 100 / Decimal(prev_paise)
                    if move > Decimal(str(price_move_warn_pct)):
                        events.append(
                            DataQualityEvent(
                                kind="price_moved_gt_threshold",
                                detail=f"{row.platform_product_id}: {prev_paise} paise in {prev_run} -> {row.selling_price_paise} paise ({move:.1f}%)",
                                listing_index=idx,
                            )
                        )
    return out, events
