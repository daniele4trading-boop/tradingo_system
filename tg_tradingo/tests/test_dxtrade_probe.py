"""Test del probe diagnostico contro la piattaforma DXtrade finta."""

from __future__ import annotations

import pytest

from dxtrade_client import DXTradeClient
from dxtrade_probe import probe, render
from test_dxtrade_client import FakeDXtrade


@pytest.fixture
def fake_platform():
    server = FakeDXtrade()
    base_url = server.start()
    server.base_url = base_url
    try:
        yield server
    finally:
        server.stop()


def _client(base_url: str, account: str | None = "default:demo1") -> DXTradeClient:
    return DXTradeClient(
        base_url=base_url,
        username="user",
        password="pw",
        account=account,
        min_interval_sec=0.0,
        sleep=lambda _s: None,
    )


def test_probe_reports_ok_checks(fake_platform):
    report = probe(_client(fake_platform.base_url), ["XAUUSD", "EURUSD"])
    checks = report["checks"]
    assert checks["login"] == "ok"
    assert checks["users"] == "ok"
    assert checks["metrics"] == "ok"
    assert checks["portfolio"] == "ok"
    assert checks["positions"] == "ok (2)"


def test_probe_resolves_symbols_and_converts_lots(fake_platform):
    report = probe(_client(fake_platform.base_url), ["XAUUSD", "EURUSD"])
    gold = report["instruments"]["XAUUSD"]
    assert gold["symbol"] == "XAU/USD"
    assert gold["lot_size"] == 100
    assert gold["units_per_lot"]["0.1"] == 10
    assert gold["units_per_lot"]["0.2"] == 20

    eur = report["instruments"]["EURUSD"]
    assert eur["symbol"] == "EUR/USD"
    assert eur["units_per_lot"]["0.01"] == 1000


def test_probe_flags_unresolved_symbol(fake_platform):
    report = probe(_client(fake_platform.base_url), ["US30"])
    assert report["instruments"]["US30"]["error"]
    assert any("US30" in w for w in report["warnings"])


def test_probe_detects_hedging_from_stacked_positions(fake_platform):
    report = probe(_client(fake_platform.base_url), [])
    assert "hedging probabile" in report["hedging_hint"]


def test_probe_discovers_account_when_not_configured(fake_platform):
    report = probe(_client(fake_platform.base_url, account=None), [])
    assert report["account"] == "default:demo1"
    assert any("account non configurato" in w for w in report["warnings"])


def test_render_produces_readable_report(fake_platform):
    text = render(probe(_client(fake_platform.base_url), ["XAUUSD"]))
    assert "DXtrade probe" in text
    assert "XAUUSD   -> XAU/USD" in text
    assert "0.1 lot = 10 unità" in text
