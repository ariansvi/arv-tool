import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import arv, myplace, rentcast
from .zillow_url import parse_zillow_url


logger = logging.getLogger("arv_tool")
logging.basicConfig(level=logging.INFO)

BASE_DIR = Path(__file__).parent
app = FastAPI(title="ARV Tool")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/healthz")
async def healthz():
    return {"ok": True}


@app.post("/analyze", response_class=HTMLResponse)
async def analyze(request: Request, zillow_url: str = Form(...)):
    try:
        address = parse_zillow_url(zillow_url)
    except ValueError as e:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": str(e), "zillow_url": zillow_url},
            status_code=400,
        )

    try:
        payload = await rentcast.value_estimate(address)
    except rentcast.RentcastError as e:
        return templates.TemplateResponse(
            "index.html",
            {"request": request, "error": f"Rentcast: {e}", "zillow_url": zillow_url},
            status_code=502,
        )

    result = arv.calculate(payload)

    # County lookups (subject + every comp) in parallel.
    subject_line1 = (payload.get("subjectProperty") or {}).get("addressLine1", "")
    comp_line1s = [
        (c.get("addressLine1") or "") for c in (payload.get("comparables") or [])
    ]

    async def safe_lookup(addr: str):
        if not addr:
            return None
        try:
            return await myplace.lookup_address(addr)
        except Exception as e:
            logger.warning("MyPlace lookup failed for %s: %s", addr, e)
            return None

    # Only Cuyahoga County addresses will match. Non-Cuyahoga comps return None.
    lookups = await asyncio.gather(
        safe_lookup(subject_line1),
        *[safe_lookup(a) for a in comp_line1s],
    )
    subject_county = lookups[0]
    comp_counties = lookups[1:]

    # Attach county verification to each used comp by matching on address line.
    comp_county_map = {
        addr.lower(): rec
        for addr, rec in zip(comp_line1s, comp_counties)
        if rec is not None
    }

    def verify(comp_addr: str, sqft: int | None, beds: int | None):
        # Match using the first token (street number) + street (first two tokens) as prefix
        head = comp_addr.split(",")[0].strip().lower()
        rec = None
        for key, r in comp_county_map.items():
            if head.startswith(key.lower()) or key.lower().startswith(head):
                rec = r
                break
        if rec is None:
            return {"found": False, "rec": None, "sqft_match": None, "beds_match": None}
        sqft_match = (
            (rec.living_area_sqft == sqft) if (sqft and rec.living_area_sqft) else None
        )
        beds_match = (rec.bedrooms == beds) if (beds and rec.bedrooms) else None
        return {"found": True, "rec": rec, "sqft_match": sqft_match, "beds_match": beds_match}

    comp_verifications = [
        verify(c.address, c.sqft, c.bedrooms) for c in result.comps_used
    ]

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "zillow_url": zillow_url,
            "address": address,
            "result": result,
            "subject_county": subject_county,
            "comp_verifications": comp_verifications,
        },
    )
