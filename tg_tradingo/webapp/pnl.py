"""Phase-2 aggregations: trade PnL, open positions, equity, exec stats.

Reads EA CSV journals (trades / equity) and tradingo_signal_stats.csv.
Channel tags from the EA (ORO, GOLD, IT, AS, …) are normalized to CH_*.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

def build_magic_map(channels_cfg: list[dict]) -> dict[int, str]:
    """Costruisce {magic_prefix: channel_id} da tradingo_config channels."""
    return {
        int(ch["magic_base"]): ch["id"]
        for ch in channels_cfg
        if ch.get("magic_base") is not None and ch.get("id")
    }


def normalize_channel(
    raw: str | None,
    magic: str | int | None = None,
    magic_map: dict[int, str] | None = None,
) -> str:
    """Risolve canale: prima dal magic_number (fonte canonica), poi dal tag stringa."""
    if magic_map and magic is not None:
        try:
            prefix = (int(float(magic)) // 1000) * 1000
            ch = magic_map.get(prefix)
            if ch:
                return ch
        except (ValueError, TypeError):
            pass
    if not raw:
        return "?"
    key = raw.strip().upper()
    if key.startswith("CH_"):
        return key
    _LEGACY = {"ORO": "CH_ORO", "GOLD": "CH_GOLD", "FOREX": "CH_FOREX",
                "STARK": "CH_STARK", "IVAN": "CH_IVAN", "IT": "CH_IVAN", "AS": "CH_STARK"}
    return _LEGACY.get(key, key)


def _parse_ts(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None


def _f(row: dict, *keys: str, default: float = 0.0) -> float:
    for k in keys:
        v = row.get(k)
        if v is None or v == "":
            continue
        try:
            return float(v)
        except (TypeError, ValueError):
            continue
    return default


def _timed_read_csv(path: Path, timeout_sec: float = 5.0) -> list[dict]:
    def read() -> list[dict]:
        if not path.exists():
            return []
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))

    with ThreadPoolExecutor(max_workers=1) as ex:
        fut = ex.submit(read)
        try:
            return fut.result(timeout=timeout_sec)
        except Exception:
            return []


def _day_before(start_date: str | None, day: str) -> bool:
    """True when the YYYYMMDD `day` precedes `start_date`."""
    if not start_date:
        return False
    return day < start_date[:10].replace("-", "")


def _row_before(start_date: str | None, *values: str | None) -> bool:
    """True when the first usable ISO timestamp precedes `start_date`.

    `start_date` is either a day (`2026-08-05`) or an instant
    (`2026-08-05T10:05:00Z`): with an instant the same UTC day is cut at the
    minute, which is what a mid-day account reset needs.
    """
    if not start_date or "T" not in start_date:
        return False
    for v in values:
        if v:
            return v < start_date
    return False


def load_trades_range(
    trades_dir: Path, days: int, start_date: str | None = None
) -> list[dict]:
    """Load trades_YYYYMMDD.csv for the last `days` UTC days (inclusive of today).

    `start_date` (YYYY-MM-DD or full ISO instant) ignores the history before an
    account reset.
    """
    if not trades_dir:
        return []
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    for i in range(days):
        day = (now - timedelta(days=i)).strftime("%Y%m%d")
        if _day_before(start_date, day):
            continue
        rows.extend(
            r
            for r in _timed_read_csv(trades_dir / f"trades_{day}.csv")
            if not _row_before(start_date, r.get("ts_utc"), r.get("open_time_utc"))
        )
    return rows


def load_equity_range(
    equity_dir: Path, days: int, start_date: str | None = None
) -> list[dict]:
    if not equity_dir:
        return []
    now = datetime.now(timezone.utc)
    rows: list[dict] = []
    # chronological: oldest day first
    for i in range(days - 1, -1, -1):
        day = (now - timedelta(days=i)).strftime("%Y%m%d")
        if _day_before(start_date, day):
            continue
        rows.extend(
            r
            for r in _timed_read_csv(equity_dir / f"equity_{day}.csv")
            if not _row_before(start_date, r.get("ts_utc"))
        )
    return rows


def load_signal_stats(path: Path | None) -> list[dict]:
    if not path:
        return []
    return _timed_read_csv(path)


def _empty_bucket() -> dict[str, Any]:
    return {
        "pnl": 0.0,
        "trades": 0,
        "wins": 0,
        "losses": 0,
        "win_rate": None,
        "volume": 0.0,
    }


def _finalize_bucket(b: dict[str, Any]) -> dict[str, Any]:
    n = b["trades"]
    b["pnl"] = round(b["pnl"], 2)
    b["volume"] = round(b["volume"], 2)
    b["win_rate"] = round(100.0 * b["wins"] / n, 1) if n else None
    return b


def summarize_closed_pnl(trades: list[dict], windows_days: list[int], magic_map: dict[int, str] | None = None) -> dict[str, Any]:
    """Per-channel closed PnL for each lookback window (1=today, 7, 30)."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")

    # channel -> window_label -> bucket
    by_ch: dict[str, dict[str, dict]] = defaultdict(
        lambda: {f"d{d}": _empty_bucket() for d in windows_days}
    )
    totals = {f"d{d}": _empty_bucket() for d in windows_days}

    for row in trades:
        ev = (row.get("event") or "").upper()
        if ev and ev != "CLOSE":
            continue
        # skip OPEN-only rows that somehow lack event but have no profit
        pnl = _f(row, "realized_profit", "gross_profit")
        if ev != "CLOSE" and pnl == 0.0 and not row.get("close_time_utc"):
            continue

        ts = _parse_ts(row.get("close_time_utc") or row.get("ts_utc"))
        if ts is None:
            continue
        age_days = (now.date() - ts.date()).days
        ch = normalize_channel(row.get("channel") or row.get("channel_id"), magic=row.get("magic"), magic_map=magic_map)
        vol = _f(row, "volume")

        for d in windows_days:
            if age_days >= d:
                continue
            # "today" window: same UTC calendar day
            if d == 1 and ts.strftime("%Y-%m-%d") != today:
                continue
            label = f"d{d}"
            for bucket in (by_ch[ch][label], totals[label]):
                bucket["pnl"] += pnl
                bucket["trades"] += 1
                bucket["volume"] += vol
                if pnl > 0:
                    bucket["wins"] += 1
                elif pnl < 0:
                    bucket["losses"] += 1

    channels_out = {}
    for ch, windows in by_ch.items():
        channels_out[ch] = {k: _finalize_bucket(v) for k, v in windows.items()}
    return {
        "windows_days": windows_days,
        "totals": {k: _finalize_bucket(v) for k, v in totals.items()},
        "by_channel": channels_out,
    }


