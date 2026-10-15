"""Phase-2 PnL aggregation tests (fixture CSVs + synthetic)."""

from __future__ import annotations

import sys
from pathlib import Path

TG_ROOT = Path(__file__).resolve().parents[1]
if str(TG_ROOT) not in sys.path:
    sys.path.insert(0, str(TG_ROOT))

from webapp.pnl import (
    build_magic_map,
    build_phase2,
    exec_stats,
    normalize_channel,
    open_positions,
    summarize_closed_pnl,
)


FIXTURE = TG_ROOT / "docs" / "fixtures" / "journal_sample"


class TestNormalize:
    def test_tags(self):
        assert normalize_channel("ORO") == "CH_ORO"
        assert normalize_channel("IT") == "CH_IVAN"
        assert normalize_channel("AS") == "CH_STARK"
        assert normalize_channel("CH_GOLD") == "CH_GOLD"


class TestClosedPnl:
    def test_fixture_ivan_close(self):
        # One CLOSE with realized_profit -24.4 on 2026-07-24 — lookback relative to "now"
        # so we inject the row directly instead of depending on calendar.
        trades = [
            {
                "event": "CLOSE",
                "ts_utc": "2026-07-24T13:02:20Z",
                "close_time_utc": "2026-07-24T13:02:20Z",
                "channel": "IVAN",
                "realized_profit": "-24.4",
                "volume": "0.10",
            },
            {
                "event": "CLOSE",
                "ts_utc": "2026-07-24T14:00:00Z",
                "close_time_utc": "2026-07-24T14:00:00Z",
                "channel": "ORO",
                "realized_profit": "12.5",
                "volume": "0.20",
            },
        ]
        # Force "today" by using close_time = now-ish — summarize uses real now.
        # Instead assert structure with a patched set of windows by using recent timestamps.
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        trades[0]["close_time_utc"] = now
        trades[0]["ts_utc"] = now
        trades[1]["close_time_utc"] = now
        trades[1]["ts_utc"] = now

        out = summarize_closed_pnl(trades, [1, 7, 30])
        assert out["totals"]["d1"]["trades"] == 2
        assert out["totals"]["d1"]["pnl"] == round(-24.4 + 12.5, 2)
        assert out["by_channel"]["CH_IVAN"]["d1"]["pnl"] == -24.4
        assert out["by_channel"]["CH_ORO"]["d1"]["wins"] == 1
        assert out["by_channel"]["CH_IVAN"]["d1"]["losses"] == 1


class TestOpenPositions:
    def test_open_without_close(self):
        trades = [
            {
                "event": "OPEN",
                "ticket": "111",
                "channel": "ORO",
                "symbol": "XAUUSD",
                "direction": "BUY",
                "volume": "0.20",
                "fill": "4042",
                "req_sl": "4038",
                "req_tp": "4048",
                "signal_id": "abc",
                "open_time_utc": "2026-07-28T06:30:00Z",
                "ts_utc": "2026-07-28T06:30:00Z",
            },
            {
                "event": "OPEN",
                "ticket": "222",
                "channel": "GOLD",
                "symbol": "XAUUSD",
                "direction": "SELL",
                "volume": "0.10",
                "fill": "4050",
                "req_sl": "4055",
                "req_tp": "4040",
                "signal_id": "def",
                "open_time_utc": "2026-07-28T12:00:00Z",
                "ts_utc": "2026-07-28T12:00:00Z",
            },
            {
                "event": "CLOSE",
                "ticket": "222",
                "channel": "GOLD",
                "realized_profit": "5",
                "ts_utc": "2026-07-28T13:00:00Z",
                "close_time_utc": "2026-07-28T13:00:00Z",
            },
        ]
        opens = open_positions(trades)
        assert len(opens) == 1
        assert opens[0]["ticket"] == "111"
        assert opens[0]["channel"] == "CH_ORO"


class TestExecStats:
    def test_executed_vs_cancelled(self):
        rows = [
            {"channel_id": "CH_ORO", "status": "EXECUTED_DIRECT"},
            {"channel_id": "CH_ORO", "status": "CANCELLED_RANGE"},
            {"channel_id": "ORO", "status": "EXECUTED_OPEN_NOW"},
            {"channel_id": "CH_GOLD", "status": "CANCELLED_RANGE"},
        ]
        out = exec_stats(rows)
        assert out["by_channel"]["CH_ORO"]["executed"] == 2
        assert out["by_channel"]["CH_ORO"]["cancelled"] == 1
        assert out["totals"]["cancelled"] == 2


