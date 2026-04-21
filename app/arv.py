"""ARV calculation.

Criteria (all must hold for a comp to qualify):
- Same property type as the subject (e.g. Single Family).
- Within MAX_DISTANCE_MI miles of the subject.
- Sold within MAX_DAYS_OLD days (Rentcast status == "Inactive" with a removedDate).
- Has a known sale price.
- Has a county-confirmed square footage for itself AND the subject has one too.

Ranking: top N by sale price (we want the high-end sold comps).
ARV = average $/sqft of those top N × the subject's county-confirmed sqft.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any


MAX_DISTANCE_MI = 0.5
MAX_DAYS_OLD = 180
TOP_N = 3


@dataclass
class Comp:
    address: str
    price: int
    rentcast_sqft: int | None
    county_sqft: int | None
    bedrooms: int | None
    bathrooms: float | None
    year_built: int | None
    distance_mi: float | None
    days_old: int | None
    status: str | None
    property_type: str | None
    price_per_sqft: float | None       # computed with county_sqft
    parcel_id: str | None
    property_number: str | None
    myplace_url: str | None


@dataclass
class Subject:
    address: str
    rentcast_sqft: int | None
    county_sqft: int | None
    bedrooms: int | None
    bathrooms: float | None
    year_built: int | None
    parcel_id: str | None
    property_number: str | None
    owner: str | None
    myplace_url: str | None


@dataclass
class DroppedComp:
    address: str
    price: int | None
    distance_mi: float | None
    days_old: int | None
    status: str | None
    property_type: str | None
    reason: str


@dataclass
class ArvResult:
    subject: Subject
    rentcast_estimate: int | None
    rentcast_low: int | None
    rentcast_high: int | None
    arv: int | None
    avg_price_per_sqft: float | None
    comps_used: list[Comp]
    comps_dropped: list[DroppedComp]
    criteria: dict[str, Any]


def _pre_filter(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[DroppedComp]]:
    """Keep Rentcast comps that pass hard criteria before we even go to the county.
    Returns the eligible raw comp dicts plus early drops with reasons.
    """
    subject = payload.get("subjectProperty") or {}
    prop_type = subject.get("propertyType")
    eligible: list[dict[str, Any]] = []
    dropped: list[DroppedComp] = []

    for c in payload.get("comparables") or []:
        reason: str | None = None
        dist = c.get("distance")
        days = c.get("daysOld")

        if c.get("propertyType") != prop_type:
            reason = f"property type {c.get('propertyType')!r} ≠ subject {prop_type!r}"
        elif c.get("status") != "Inactive":
            reason = "not marked sold (status is Active)"
        elif not c.get("price"):
            reason = "no sale price"
        elif dist is None or dist > MAX_DISTANCE_MI:
            reason = f"distance {dist:.2f} mi > {MAX_DISTANCE_MI} mi" if dist is not None else "no distance"
        elif days is None or days > MAX_DAYS_OLD:
            reason = f"sold {days}d ago > {MAX_DAYS_OLD}d" if days is not None else "no sale date"

        if reason:
            dropped.append(
                DroppedComp(
                    address=c.get("formattedAddress", ""),
                    price=c.get("price"),
                    distance_mi=dist,
                    days_old=days,
                    status=c.get("status"),
                    property_type=c.get("propertyType"),
                    reason=reason,
                )
            )
        else:
            eligible.append(c)

    return eligible, dropped


def select_candidate_comps(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], list[DroppedComp]]:
    """Return up to TOP_N eligible comps ranked by highest sale price, plus any
    dropped with reason. The county sqft check happens in `finalize()` after
    the caller has looked each one up in MyPlace.
    """
    eligible, dropped = _pre_filter(payload)
    ranked = sorted(eligible, key=lambda c: c.get("price", 0), reverse=True)
    return ranked[:TOP_N], dropped


def finalize(
    payload: dict[str, Any],
    subject_county: Any,
    ranked_comps: list[dict[str, Any]],
    comp_county_records: list[Any],
    pre_dropped: list[DroppedComp],
) -> ArvResult:
    """Combine Rentcast + county data into the final ARV result."""
    subj_raw = payload.get("subjectProperty") or {}
    subj_county_sqft = subject_county.living_area_sqft if subject_county else None

    subject = Subject(
        address=subj_raw.get("formattedAddress", ""),
        rentcast_sqft=subj_raw.get("squareFootage"),
        county_sqft=subj_county_sqft,
        bedrooms=subject_county.bedrooms if subject_county else subj_raw.get("bedrooms"),
        bathrooms=subject_county.bathrooms if subject_county else subj_raw.get("bathrooms"),
        year_built=subject_county.year_built if subject_county else subj_raw.get("yearBuilt"),
        parcel_id=subject_county.parcel_id if subject_county else None,
        property_number=subject_county.property_number if subject_county else None,
        owner=subject_county.owner if subject_county else None,
        myplace_url=subject_county.myplace_url if subject_county else None,
    )

    comps_used: list[Comp] = []
    extra_dropped: list[DroppedComp] = []

    for c, county in zip(ranked_comps, comp_county_records):
        county_sqft = county.living_area_sqft if county else None
        if county_sqft is None:
            extra_dropped.append(
                DroppedComp(
                    address=c.get("formattedAddress", ""),
                    price=c.get("price"),
                    distance_mi=c.get("distance"),
                    days_old=c.get("daysOld"),
                    status=c.get("status"),
                    property_type=c.get("propertyType"),
                    reason=(
                        "not found in Cuyahoga MyPlace"
                        if county is None
                        else "MyPlace has no living-area sqft"
                    ),
                )
            )
            continue

        pps = c["price"] / county_sqft
        comps_used.append(
            Comp(
                address=c.get("formattedAddress", ""),
                price=int(c["price"]),
                rentcast_sqft=c.get("squareFootage"),
                county_sqft=county_sqft,
                bedrooms=county.bedrooms if county else c.get("bedrooms"),
                bathrooms=county.bathrooms if county else c.get("bathrooms"),
                year_built=county.year_built if county else c.get("yearBuilt"),
                distance_mi=c.get("distance"),
                days_old=c.get("daysOld"),
                status=c.get("status"),
                property_type=c.get("propertyType"),
                price_per_sqft=round(pps, 2),
                parcel_id=county.parcel_id,
                property_number=county.property_number,
                myplace_url=county.myplace_url,
            )
        )

    avg_pps: float | None = None
    arv: int | None = None
    if comps_used and subj_county_sqft:
        avg_pps = statistics.mean(c.price_per_sqft for c in comps_used)
        arv = int(round(avg_pps * subj_county_sqft))

    return ArvResult(
        subject=subject,
        rentcast_estimate=payload.get("price"),
        rentcast_low=payload.get("priceRangeLow"),
        rentcast_high=payload.get("priceRangeHigh"),
        arv=arv,
        avg_price_per_sqft=round(avg_pps, 2) if avg_pps else None,
        comps_used=comps_used,
        comps_dropped=pre_dropped + extra_dropped,
        criteria={
            "top_n_by_price": TOP_N,
            "max_distance_mi": MAX_DISTANCE_MI,
            "max_days_old": MAX_DAYS_OLD,
            "same_property_type": True,
            "status": "Inactive (sold/removed)",
            "sqft_source": "Cuyahoga County MyPlace (required)",
        },
    )
