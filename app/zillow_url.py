import re
from urllib.parse import unquote


_PATH_RE = re.compile(r"/homedetails/([^/]+)/\d+_zpid", re.IGNORECASE)


def parse_zillow_url(url: str) -> str:
    """Extract a street address from a Zillow homedetails URL.

    Example:
        https://www.zillow.com/homedetails/9248-Lynnhaven-Rd-Parma-Heights-OH-44130/33578596_zpid/
        -> "9248 Lynnhaven Rd, Parma Heights, OH 44130"
    """
    m = _PATH_RE.search(url)
    if not m:
        raise ValueError(
            "URL does not look like a Zillow homedetails page. "
            "Expected a URL like .../homedetails/<slug>/<zpid>_zpid/"
        )
    slug = unquote(m.group(1))
    parts = slug.split("-")
    if len(parts) < 4:
        raise ValueError(f"Unexpected address slug: {slug}")

    zip_code = parts[-1]
    state = parts[-2]
    if not re.fullmatch(r"\d{5}", zip_code) or not re.fullmatch(r"[A-Z]{2}", state):
        raise ValueError(f"Could not find state+zip at end of slug: {slug}")

    # Street number is first token; find where street ends and city begins.
    # Heuristic: street contains a type token (Rd, St, Ave, ...) somewhere in the middle.
    street_types = {
        "Rd", "St", "Ave", "Blvd", "Dr", "Ln", "Ct", "Pl", "Way", "Cir",
        "Hwy", "Pkwy", "Ter", "Trl", "Loop", "Sq", "Path", "Run", "Oval",
    }
    street_end = None
    for i, tok in enumerate(parts[:-2]):
        if tok in street_types:
            street_end = i
            break
    if street_end is None:
        raise ValueError(f"Could not find street type in slug: {slug}")

    street = " ".join(parts[: street_end + 1])
    city = " ".join(parts[street_end + 1 : -2])
    return f"{street}, {city}, {state} {zip_code}"
