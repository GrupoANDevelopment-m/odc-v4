"""Concrete OSINT tools. All HTTP, no deps, no subprocess."""
from __future__ import annotations

import json
import logging
import math
import ssl
import time
import urllib.parse
import urllib.request
from typing import Any

from odc.tools.base import Tool, ToolResult, tool

log = logging.getLogger(__name__)

_UA = "ODC-v4-osint/1.0 (+https://github.com/local/odc-v4)"
_CTX = ssl.create_default_context()
_TIMEOUT = 12  # seconds


def _http_json(url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """GET URL → parsed JSON. Raises on error."""
    if params:
        url = url + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX) as r:
        raw = r.read()
        ms = int((time.time() - t0) * 1000)
    return {"_ms": ms, "_bytes": len(raw), "_url": url, "data": json.loads(raw)}


def _http_text(url: str, params: dict[str, Any] | None = None) -> str:
    if params:
        url = url + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=_TIMEOUT, context=_CTX) as r:
        return r.read().decode("utf-8", "replace")


# ═══════════════════════════════════════════════════════════════════════
# 1. FLIGHTS — OpenSky (real-time aircraft state vectors)
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.flights",
    description=(
        "Live flight tracking via OpenSky Network. Returns aircraft state "
        "vectors (icao24, callsign, country, position, altitude, velocity, "
        "heading) for a bounding box. No API key needed for anonymous "
        "(rate-limited ~10 req/min). Pass lamin/lamax/lomin/lomax to scope."
    ),
    parameters={
        "type": "object",
        "properties": {
            "lamin": {"type": "number", "description": "Min latitude (default 40)"},
            "lomin": {"type": "number", "description": "Min longitude (default -10)"},
            "lamax": {"type": "number", "description": "Max latitude (default 55)"},
            "lomax": {"type": "number", "description": "Max longitude (default 10)"},
        },
    },
)
async def flights(lamin: float = 40, lomin: float = -10, lamax: float = 55, lomax: float = 10) -> dict:
    res = _http_json("https://opensky-network.org/api/states/all",
                     {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax})
    states = res["data"].get("states") or []
    # State vectors are positional arrays per OpenSky spec. Decode first 50.
    # Indexes: 0=icao24 1=callsign 3=origin_country 5=longitude 6=latitude
    #          7=baro_altitude 9=velocity 10=heading 11=on_ground
    decoded = []
    for s in states[:50]:
        if not s:
            continue
        decoded.append({
            "icao24": s[0],
            "callsign": (s[1] or "").strip(),
            "country": s[2],
            "lon": s[5],
            "lat": s[6],
            "altitude_m": s[7],
            "velocity_mps": s[9],
            "heading_deg": s[10],
            "on_ground": s[8],
        })
    return {
        "source": "opensky-network.org",
        "time": res["data"].get("time"),
        "bbox": {"lamin": lamin, "lomin": lomin, "lamax": lamax, "lomax": lomax},
        "count": len(states),
        "showing": len(decoded),
        "aircraft": decoded,
        "_fetch_ms": res["_ms"],
        "_bytes": res["_bytes"],
    }


# ═══════════════════════════════════════════════════════════════════════
# 2. EARTHQUAKES — USGS GeoJSON feed
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.earthquakes",
    description=(
        "Real-time earthquake feed from USGS. Magnitude, location, depth, "
        "tsunami flag, and link to detail page. Feed windows: hour, day, "
        "week, month. Significance filters via minmagnitude."
    ),
    parameters={
        "type": "object",
        "properties": {
            "window": {"type": "string", "enum": ["hour", "day", "week", "month"], "description": "Time window (default hour)"},
            "minmagnitude": {"type": "number", "description": "Min magnitude filter (default 2.5)"},
            "limit": {"type": "integer", "description": "Max results to return (default 20)"},
        },
    },
)
async def earthquakes(window: str = "hour", minmagnitude: float = 2.5, limit: int = 20) -> dict:
    # USGS feed paths only support these specific magnitudes:
    # 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.0 + "significant"
    # Pick the smallest valid magnitude >= minmagnitude so we don't 404.
    _VALID_MAGS = [1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.0]
    mag = _VALID_MAGS[0]
    for m in _VALID_MAGS:
        if m >= minmagnitude:
            mag = m
            break
    feed = f"https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/{mag}_{window}.geojson"
    res = _http_json(feed)
    feats = res["data"].get("features", [])
    out = []
    for f in feats[:limit]:
        p = f["properties"]
        c = f["geometry"]["coordinates"]
        out.append({
            "id": f["id"],
            "mag": p.get("mag"),
            "place": p.get("place"),
            "time_ms": p.get("time"),
            "tsunami": p.get("tsunami"),
            "alert": p.get("alert"),
            "lon": c[0], "lat": c[1], "depth_km": c[2],
            "url": p.get("detail"),
        })
    return {
        "source": "earthquake.usgs.gov",
        "window": window,
        "min_magnitude": minmagnitude,
        "count": len(out),
        "earthquakes": out,
        "_fetch_ms": res["_ms"],
    }