class TestBuildPhase2:
    def test_from_fixture_journal(self):
        out = build_phase2(
            ea_journal_dir=str(FIXTURE),
            signal_stats_path=None,
            lookback_days=3650,  # include 2026-07-24 fixture regardless of "today"
            equity_days=3650,
        )
        assert out["sources"]["trades_rows"] >= 2
        assert out["sources"]["equity_rows"] >= 2
        assert out["equity"]["latest"] is not None
        assert out["equity"]["latest"]["equity"] == 9975.0
        # OPEN then CLOSE on same ticket → no open positions
        assert out["open_positions"] == []


class TestMagicMap:
    CHANNELS = [
        {"id": "CH_GOLD", "magic_base": 12000},
        {"id": "CH_FOREX", "magic_base": 13000},
        {"id": "CH_STARK", "magic_base": 14000},
        {"id": "CH_IVAN", "magic_base": 17000},
        {"id": "CH_IVANBTC", "magic_base": 18000},
        {"id": "CH_NOMAGIC"},
    ]

    def test_build_skips_channels_without_magic(self):
        m = build_magic_map(self.CHANNELS)
        assert m == {
            12000: "CH_GOLD",
            13000: "CH_FOREX",
            14000: "CH_STARK",
            17000: "CH_IVAN",
            18000: "CH_IVANBTC",
        }

    def test_magic_wins_over_tag(self):
        m = build_magic_map(self.CHANNELS)
        # tp_index 1..3 sit on top of magic_base
        assert normalize_channel("IT", "18003", m) == "CH_IVANBTC"
        assert normalize_channel("GOLD", 12002, m) == "CH_GOLD"
        assert normalize_channel("", "14001.0", m) == "CH_STARK"

    def test_unknown_magic_falls_back_to_tag(self):
        m = build_magic_map(self.CHANNELS)
        assert normalize_channel("IT", "99001", m) == "CH_IVAN"
        assert normalize_channel("AS", None, m) == "CH_STARK"


class TestNormalizedPnl:
    """P&L riportato a 0.01 lotti per segnale e pips, per classifica omogenea."""

    @staticmethod
    def _close(sig, tp, symbol, direction, vol, fill, close, profit, magic):
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "event": "CLOSE", "ts_utc": now, "close_time_utc": now, "signal_id": sig,
            "tp_index": str(tp), "symbol": symbol, "direction": direction,
            "volume": str(vol), "fill": str(fill), "close_price": str(close),
            "realized_profit": str(profit), "magic": str(magic),
        }

    def test_pip_size_and_pips(self):
        from webapp.pnl import pip_size, trade_pips
        assert pip_size("XAUUSD") == 0.1
        assert pip_size("XAUUSD.ago") == 0.1
        assert pip_size("GBPJPY") == 0.01
        assert pip_size("EURUSD") == 0.0001
        assert pip_size("BTCUSD") == 0.1
        row = {"symbol": "XAUUSD", "direction": "SELL", "fill": "4176.05", "close_price": "4171.18"}
        assert round(trade_pips(row), 1) == 48.7

    def test_btc_100k_points_is_10_usd_at_001(self):
        # 100.000 point BTC (1000 $ di prezzo) = 10.000 pips; a 0.01 lotti = 10 $.
        rows = [self._close("b1", 1, "BTCUSD", "BUY", 0.01, 60000, 61000, 10.0, 18001)]
        out = summarize_closed_pnl(rows, [1, 30], {18000: "CH_IVANBTC"})
        b = out["by_channel"]["CH_IVANBTC"]["d30"]
        assert b["pips"] == 10000.0
        assert b["pnl_norm"] == 10.0
        assert b["signals"] == 1

    def test_multi_tp_signal_normalized_by_total_volume(self):
        # IVAN: 4 TP da 0.10 (0.40 totali) -> P&L a 0.01 = reale / 40.
        rows = [
            self._close("s1", i, "XAUUSD", "SELL", 0.10, 4184.0, 4180.0, 40.0, 17000 + i)
            for i in range(1, 5)
        ]
        rows.append(self._close("s2", 1, "XAUUSD", "SELL", 0.20, 4185.0, 4189.0, -80.0, 14001))
        mm = {17000: "CH_IVAN", 14000: "CH_STARK"}
        out = summarize_closed_pnl(rows, [30], mm)
        ivan = out["by_channel"]["CH_IVAN"]["d30"]
        stark = out["by_channel"]["CH_STARK"]["d30"]
        assert ivan["pnl"] == 160.0
        assert ivan["pnl_norm"] == 4.0
        assert ivan["pips"] == 40.0
        assert ivan["trades"] == 4 and ivan["signals"] == 1
        assert ivan["signal_win_rate"] == 100.0
        assert stark["pnl_norm"] == -4.0
        assert stark["pips"] == -40.0
        assert out["totals"]["d30"]["pnl_norm"] == 0.0
