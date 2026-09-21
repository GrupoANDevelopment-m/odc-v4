"""OSINT tools that require API keys (optional, gracefully degrade).

These are layered on top of the no-key tools in `tools.py`. Each one
checks for an env-var API key first; without one it returns a
structured error so the LLM knows the data isn't reachable.

Keys (all optional, all free-tier):
  OPENSANCTIONS_API_KEY  → OpenSanctions (sanctions search)
  N2YO_API_KEY           → N2YO (live satellite tracking)
  NASA_FIRMS_MAP_KEY     → NASA FIRMS (active fire hotspots)
  EONET_API_KEY          → NASA EONET (events; works without key but flaky)
  GDELT_TOKEN            → GDELT (not strictly required for some endpoints)

Without keys, tools return: {"error": "API key required", "env": "..."}.
With keys, tools hit the real APIs.
"""
from __future__ import annotations

import os
import urllib.parse
from typing import Any

from odc.tools.base import tool
from odc.osint.tools import _http_json, _http_text


def _key_or_error(env_var: str, hint: str = "") -> dict[str, Any]:
    """Return key if present, else a structured error dict."""
    k = os.environ.get(env_var, "").strip()
    if not k:
        return {
            "error": f"API key required: set {env_var} env var",
            "env": env_var,
            "hint": hint or f"Sign up for free at the provider's site",
            "_available": False,
        }
    return {"_key": k}


# ═══════════════════════════════════════════════════════════════════════
# 9. SANCTIONS — OpenSanctions (person / org / vessel)
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.sanctions",
    description=(
        "Sanctions search via OpenSanctions. Search persons, organizations, "
        "vessels against OFAC SDN, EU CFSP, UN, and 200+ other lists. "
        "Requires OPENSANCTIONS_API_KEY (free at opensanctions.org)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Name to search"},
            "limit": {"type": "integer", "description": "Max results (default 5)"},
            "datasets": {"type": "string", "description": "Comma-separated dataset ids (default us_ofac_sdn)"},
        },
    },
)
async def sanctions(query: str, limit: int = 5, datasets: str = "us_ofac_sdn") -> dict:
    k = _key_or_error("OPENSANCTIONS_API_KEY", "https://www.opensanctions.org/")
    if "error" in k:
        return k
    res = _http_json("https://api.opensanctions.org/search/default",
                     {"q": query, "limit": limit, "datasets": datasets})
    hits = []
    for r in res["data"].get("results", []):
        hits.append({
            "id": r.get("id"),
            "caption": r.get("caption"),
            "schema": r.get("schema"),
            "datasets": r.get("datasets"),
            "score": r.get("score"),
            "countries": r.get("countries"),
        })
    return {"source": "opensanctions.org", "query": query, "count": len(hits), "hits": hits,
            "_fetch_ms": res["_ms"]}


# ═══════════════════════════════════════════════════════════════════════
# 10. SATELLITES — N2YO (live position above a location)
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.satellites",
    description=(
        "Live satellite positions via N2YO. Pass a NORAD id (e.g. 25544=ISS, "
        "48274=Starlink). Requires N2YO_API_KEY (free at n2yo.com)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "norad_id": {"type": "integer", "description": "NORAD catalog id (default 25544 = ISS)"},
            "latitude": {"type": "number", "description": "Observer latitude (default 0)"},
            "longitude": {"type": "number", "description": "Observer longitude (default 0)"},
            "altitude_m": {"type": "number", "description": "Observer altitude (default 0)"},
        },
    },
)
async def satellites(norad_id: int = 25544, latitude: float = 0,
                     longitude: float = 0, altitude_m: float = 0) -> dict:
    k = _key_or_error("N2YO_API_KEY", "https://www.n2yo.com/")
    if "error" in k:
        return k
    url = (f"https://api.n2yo.com/rest/v1/satellite/above/{latitude}/{longitude}/"
           f"{altitude_m}/90/1&apiKey={k['_key']}")
    res = _http_json(url)
    above = res["data"].get("above", [])
    return {"source": "n2yo.com", "norad_id": norad_id,
            "observer": {"lat": latitude, "lon": longitude, "alt_m": altitude_m},
            "count": len(above),
            "satellites": [{"satid": s["satid"], "satname": s["satname"],
                            "alt": s["elevation"], "az": s["azimuth"],
                            "dir": s["direction"], "dist_km": s["distance"]} for s in above]}