# ═══════════════════════════════════════════════════════════════════════
# 3. CVE — NVD recent vulnerabilities
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.cve",
    description=(
        "Recent CVEs from NVD. CVE id, description, CVSS v3 score, "
        "published date, references. Pass days_back to scope (default 7, "
        "max 120). Pass min_cvss to filter (default 0 = all)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "days_back": {"type": "integer", "description": "How far back to look (default 7)"},
            "min_cvss": {"type": "number", "description": "Min CVSS v3 score filter (default 0 = all)"},
            "limit": {"type": "integer", "description": "Max CVEs to return (default 20)"},
        },
    },
)
async def cve(days_back: int = 7, min_cvss: float = 0.0, limit: int = 20) -> dict:
    # NVD needs ISO 8601 timestamps
    end = int(time.time())
    start = end - days_back * 24 * 3600
    res = _http_json(
        "https://services.nvd.nist.gov/rest/json/cves/2.0",
        {"lastModStartDate": _iso(start), "lastModEndDate": _iso(end), "resultsPerPage": min(limit * 3, 2000)},
    )
    vulns = res["data"].get("vulnerabilities", [])
    out = []
    for v in vulns:
        c = v["cve"]
        metrics = c.get("metrics", {}).get("cvssMetricV31", [])
        score = metrics[0]["cvssData"]["baseScore"] if metrics else None
        if score is not None and score < min_cvss:
            continue
        descs = c.get("descriptions", [])
        en = next((d["value"] for d in descs if d["lang"] == "en"), "")
        refs = [r["url"] for r in c.get("references", [])][:5]
        out.append({
            "id": c["id"],
            "published": c.get("published"),
            "last_modified": c.get("lastModified"),
            "cvss_v3": score,
            "description": en[:400],
            "refs": refs,
        })
        if len(out) >= limit:
            break
    return {
        "source": "services.nvd.nist.gov",
        "days_back": days_back,
        "min_cvss": min_cvss,
        "count": len(out),
        "cves": out,
        "_fetch_ms": res["_ms"],
    }


def _iso(epoch_s: int) -> str:
    import datetime as _dt
    return _dt.datetime.fromtimestamp(epoch_s, tz=_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000")


# ═══════════════════════════════════════════════════════════════════════
# 4. BITCOIN — blockstream mempool & tip
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.bitcoin",
    description=(
        "Bitcoin network state via blockstream.info. Returns tip block "
        "height/hash/timestamp, mempool stats (tx count, vsize, total fee), "
        "and optionally a fee histogram. No API key needed."
    ),
    parameters={
        "type": "object",
        "properties": {
            "with_fees": {"type": "boolean", "description": "Include fee histogram (default true)"},
        },
    },
)
async def bitcoin(with_fees: bool = True) -> dict:
    tip_h = int(_http_text("https://blockstream.info/api/blocks/tip/height").strip())
    # /api/block-height/<h> returns plain text (the block hash), not JSON
    block_hash = _http_text("https://blockstream.info/api/block-height/" + str(tip_h)).strip()
    block_info = _http_json("https://blockstream.info/api/block/" + block_hash)["data"]
    mempool = _http_json("https://blockstream.info/api/mempool")["data"]
    fees = []
    if with_fees:
        f = _http_json("https://blockstream.info/api/v1/fees/recommended")["data"]
        fees = [{"target_blocks": k, "fee_rate_sat_vB": v} for k, v in f.items()]
    return {
        "source": "blockstream.info",
        "tip": {
            "height": block_info.get("height"),
            "hash": block_info.get("id"),
            "timestamp": block_info.get("timestamp"),
            "tx_count": block_info.get("tx_count"),
            "size_bytes": block_info.get("size"),
            "weight": block_info.get("weight"),
        },
        "mempool": {
            "tx_count": mempool.get("count"),
            "vsize": mempool.get("vsize"),
            "total_fee_sat": mempool.get("total_fee"),
            "fee_histogram_count": len(mempool.get("fee_histogram", [])),
        },
        "fees_recommended": fees,
    }


# ═══════════════════════════════════════════════════════════════════════
# 5. CRYPTO PRICES — CoinGecko
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.crypto_prices",
    description=(
        "Live crypto prices vs USD via CoinGecko. Pass comma-separated "
        "coin ids (default 'bitcoin,ethereum,solana'). Returns price, "
        "24h change, market cap."
    ),
    parameters={
        "type": "object",
        "properties": {
            "ids": {"type": "string", "description": "Comma-separated CoinGecko ids (default bitcoin,ethereum,solana)"},
            "vs": {"type": "string", "description": "Quote currency (default usd)"},
        },
    },
)
async def crypto_prices(ids: str = "bitcoin,ethereum,solana", vs: str = "usd") -> dict:
    res = _http_json("https://api.coingecko.com/api/v3/simple/price",
                     {"ids": ids, "vs_currencies": vs,
                      "include_24hr_change": "true",
                      "include_market_cap": "true"})
    out = []
    for cid, vals in res["data"].items():
        out.append({
            "id": cid,
            "price": vals.get(vs),
            "vs": vs,
            "change_24h_pct": vals.get(f"{vs}_24h_change"),
            "market_cap": vals.get(f"{vs}_market_cap"),
        })
    return {
        "source": "api.coingecko.com",
        "vs": vs,
        "count": len(out),
        "prices": out,
        "_fetch_ms": res["_ms"],
    }


