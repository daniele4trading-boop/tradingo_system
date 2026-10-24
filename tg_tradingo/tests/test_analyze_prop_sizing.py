"""Test dell'analizzatore di sizing per conti prop."""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from analyze_prop_sizing import DayCurve, load_equity_day, main, max_scale, replay

INITIAL = 10_000.0
DAILY_LIMIT = 300.0
FLOOR = 9_700.0


def write_equity(path: Path, values: list[float], day: str = "20260803") -> Path:
    target = path / f"equity_{day}.csv"
    with target.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ts_utc", "balance", "equity", "floating_total"])
        for minute, value in enumerate(values):
            writer.writerow([f"2026-08-03T{minute // 60:02d}:{minute % 60:02d}:00Z",
                             values[0], value, 0.0])
    return target


def test_load_equity_day_extracts_worst_and_close(tmp_path):
    path = write_equity(tmp_path, [11000.0, 10900.0, 10750.0, 11100.0])
    day = load_equity_day(str(path))
    assert day.date == "20260803"
    assert day.worst == pytest.approx(-250.0)
    assert day.close == pytest.approx(100.0)
    assert day.worst_at == "2026-08-03T00:02:00Z"


def test_load_equity_day_ignores_empty_file(tmp_path):
    empty = tmp_path / "equity_20260801.csv"
    empty.write_text("ts_utc,balance,equity,floating_total\n", encoding="utf-8")
    assert load_equity_day(str(empty)) is None


def test_replay_scales_linearly_with_the_lot_factor():
    days = [DayCurve("d1", worst=-1000.0, close=-500.0, worst_at="")]
    low_full, _, pnl_full = replay(days, 1.0, INITIAL, DAILY_LIMIT, FLOOR)
    low_half, _, pnl_half = replay(days, 0.5, INITIAL, DAILY_LIMIT, FLOOR)
    assert INITIAL - low_full == pytest.approx(2 * (INITIAL - low_half))
    assert pnl_full == pytest.approx(2 * pnl_half)


def test_replay_flags_the_daily_breach():
    days = [DayCurve("d1", worst=-400.0, close=0.0, worst_at="")]
    _, breach, _ = replay(days, 1.0, INITIAL, DAILY_LIMIT, FLOOR)
    assert breach[0] == "d1"
    assert breach[1] in ("limite giornaliero", "floor statico")


def test_replay_flags_the_floor_breach_on_a_later_day():
    """Due giorni da -200: il daily regge, il floor statico no."""
    days = [
        DayCurve("d1", worst=-200.0, close=-200.0, worst_at=""),
        DayCurve("d2", worst=-200.0, close=-200.0, worst_at=""),
    ]
    _, breach, pnl = replay(days, 1.0, INITIAL, DAILY_LIMIT, FLOOR)
    assert breach[0] == "d2"
    assert breach[1] == "floor statico"
    assert pnl == pytest.approx(-400.0)


def test_replay_without_breach_returns_none():
    days = [DayCurve("d1", worst=-50.0, close=25.0, worst_at="")]
    lowest, breach, pnl = replay(days, 1.0, INITIAL, DAILY_LIMIT, FLOOR)
    assert breach is None
    assert lowest == pytest.approx(9950.0)
    assert pnl == pytest.approx(25.0)


def test_max_scale_finds_the_binding_day():
    """Peggior giorno -1416: il massimo e' 300/1416 = 0.212."""
    days = [
        DayCurve("d1", worst=-100.0, close=50.0, worst_at=""),
        DayCurve("d2", worst=-1416.46, close=-490.91, worst_at=""),
    ]
    k = max_scale(days, INITIAL, DAILY_LIMIT, FLOOR)
    assert k == pytest.approx(300.0 / 1416.46, rel=1e-3)
    assert replay(days, k * 0.999, INITIAL, DAILY_LIMIT, FLOOR)[1] is None
    assert replay(days, k * 1.01, INITIAL, DAILY_LIMIT, FLOOR)[1] is not None


def test_max_scale_is_zero_when_even_tiny_size_breaches():
    """Un giorno che apre gia' sotto il floor non ha size praticabile."""
    days = [DayCurve("d1", worst=-1.0, close=0.0, worst_at="")]
    assert max_scale(days, INITIAL, 0.0, INITIAL) == pytest.approx(0.0, abs=1e-6)


def test_cli_reports_and_exits_zero(tmp_path, capsys):
    write_equity(tmp_path, [10000.0, 9850.0, 10100.0], day="20260803")
    write_equity(tmp_path, [10100.0, 9900.0, 10050.0], day="20260804")
    assert main(["--equity-dir", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert "k massimo teorico" in out
    assert "lotto minimo" in out
    assert "20260803" in out and "20260804" in out


def test_cli_errors_on_empty_directory(tmp_path, capsys):
    assert main(["--equity-dir", str(tmp_path)]) == 1
    assert "Nessun equity" in capsys.readouterr().out


def test_cli_warns_when_min_lot_is_already_too_big(tmp_path, capsys):
    write_equity(tmp_path, [10000.0, 4000.0, 10000.0])
    main(["--equity-dir", str(tmp_path)])
    assert "ATTENZIONE" in capsys.readouterr().out
