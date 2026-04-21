"""Cuyahoga County MyPlace scraper.

MyPlace has an undocumented search endpoint that returns parcel metadata, and
a session-gated POST that returns a "Building Information" HTML page with
sqft / bedrooms / bathrooms / year built. We use both to confirm Rentcast data.
"""

from __future__ import annotations

import base64
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from selectolax.parser import HTMLParser


BASE = "https://myplace.cuyahogacounty.gov"
SEARCH = f"{BASE}/MyPlaceService.svc/ParcelsAndValuesByAnySearchByAndCity"
PROPERTY = f"{BASE}/MainPage/PropertyData"


@dataclass
class ParcelSearch:
    parcel_id: str
    property_number: str
    owner: str
    physical_address: str
    parcel_city: str
    parcel_zip: str


@dataclass
class BuildingInfo:
    parcel_id: str
    bedrooms: int | None
    bathrooms: float | None
    half_baths: int | None
    living_area_sqft: int | None
    year_built: int | None
    stories: str | None


@dataclass
class CountyRecord:
    parcel_id: str
    property_number: str
    owner: str
    address: str
    city: str
    zip_code: str
    bedrooms: int | None
    bathrooms: float | None
    living_area_sqft: int | None
    year_built: int | None
    myplace_url: str


_STREET_TYPES = {
    "rd", "road", "st", "street", "ave", "avenue", "blvd", "boulevard",
    "dr", "drive", "ln", "lane", "ct", "court", "pl", "place", "way",
    "cir", "circle", "hwy", "highway", "pkwy", "parkway", "ter", "terrace",
    "trl", "trail", "loop", "sq", "square", "path", "run", "oval",
}


def _search_variants(address_line1: str) -> list[str]:
    """Generate MyPlace-friendly search terms. MyPlace records may use a
    different street-type abbreviation than Zillow/Rentcast (Rd vs DR, etc.),
    so we fall back to the prefix without any trailing street-type token.
    """
    cleaned = address_line1.strip()
    variants = [cleaned]
    tokens = cleaned.split()
    if len(tokens) >= 3 and tokens[-1].rstrip(".").lower() in _STREET_TYPES:
        variants.append(" ".join(tokens[:-1]))
    return variants


async def search_by_address(client: httpx.AsyncClient, address_line1: str) -> ParcelSearch | None:
    """Search MyPlace by street address (the 'address line 1' part, no city/state)."""
    for term in _search_variants(address_line1):
        url = f"{SEARCH}/{term}"
        r = await client.get(url, params={"city": "99", "searchBy": "Address"},
                             headers={"Accept": "application/json"})
        if r.status_code != 200:
            continue
        raw = r.json()
        parcels = json.loads(raw) if isinstance(raw, str) else raw
        if not parcels or not parcels[0]:
            continue
        p = parcels[0][0]
        return ParcelSearch(
            parcel_id=str(p.get("PARCEL_ID", "")).strip(),
            property_number=str(p.get("PROPERTY_NUMBER", "")).strip(),
            owner=str(p.get("DEEDED_OWNER", "")).strip(),
            physical_address=str(p.get("PHYSICAL_ADDRESS", "")).strip(),
            parcel_city=str(p.get("PARCEL_CITY", "")).strip(),
            parcel_zip=str(p.get("PARCEL_ZIP", "")).strip(),
        )
    return None


def parcel_page_url(parcel_id: str) -> str:
    enc_id = base64.b64encode(parcel_id.encode()).decode()
    enc_by = base64.b64encode(b"Parcel").decode()
    return f"{BASE}/{enc_id}?city=99&searchBy={enc_by}"


_HIDDEN_RE = re.compile(
    r'<input[^>]+id="(hdnSearch[A-Za-z]+)"[^>]+value="([^"]*)"',
    re.IGNORECASE,
)


