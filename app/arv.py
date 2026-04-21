"""ARV calculation from Rentcast AVM + comparables.

Strategy:
- Filter comps that have both a price and a square footage.
- Compute $/sqft for each.
- Trim the highest and lowest outlier by $/sqft (when we have >= 5 comps).
- ARV = median $/sqft * subject square footage.

We also surface Rentcast's own AVM price as a sanity check.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any


@dataclass
class Comp:
    address: str
    price: int
    sqft: int | None
    bedrooms: int | None
    bathrooms: float | None
    year_built: int | None
    distance_mi: float | None
    days_old: int | None
    status: str | None
    price_per_sqft: float | None
    correlation: float | None
    zip_code: str | None
    city: str | None


@dataclass
class ArvResult:
    subject_address: str
    subject_sqft: int | None
    subject_bedrooms: int | None
    subject_bathrooms: float | None
    subject_year_built: int | None
    rentcast_estimate: int | None
    rentcast_low: int | None
    rentcast_high: int | None
    arv: int | None
    median_price_per_sqft: float | None
    comps_used: list[Comp]
    comps_dropped: list[Comp]


def _comp_from_rentcast(c: dict[str, Any]) -> Comp:
    price = c.get("price")
    sqft = c.get("squareFootage")
    pps = (price / sqft) if (price and sqft) else None
    return Comp(
        address=c.get("formattedAddress", ""),
        price=int(price) if price else 0,
        sqft=int(sqft) if sqft else None,
        bedrooms=c.get("bedrooms"),
        bathrooms=c.get("bathrooms"),
        year_built=c.get("yearBuilt"),
        distance_mi=c.get("distance"),
        days_old=c.get("daysOld"),
        status=c.get("status"),
        price_per_sqft=round(pps, 2) if pps else None,
        correlation=c.get("correlation"),
        zip_code=c.get("zipCode"),
        city=c.get("city"),
    )


def calculate(rentcast_payload: dict[str, Any]) -> ArvResult:
    subject = rentcast_payload.get("subjectProperty") or {}
    raw_comps = rentcast_payload.get("comparables") or []

    comps_all = [_comp_from_rentcast(c) for c in raw_comps]
    usable = [c for c in comps_all if c.sqft and c.price and c.price_per_sqft]

    used: list[Comp] = []
    dropped = [c for c in comps_all if c not in usable]
    median_pps: float | None = None
    arv: int | None = None

    subject_sqft = subject.get("squareFootage")

    if usable and subject_sqft:
        sorted_comps = sorted(usable, key=lambda c: c.price_per_sqft)
        # Trim the extreme high and low outliers when we have enough comps.
        if len(sorted_comps) >= 5:
            dropped.extend([sorted_comps[0], sorted_comps[-1]])
            used = sorted_comps[1:-1]
        else:
            used = sorted_comps
        median_pps = statistics.median(c.price_per_sqft for c in used)
        arv = int(round(median_pps * subject_sqft))

    return ArvResult(
        subject_address=subject.get("formattedAddress", ""),
        subject_sqft=subject_sqft,
        subject_bedrooms=subject.get("bedrooms"),
        subject_bathrooms=subject.get("bathrooms"),
        subject_year_built=subject.get("yearBuilt"),
        rentcast_estimate=rentcast_payload.get("price"),
        rentcast_low=rentcast_payload.get("priceRangeLow"),
        rentcast_high=rentcast_payload.get("priceRangeHigh"),
        arv=arv,
        median_price_per_sqft=round(median_pps, 2) if median_pps else None,
        comps_used=used,
        comps_dropped=dropped,
    )