# ═══════════════════════════════════════════════════════════════════════
# 6. SPACE WEATHER — NOAA SWPC
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.space_weather",
    description=(
        "Solar/geomagnetic indices from NOAA SWPC. Observed sunspot "
        "numbers, F10.7cm flux, Kp index. Returns last 12 months."
    ),
    parameters={"type": "object", "properties": {}},
)
async def space_weather() -> dict:
    res = _http_json("https://services.swpc.noaa.gov/json/solar-cycle/observed-solar-cycle-indices.json")
    rows = res["data"][-12:] if isinstance(res["data"], list) else []
    return {
        "source": "services.swpc.noaa.gov",
        "rows": rows,
        "latest_ssn": rows[-1].get("ssn") if rows else None,
        "latest_f107": rows[-1].get("f10.7") if rows else None,
        "_fetch_ms": res["_ms"],
    }


# ═══════════════════════════════════════════════════════════════════════
# 7. WEATHER — Open-Meteo
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.weather",
    description=(
        "Live weather from Open-Meteo. No API key. Pass lat/lon and "
        "optional comma-separated `current` vars (default "
        "temperature_2m,wind_speed_10m,relative_humidity_2m)."
    ),
    parameters={
        "type": "object",
        "properties": {
            "latitude": {"type": "number", "description": "Latitude (default -23.5 = São Paulo)"},
            "longitude": {"type": "number", "description": "Longitude (default -46.9 = São Paulo)"},
            "current": {"type": "string", "description": "Comma-separated current variables"},
        },
    },
)
async def weather(latitude: float = -23.5, longitude: float = -46.9,
                  current: str = "temperature_2m,wind_speed_10m,relative_humidity_2m,precipitation") -> dict:
    res = _http_json("https://api.open-meteo.com/v1/forecast",
                     {"latitude": latitude, "longitude": longitude,
                      "current": current, "timezone": "auto"})
    return {
        "source": "api.open-meteo.com",
        "lat": res["data"].get("latitude"),
        "lon": res["data"].get("longitude"),
        "timezone": res["data"].get("timezone"),
        "current": res["data"].get("current"),
        "_fetch_ms": res["_ms"],
    }


# ═══════════════════════════════════════════════════════════════════════
# 8. KNOWLEDGE — Wikipedia recent changes / search
# ═══════════════════════════════════════════════════════════════════════
@tool(
    name="osint.wikipedia",
    description=(
        "Wikipedia knowledge: search by query (returns titles + snippets) "
        "or list recent changes (live edits across all wikis). Both modes "
        "are unauthenticated and rate-limited but practical for an agent."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "If set, search Wikipedia for this query"},
            "limit": {"type": "integer", "description": "Max results (default 5)"},
        },
    },
)
async def wikipedia(query: str | None = None, limit: int = 5) -> dict:
    if query:
        res = _http_json("https://en.wikipedia.org/w/api.php",
                         {"action": "query", "list": "search", "srsearch": query,
                          "srlimit": limit, "format": "json"})
        hits = res["data"].get("query", {}).get("search", [])
        out = [{"title": h["title"], "snippet": h["snippet"], "pageid": h["pageid"]} for h in hits]
        return {"source": "en.wikipedia.org", "mode": "search", "query": query, "hits": out}
    res = _http_json("https://en.wikipedia.org/w/api.php",
                     {"action": "query", "list": "recentchanges", "rcnamespace": 0,
                      "rclimit": limit, "format": "json"})
    rc = res["data"].get("query", {}).get("recentchanges", [])
    out = [{"title": r["title"], "timestamp": r["timestamp"], "user": r["user"],
            "comment": r.get("comment", "")[:200]} for r in rc]
    return {"source": "en.wikipedia.org", "mode": "recent", "changes": out}


# ═══════════════════════════════════════════════════════════════════════
# Registry export
# ═══════════════════════════════════════════════════════════════════════
ALL_OSINT_TOOLS = [flights, earthquakes, cve, bitcoin, crypto_prices,
                   space_weather, weather, wikipedia]


def register_all(registry) -> list[str]:
    """Register all OSINT tools into a ToolRegistry. Returns tool names."""
    names = []
    for fn in ALL_OSINT_TOOLS:
        # The @tool decorator already returns a Tool instance
        registry.register(fn)
        names.append(fn.name)
    return names
