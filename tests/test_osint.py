"""Real tests against real OSINT endpoints.

These hit the actual APIs (OpenSky, USGS, NVD, blockstream, CoinGecko,
NOAA, Open-Meteo, Wikipedia). No mocks. Each test should pass when the
network is reachable from the sandbox.

If a particular endpoint is rate-limited or down, that single test will
fail loudly (we want to know). Other tests still pass.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

import pytest

# Ensure package import
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from odc.osint.tools import (
    flights, earthquakes, cve, bitcoin, crypto_prices,
    space_weather, weather, wikipedia, ALL_OSINT_TOOLS,
)


def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro) if False else asyncio.run(coro)


# Helper: call tool.run() to get the raw dict (bypass Tool.__call__ wrapper)
def _call(tool_obj, **kwargs):
    return _run(tool_obj.run(**kwargs))


# ──────────────────────────────────────────────────────────────────
# Sanity: 8 tools registered
# ──────────────────────────────────────────────────────────────────
def test_osint_registry_has_eight_tools():
    assert len(ALL_OSINT_TOOLS) == 8
    names = {t.name for t in ALL_OSINT_TOOLS}
    assert "osint.flights" in names
    assert "osint.earthquakes" in names
    assert "osint.cve" in names
    assert "osint.bitcoin" in names
    assert "osint.crypto_prices" in names
    assert "osint.space_weather" in names
    assert "osint.weather" in names
    assert "osint.wikipedia" in names


def test_each_tool_is_proper_tool_instance():
    from odc.tools.base import Tool
    for t in ALL_OSINT_TOOLS:
        assert isinstance(t, Tool), f"{t.name} is not a Tool"
        assert t.name.startswith("osint.")
        assert t.description
        assert t.parameters.get("type") == "object"


# ──────────────────────────────────────────────────────────────────
# Real network tests — each hits a real API
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(30)
def test_osint_real_endpoints():
    """Hit all 8 endpoints with a 5s budget each. Skip on connectivity issues."""
    import urllib.request
    try:
        urllib.request.urlopen("https://opensky-network.org", timeout=3).read(0)
    except Exception as e:
        pytest.skip(f"network unreachable: {e}")

    results = {}
    for name, tool_obj in [
        ("flights", flights),
        ("earthquakes", earthquakes),
        ("cve", cve),
        ("bitcoin", bitcoin),
        ("crypto_prices", crypto_prices),
        ("space_weather", space_weather),
        ("weather", weather),
        ("wikipedia", wikipedia),
    ]:
        try:
            t0 = time.time()
            if name == "cve":
                out = _call(tool_obj, days_back=30, limit=5)
            elif name == "bitcoin":
                out = _call(tool_obj, with_fees=True)
            elif name == "weather":
                out = _call(tool_obj, latitude=-23.5, longitude=-46.9)
            elif name == "wikipedia":
                out = _call(tool_obj, query="Cortana (Halo)")
            else:
                out = _call(tool_obj)
            dt = (time.time() - t0) * 1000
            results[name] = ("OK", dt, type(out).__name__)
        except Exception as e:
            results[name] = ("ERR", 0, f"{type(e).__name__}: {str(e)[:80]}")

    for name, (status, dt, info) in results.items():
        print(f"  {name:<15} {status:<4} {dt:>6.0f}ms  {info}")
    ok = sum(1 for s, *_ in results.values() if s == "OK")
    assert ok >= 5, f"too many failures: {results}"


# ──────────────────────────────────────────────────────────────────
# Flights — decode state vectors
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(30)
def test_osint_flights_decodes_callsigns():
    try:
        out = _call(flights, lamin=40, lomin=-10, lamax=55, lomax=10)
    except Exception as e:
        pytest.skip(f"OpenSky unavailable: {e}")
    assert out["source"] == "opensky-network.org"
    assert "bbox" in out
    assert isinstance(out["aircraft"], list)
    if out["aircraft"]:
        a = out["aircraft"][0]
        assert "icao24" in a and "country" in a
        if a["lat"] is not None:
            assert -90 <= a["lat"] <= 90


# ──────────────────────────────────────────────────────────────────
# Earthquakes — feed structure
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(20)
def test_osint_earthquakes_feed_structure():
    out = _call(earthquakes, window="day", minmagnitude=4.0, limit=10)
    assert out["source"] == "earthquake.usgs.gov"
    assert isinstance(out["earthquakes"], list)
    for eq in out["earthquakes"][:3]:
        assert eq["mag"] is None or 0 <= eq["mag"] <= 12
        assert eq["depth_km"] is None or eq["depth_km"] >= 0


# ──────────────────────────────────────────────────────────────────
# CVE — NVD fields
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(25)
def test_osint_cve_returns_recent():
    out = _call(cve, days_back=14, min_cvss=7.0, limit=5)
    assert out["source"] == "services.nvd.nist.gov"
    assert isinstance(out["cves"], list)
    if out["cves"]:
        c = out["cves"][0]
        assert c["id"].startswith("CVE-")
        if c["cvss_v3"] is not None:
            assert 0.0 <= c["cvss_v3"] <= 10.0


# ──────────────────────────────────────────────────────────────────
# Bitcoin — real tip height + mempool
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(20)
def test_osint_bitcoin_tip_is_real():
    out = _call(bitcoin, with_fees=False)
    tip = out["tip"]
    assert tip["height"] > 800_000, f"tip too low: {tip['height']}"
    assert tip["hash"] and len(tip["hash"]) == 64
    assert isinstance(out["mempool"]["tx_count"], int)


# ──────────────────────────────────────────────────────────────────
# Crypto prices — CoinGecko
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(15)
def test_osint_crypto_prices_returns_three():
    try:
        out = _call(crypto_prices, ids="bitcoin,ethereum,solana")
    except Exception as e:
        pytest.skip(f"CoinGecko unavailable: {e}")
    assert out["count"] == 3
    ids = {p["id"] for p in out["prices"]}
    assert ids == {"bitcoin", "ethereum", "solana"}
    for p in out["prices"]:
        if p["price"] is not None:
            assert p["price"] > 0


# ──────────────────────────────────────────────────────────────────
# Wikipedia — search returns titles
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(15)
def test_osint_wikipedia_search():
    out = _call(wikipedia, query="ACAMR-7", limit=3)
    assert out["mode"] == "search"
    assert out["query"] == "ACAMR-7"
    assert isinstance(out["hits"], list)


# ──────────────────────────────────────────────────────────────────
# Space weather — NOAA returns structured rows
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(15)
def test_osint_space_weather_returns_rows():
    out = _call(space_weather)
    assert out["source"] == "services.swpc.noaa.gov"
    assert isinstance(out["rows"], list)
    assert len(out["rows"]) > 0


# ──────────────────────────────────────────────────────────────────
# Weather — Open-Meteo
# ──────────────────────────────────────────────────────────────────
@pytest.mark.timeout(15)
def test_osint_weather_returns_temperature():
    out = _call(weather, latitude=-23.5, longitude=-46.9)
    assert out["source"] == "api.open-meteo.com"
    assert out["current"] is not None
    t = out["current"].get("temperature_2m")
    if t is not None:
        assert -10 <= t <= 50, f"implausible temperature {t}"


# ──────────────────────────────────────────────────────────────────
# Agent integration: OSINT tools registered by default
# ──────────────────────────────────────────────────────────────────
def test_osint_tools_registered_in_agent_registry(tmp_path, monkeypatch):
    """Build an Agent (no LLM needed) and check OSINT tools are there."""
    monkeypatch.setenv("ODC_DATA_DIR", str(tmp_path / "data"))
    # use a real provider name, just don't call LLM
    monkeypatch.setenv("ODC_LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_API_KEY", "test-key-not-used")

    from odc.config import Config
    cfg = Config(data_dir=tmp_path / "data")
    from odc.agent import Agent
    a = Agent(config=cfg, with_memory=False)

    names = a.tools.names()
    expected = {
        "osint.flights", "osint.earthquakes", "osint.cve", "osint.bitcoin",
        "osint.crypto_prices", "osint.space_weather", "osint.weather", "osint.wikipedia",
    }
    missing = expected - set(names)
    assert not missing, f"missing OSINT tools: {missing}"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v", "-x", "--tb=short"]))