# ═════════─────────────────────────────────────────────────────────────────
# 11. FIRES — NASA FIRMS (active fire hotspots)
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.fires",
    description=(
        "Active fire hotspots via NASA FIRMS. Returns lat/lon/ brightness/ "
        "confidence for each detection. Requires NASA_FIRMS_MAP_KEY (free "
        "at firms.modaps.eosdis.nasa.gov)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "bbox": {"type": "string", "description": "'west,south,east,north' bbox (default covers Amazon basin)"},
            "days": {"type": "integer", "description": "Days back (1-10, default 1)"},
            "limit": {"type": "integer", "description": "Max fires (default 50)"},
        },
    },
)
async def fires(bbox: str = "-75,-15,-45,5", days: int = 1, limit: int = 50) -> dict:
    k = _key_or_error("NASA_FIRMS_MAP_KEY", "https://firms.modaps.eosdis.nasa.gov/api/")
    if "error" in k:
        return k
    url = f"https://firms.modaps.eosdis.nasa.gov/api/area/csv/{k['_key']}/VIIRS_NOAA20_NRT/{bbox}/{days}"
    try:
        csv_text = _http_text(url)
    except Exception as e:
        return {"error": f"FIRMS request failed: {e}", "_available": False}
    lines = csv_text.strip().split("\n")
    if len(lines) < 2:
        return {"source": "NASA FIRMS", "count": 0, "fires": []}
    headers = lines[0].split(",")
    out = []
    for line in lines[1:1 + limit]:
        cells = line.split(",")
        if len(cells) < len(headers):
            continue
        d = dict(zip(headers, cells))
        out.append({
            "latitude": float(d.get("latitude", 0)),
            "longitude": float(d.get("longitude", 0)),
            "brightness_k": float(d.get("bright_ti4", 0)) if d.get("bright_ti4") else None,
            "confidence_pct": d.get("confidence"),
            "frp_mw": float(d.get("frp", 0)) if d.get("frp") else None,
            "satellite": d.get("satellite"),
            "instrument": d.get("instrument"),
            "acq_datetime": d.get("acq_date") + " " + d.get("acq_time", ""),
        })
    return {"source": "NASA FIRMS", "bbox": bbox, "days": days,
            "count": len(out), "fires": out}


# ═══════════════════════════════════════════════════════════════════════
# 12. EONET — NASA Earth Observatory Natural Event Tracker
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.eonet",
    description=(
        "Natural events from NASA EONET (wildfires, volcanoes, icebergs, "
        "sea/lake ice, drought, etc). Open data; works without a key most "
        "of the time but is flaky. If you have NASA_EONET_API_KEY set, "
        "rate limits improve."
    ),
    parameters={
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["open", "closed"], "description": "Event status (default open)"},
            "limit": {"type": "integer", "description": "Max events (default 20)"},
            "days": {"type": "integer", "description": "Limit to events in last N days (default 60)"},
        },
    },
)
async def eonet(status: str = "open", limit: int = 20, days: int = 60) -> dict:
    res = _http_json("https://eonet.gsfc.nasa.gov/api/v3/events",
                     {"status": status, "limit": limit, "days": days})
    events = []
    for e in res["data"]:
        geom = e.get("geometry", [])
        coords = geom[0] if geom else None
        events.append({
            "id": e.get("id"),
            "title": e.get("title"),
            "description": e.get("description", "")[:200],
            "categories": [c.get("title") for c in e.get("categories", [])],
            "sources": [s.get("url") for s in e.get("sources", [])][:3],
            "last_updated": e.get("updated"),
            "coordinates": coords.get("coordinates") if coords else None,
        })
    return {"source": "eonet.gsfc.nasa.gov", "status": status,
            "count": len(events), "events": events, "_fetch_ms": res["_ms"]}


# ═══════════════════════════════════════════════════════════════════════
# 13. NEWS HEADLINES — GDELT (free, occasionally slow)
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.news",
    description=(
        "Global news via GDELT DOC API. Search articles by query. Returns "
        "title, url, language, source. No API key needed but service is "
        "occasionally slow; will retry once on timeout."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query (default 'climate change')"},
            "max_records": {"type": "integer", "description": "Max articles (default 10)"},
        },
    },
)
async def news(query: str = "climate change", max_records: int = 10) -> dict:
    url = ("https://api.gdeltproject.org/api/v2/doc/doc?query="
           + urllib.parse.quote(query)
           + f"&mode=ArtList&maxrecords={max_records}&format=json")
    last_err = None
    for attempt in range(2):
        try:
            res = _http_json(url)
            articles = res["data"].get("articles", [])
            out = [{
                "title": a.get("title"),
                "url": a.get("url"),
                "language": a.get("language"),
                "source": a.get("domain"),
                "published": a.get("seendate"),
            } for a in articles]
            return {"source": "gdeltproject.org", "query": query,
                    "count": len(out), "articles": out, "_fetch_ms": res["_ms"]}
        except Exception as e:
            last_err = e
    return {"error": f"GDELT unreachable after 2 attempts: {last_err}", "_available": False}


# ═══════════════════════════════════════════════════════════════════════
# Registry
# ═══════════════════════════════════════════════════════════════════════
ALL_KEYED_OSINT_TOOLS = [sanctions, satellites, fires, eonet, news]


def register_all(registry) -> list[str]:
    """Register the keyed OSINT tools. Returns tool names."""
    names = []
    for fn in ALL_KEYED_OSINT_TOOLS:
        registry.register(fn)
        names.append(fn.name)
    return names