def open_positions(trades: list[dict], magic_map: dict[int, str] | None = None) -> list[dict]:
    """OPEN rows whose ticket has no later CLOSE in the loaded set."""
    closed_tickets: set[str] = set()
    opens: dict[str, dict] = {}

    # Process in time order so last OPEN wins if re-opened same ticket (rare)
    def sort_key(r: dict) -> str:
        return r.get("ts_utc") or r.get("open_time_utc") or ""

    for row in sorted(trades, key=sort_key):
        ticket = str(row.get("ticket") or "").strip()
        if not ticket:
            continue
        ev = (row.get("event") or "").upper()
        if ev == "CLOSE":
            closed_tickets.add(ticket)
            opens.pop(ticket, None)
        elif ev == "OPEN" or (not ev and not row.get("close_time_utc")):
            if ticket in closed_tickets:
                # ticket closed then somehow open again — allow
                closed_tickets.discard(ticket)
            opens[ticket] = {
                "ticket": ticket,
                "signal_id": row.get("signal_id") or "",
                "channel": normalize_channel(row.get("channel"), magic=row.get("magic"), magic_map=magic_map),
                "symbol": row.get("symbol") or "",
                "direction": row.get("direction") or "",
                "volume": _f(row, "volume"),
                "fill": _f(row, "fill"),
                "sl": _f(row, "req_sl"),
                "tp": _f(row, "req_tp"),
                "open_time_utc": row.get("open_time_utc") or row.get("ts_utc"),
                "magic": row.get("magic") or "",
                "tp_index": row.get("tp_index") or "",
            }
    return sorted(opens.values(), key=lambda x: x.get("open_time_utc") or "", reverse=True)


