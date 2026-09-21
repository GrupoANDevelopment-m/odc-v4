"""Tests for proactive OSINT tool injection in dynamic prompt."""
import pytest

from odc.prompt.builder import build_layer_2


# Minimal mock tool class for the builder
class MockTool:
    def __init__(self, name: str, description: str = ""):
        self.name = name
        self.description = description


def _all_osint_tools():
    return [
        MockTool("osint.flights", "live flights"),
        MockTool("osint.earthquakes", "earthquakes"),
        MockTool("osint.bitcoin", "bitcoin network"),
        MockTool("osint.crypto_prices", "crypto prices"),
        MockTool("osint.weather", "weather"),
        MockTool("osint.news", "news"),
        MockTool("web.fetch", "fetch URL"),
        MockTool("fs.read", "read file"),
        MockTool("dynamic.tool_create", "create tool"),
        MockTool("tool.discover", "discover tools"),
    ]


# ──────────────────────────────────────────────────────────────────
# Proactive OSINT injection
# ──────────────────────────────────────────────────────────────────
def test_flight_task_injects_flights_tool():
    text, names = build_layer_2("show me flights over Europe", _all_osint_tools())
    assert "osint.flights" in names


def test_earthquake_task_injects_earthquakes_tool():
    text, names = build_layer_2("earthquakes in Japan today", _all_osint_tools())
    assert "osint.earthquakes" in names


def test_bitcoin_task_injects_bitcoin_tool():
    text, names = build_layer_2("current bitcoin block height", _all_osint_tools())
    assert "osint.bitcoin" in names


def test_weather_task_injects_weather_tool():
    text, names = build_layer_2("what is the weather in São Paulo", _all_osint_tools())
    assert "osint.weather" in names


def test_news_task_injects_news_tool():
    text, names = build_layer_2("latest news about climate", _all_osint_tools())
    assert "osint.news" in names


def test_crypto_prices_task_injects_crypto_prices():
    text, names = build_layer_2("bitcoin price right now", _all_osint_tools())
    assert "osint.crypto_prices" in names


def test_non_osint_task_does_not_force_osint_tools():
    text, names = build_layer_2("parse this JSON file", _all_osint_tools())
    # Should NOT include osint.flights (task is about parsing JSON, not flights)
    assert "osint.flights" not in names


# ──────────────────────────────────────────────────────────────────
# Always-included tools still preserved
# ──────────────────────────────────────────────────────────────────
def test_self_extension_tools_always_present():
    text, names = build_layer_2("parse a JSON file", _all_osint_tools())
    assert "dynamic.tool_create" in names
    assert "tool.discover" in names


# ──────────────────────────────────────────────────────────────────
# Multiple OSINT triggers in one task
# ──────────────────────────────────────────────────────────────────
def test_combined_task_injects_multiple_osint():
    text, names = build_layer_2(
        "tell me about flights delayed by weather in Europe",
        _all_osint_tools(),
    )
    assert "osint.flights" in names
    assert "osint.weather" in names


# ──────────────────────────────────────────────────────────────────
# Portuguese keywords work
# ──────────────────────────────────────────────────────────────────
def test_portuguese_keywords_trigger_osint():
    text, names = build_layer_2("qual o preco do bitcoin agora", _all_osint_tools())
    assert "osint.crypto_prices" in names
    text2, names2 = build_layer_2("voos sobre o Brasil", _all_osint_tools())
    assert "osint.flights" in names2
