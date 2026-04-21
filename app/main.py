import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Form, Request
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

    ranked_comps, pre_dropped = arv.select_candidate_comps(payload)

    subject_line1 = (payload.get("subjectProperty") or {}).get("addressLine1", "")
    comp_line1s = [c.get("addressLine1") or "" for c in ranked_comps]

    async def safe_lookup(addr: str):
        if not addr:
            return None
        try:
            return await myplace.lookup_address(addr)
        except Exception as e:
            logger.warning("MyPlace lookup failed for %s: %s", addr, e)
            return None

    lookups = await asyncio.gather(
        safe_lookup(subject_line1),
        *[safe_lookup(a) for a in comp_line1s],
    )
    subject_county = lookups[0]
    comp_counties = lookups[1:]

    result = arv.finalize(
        payload=payload,
        subject_county=subject_county,
        ranked_comps=ranked_comps,
        comp_county_records=comp_counties,
        pre_dropped=pre_dropped,
    )

    return templates.TemplateResponse(
        "result.html",
        {
            "request": request,
            "zillow_url": zillow_url,
            "address": address,
            "result": result,
        },
    )