async def fetch_building_info(
    client: httpx.AsyncClient, parcel: ParcelSearch
) -> BuildingInfo | None:
    """POST to /MainPage/PropertyData (Building Information) and parse the HTML."""
    parcel_url = parcel_page_url(parcel.parcel_id)
    # Prime the session by loading the parcel page first; this also gives us
    # the full set of hidden-search fields the PropertyData POST expects.
    r1 = await client.get(parcel_url)
    if r1.status_code != 200:
        return None
    hidden = dict(_HIDDEN_RE.findall(r1.text))

    form = {
        "hdnParcelId": parcel.parcel_id,
        "hdnListId": "",
        "hdnButtonClicked": "Building Information",
        "hdnSearchChoice": "Parcel",
        "hdnSearchText": parcel.parcel_id,
        "hdnSearchCity": "99",
        "hdnSearchPropertyNumber": parcel.property_number,
        "hdnSearchDeededOwner": parcel.owner,
        "hdnSearchPhysicalAddress": parcel.physical_address,
        "hdnSearchParelUnit": hidden.get("hdnSearchParelUnit", ""),
        "hdnSearchParcelCity": parcel.parcel_city,
        "hdnSearchParcelZip": parcel.parcel_zip,
        "hdnSearchPropertyType": hidden.get("hdnSearchPropertyType", ""),
        "hdnSearchTaxLuc": hidden.get("hdnSearchTaxLuc", ""),
        "hdnSearchPropertyClass": hidden.get("hdnSearchPropertyClass", ""),
        "hdnSearchTaxLucDescription": hidden.get("hdnSearchTaxLucDescription", ""),
        "hdnSearchLegalDescription": hidden.get("hdnSearchLegalDescription", ""),
        "hdnSearchNeighborhoodCode": hidden.get("hdnSearchNeighborhoodCode", ""),
    }
    r2 = await client.post(
        PROPERTY,
        data=form,
        headers={
            "Referer": parcel_url,
            "Content-Type": "application/x-www-form-urlencoded",
        },
        follow_redirects=True,
    )
    if r2.status_code != 200:
        return None
    return _parse_building_html(parcel.parcel_id, r2.text)


def _parse_building_html(parcel_id: str, html: str) -> BuildingInfo:
    """Extract building fields from the MyPlace Building Information page."""
    tree = HTMLParser(html)

    # Fields appear as pairs of divs:
    #   <div class="generalInfoLabel[Green] ...">Label</div>
    #   <div class="generalInfoValue[Green] ...">Value</div>
    labels = [n for n in tree.css('div[class*="generalInfoLabel"]')]
    values = [n for n in tree.css('div[class*="generalInfoValue"]')]

    # Pair them up in document order (MyPlace alternates Label/Value in sequence).
    pairs: dict[str, str] = {}
    # The label/value divs are not guaranteed to be perfectly interleaved, but
    # in practice every Label has a matching Value immediately after it in the
    # DOM. Walk both lists by position; if mismatches occur, we still return
    # what we can.
    for lbl, val in zip(labels, values):
        key = (lbl.text(strip=True) or "").strip()
        value = (val.text(strip=True) or "").strip()
        if key:
            pairs.setdefault(key, value)

    def to_int(s: str | None) -> int | None:
        if not s:
            return None
        digits = re.sub(r"[^\d]", "", s)
        return int(digits) if digits else None

    def to_float(s: str | None) -> float | None:
        if not s:
            return None
        m = re.search(r"\d+(?:\.\d+)?", s)
        return float(m.group(0)) if m else None

    return BuildingInfo(
        parcel_id=parcel_id,
        bedrooms=to_int(pairs.get("Bedrooms")),
        bathrooms=to_float(pairs.get("Bathrooms")),
        half_baths=to_int(pairs.get("Half Baths")),
        living_area_sqft=to_int(pairs.get("Living Area Total")),
        year_built=to_int(pairs.get("Year Built")),
        stories=pairs.get("Stories") or None,
    )


async def lookup_address(address_line1: str) -> CountyRecord | None:
    """Full lookup: address → parcel search → building info."""
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        parcel = await search_by_address(client, address_line1)
        if not parcel:
            return None
        building = await fetch_building_info(client, parcel)
        if not building:
            # Still return parcel-only data; better than nothing.
            return CountyRecord(
                parcel_id=parcel.parcel_id,
                property_number=parcel.property_number,
                owner=parcel.owner,
                address=parcel.physical_address,
                city=parcel.parcel_city,
                zip_code=parcel.parcel_zip,
                bedrooms=None,
                bathrooms=None,
                living_area_sqft=None,
                year_built=None,
                myplace_url=parcel_page_url(parcel.parcel_id),
            )
        return CountyRecord(
            parcel_id=parcel.parcel_id,
            property_number=parcel.property_number,
            owner=parcel.owner,
            address=parcel.physical_address,
            city=parcel.parcel_city,
            zip_code=parcel.parcel_zip,
            bedrooms=building.bedrooms,
            bathrooms=building.bathrooms,
            living_area_sqft=building.living_area_sqft,
            year_built=building.year_built,
            myplace_url=parcel_page_url(parcel.parcel_id),
        )