def equity_series(rows: list[dict], max_points: int = 96) -> dict[str, Any]:
    """Downsample equity CSV rows for a simple sparkline / polyline."""
    if not rows:
        return {"points": [], "latest": None, "delta_today": None}

    points_raw = []
    for r in rows:
        ts = r.get("ts_utc")
        try:
            eq = float(r.get("equity") or 0)
            bal = float(r.get("balance") or 0)
            floating = float(r.get("floating_total") or 0)
            opens = int(float(r.get("open_positions_count") or 0))
        except (TypeError, ValueError):
            continue
        if not ts:
            continue
        points_raw.append(
            {
                "ts_utc": ts,
                "equity": round(eq, 2),
                "balance": round(bal, 2),
                "floating": round(floating, 2),
                "open_positions": opens,
            }
        )

    if not points_raw:
        return {"points": [], "latest": None, "delta_today": None}

    # downsample evenly
    n = len(points_raw)
    if n <= max_points:
        points = points_raw
    else:
        step = n / max_points
        idxs = sorted({min(n - 1, int(i * step)) for i in range(max_points)})
        if idxs[-1] != n - 1:
            idxs.append(n - 1)
        points = [points_raw[i] for i in idxs]

    latest = points_raw[-1]
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    first_today = next((p for p in points_raw if (p["ts_utc"] or "").startswith(today)), None)
    delta = None
    if first_today is not None:
        delta = round(latest["equity"] - first_today["equity"], 2)

    return {"points": points, "latest": latest, "delta_today": delta}


def exec_stats(stats_rows: list[dict], magic_map: dict[int, str] | None = None) -> dict[str, Any]:
    """Aggregate tradingo_signal_stats.csv by channel / status."""
    by_ch: dict[str, dict[str, int]] = defaultdict(
        lambda: {"executed": 0, "cancelled": 0, "other": 0}
    )
    totals = {"executed": 0, "cancelled": 0, "other": 0}

    for r in stats_rows:
        ch = normalize_channel(r.get("channel_id") or r.get("channel"), magic=r.get("magic"), magic_map=magic_map)
        status = (r.get("status") or "").upper()
        if "CANCEL" in status:
            key = "cancelled"
        elif "EXECUT" in status or status.startswith("OPEN") or "DIRECT" in status:
            key = "executed"
        else:
            key = "other"
        by_ch[ch][key] += 1
        totals[key] += 1

    return {"totals": totals, "by_channel": dict(by_ch)}


def build_phase2(
    *,
    ea_journal_dir: str | None,
    signal_stats_path: str | None = None,
    lookback_days: int = 30,
    equity_days: int = 3,
    start_date: str | None = None,
    magic_map: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Full Phase-2 payload for the dashboard API."""
    root = Path(ea_journal_dir) if ea_journal_dir else None
    trades_dir = (root / "trades") if root else None
    equity_dir = (root / "equity") if root else None

    if signal_stats_path:
        stats_path = Path(signal_stats_path)
    elif root is not None:
        # journal is under MQL5\Files\journal → stats sits in Files\
        stats_path = root.parent / "tradingo_signal_stats.csv"
    else:
        stats_path = None

    trades = (
        load_trades_range(trades_dir, lookback_days, start_date) if trades_dir else []
    )
    # For open positions we need enough history that an OPEN from days ago still shows
    equity_rows = (
        load_equity_range(equity_dir, equity_days, start_date) if equity_dir else []
    )
    stats_rows = load_signal_stats(stats_path)

    return {
        "pnl": summarize_closed_pnl(trades, windows_days=[1, 7, 30], magic_map=magic_map),
        "open_positions": open_positions(trades, magic_map=magic_map),
        "equity": equity_series(equity_rows),
        "exec": exec_stats(stats_rows, magic_map=magic_map),
        "sources": {
            "trades_dir": str(trades_dir) if trades_dir else None,
            "equity_dir": str(equity_dir) if equity_dir else None,
            "signal_stats": str(stats_path) if stats_path else None,
            "trades_rows": len(trades),
            "equity_rows": len(equity_rows),
            "stats_rows": len(stats_rows),
            "start_date": start_date,
        },
    }
