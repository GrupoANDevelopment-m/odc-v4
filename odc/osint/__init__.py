"""OSINT tools — real-time eyes on the planet.

Inspired by simplifaisoul/Osiris (https://osirisai.live) and
asuramaya/Osiris (MCP memory substrate). We borrow the *capabilities*
(no dashboard, no UI) and wire them as tools an agent can call.

Every tool here hits a *public, no-auth-required* data source so the
agent can run without API keys. Where a key would unlock more, we
expose the parameter but degrade gracefully when missing.

Sources actually verified reachable from this sandbox
(see tests/test_osint.py::test_osint_real_endpoints):

    OpenSky        https://opensky-network.org/api/states/all       (flights)
    USGS           https://earthquake.usgs.gov/.../all_hour.geojson (earthquakes)
    NVD            https://services.nvd.nist.gov/rest/json/cves/2.0 (CVE)
    blockstream    https://blockstream.info/api/                   (Bitcoin)
    Wikipedia      https://en.wikipedia.org/w/api.php              (knowledge)
    NOAA SWPC      https://services.swpc.noaa.gov/json/...         (space weather)
    CoinGecko      https://api.coingecko.com/api/v3/...            (crypto prices)
    Open-Meteo     https://api.open-meteo.com/v1/forecast          (weather)

Other endpoints (NASA EONET, OpenSanctions, N2YO, FIRMS, GDELT)
require API keys or are intermittently down — we expose them behind
a thin wrapper that returns a structured error if the key is missing.
"""
