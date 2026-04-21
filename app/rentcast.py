import os
from typing import Any

import httpx


BASE = "https://api.rentcast.io/v1"


class RentcastError(RuntimeError):
    pass


async def value_estimate(address: str) -> dict[str, Any]:
    """Fetch Rentcast AVM value estimate + comparable sales for an address."""
    key = os.environ.get("RENTCAST_API_KEY")
    if not key:
        raise RentcastError("RENTCAST_API_KEY not set")

    async with httpx.AsyncClient(timeout=20.0) as client:
        r = await client.get(
            f"{BASE}/avm/value",
            params={"address": address},
            headers={"X-Api-Key": key, "Accept": "application/json"},
        )
    if r.status_code == 404:
        raise RentcastError(f"Property not found by Rentcast: {address}")
    if r.status_code >= 400:
        raise RentcastError(f"Rentcast error {r.status_code}: {r.text[:200]}")
    return r.json()
