"""Report IVAN VIP: segnale Telegram vs esecuzione reale sui terminali MT5.

Ricostruisce ogni setup del canale CH_IVAN (XAUUSD, magic 17001-17004 = TP1-TP4)
dagli eventi del bridge e lo confronta con i deal esportati da ogni terminale.
Produce un CSV per setup, un CSV per leg (posizione), un CSV di statistiche e
un report Markdown con discrepanze e statistiche (per broker, aggregate e
"teoriche" del segnale).

Sola lettura, nessuna dipendenza oltre la stdlib.

Input
-----
--events DIR|FILE...   journal bridge `journal/bridge_events/events_YYYYMMDD.jsonl`
--bridge-log FILE...   in alternativa/aggiunta: log `logs/tradingo_*.log` (meno ricco)
--broker NAME=DIR      cartella per terminale con:
                         deals.json   (lista dict da MetaTrader5.history_deals_get, `_asdict()`)
                         o deals.csv  (stesse colonne)
                         tradingo_signal_stats.csv  (opzionale, EA)
                         ea_logs/*.log              (opzionale, log Experts convertiti utf-8)
                         account.json               (opzionale: valuta, login)
--from / --to          intervallo (UTC, inclusivo) sull'ora del messaggio OPEN
--out DIR              cartella di output

Esempio
-------
python tools/ivan_report.py --events export/bridge_events \
    --broker vantage=export/vantage --broker bullwaves=export/bullwaves \
    --from 2026-09-15 --to 2026-09-26 --out report_out
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

UTC = dt.timezone.utc
CHANNEL = "CH_IVAN"
MAGIC_BASE = 17000
MAGIC_MIN, MAGIC_MAX = MAGIC_BASE + 1, MAGIC_BASE + 4
OPEN_ACTIONS = ("OPEN",)
FOLLOWUP_ACTIONS = ("CHECK_AND_BE", "UPDATE_SL", "UPDATE_TP", "UPDATE_OPEN",
                    "CLOSE_ALL_SYMBOL", "CLOSE_SELECTIVE", "CLOSE_HALF_BE",
                    "BREAK_EVEN_PRICE", "CHECK_AND_CLOSE_TP")
COMMENT_RE = re.compile(r"(?:TG-)?I[TV](?:AN)?-T(\d)-([0-9a-f]{6,12})")
TP_HIT_RE = re.compile(r"TP\s*(\d)\s*HIT", re.IGNORECASE)
OUT_PRICE_RE = re.compile(r"\[(tp|sl)\s+([\d.]+)\]", re.IGNORECASE)
BE_TOL = 0.30  # $/oz: chiusura SL entro questa distanza dal fill = break-even
STALE_EDIT_AGE = dt.timedelta(hours=6)  # EDIT di un messaggio più vecchio: non è il setup corrente

try:  # su Windows senza tzdata la zona può mancare: fallback a UTC+2
    from zoneinfo import ZoneInfo

    ROME = ZoneInfo("Europe/Rome")
except Exception:  # noqa: BLE001
    ROME = dt.timezone(dt.timedelta(hours=2))


# ---------------------------------------------------------------------------
# Eventi bridge
# ---------------------------------------------------------------------------

@dataclass
class Event:
    ts: dt.datetime
    sid: str
    action: str            # OPEN / CHECK_AND_BE / ... oppure "" per messaggi non emessi
    event_type: str        # NEW / EDIT
    text: str
    payload: dict
    outcome: str = "EMITTED"
    message_id: int | None = None

    @property
    def emitted(self) -> bool:
        return self.outcome == "EMITTED" and bool(self.action)


def telegram_age(e: Event) -> dt.timedelta | None:
    """Età del messaggio Telegram (payload.telegram_date) al momento dell'evento bridge."""
    td = e.payload.get("telegram_date") if e.payload else None
    if not td:
        return None
    try:
        return e.ts - parse_iso(str(td))
    except ValueError:
        return None


def parse_iso(s: str) -> dt.datetime:
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = dt.datetime.fromisoformat(s)
    if d.tzinfo is None:
        d = d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


def load_events_jsonl(paths: list[Path], channel: str = CHANNEL) -> list[Event]:
    events: list[Event] = []
    for p in paths:
        with p.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("channel_id") != channel:
                    continue
                payload = e.get("payload") or {}
                events.append(Event(
                    ts=parse_iso(e["ts_utc"]),
                    sid=e.get("signal_id") or "",
                    action=e.get("action") or "",
                    event_type=e.get("event_type") or "NEW",
                    text=e.get("raw_text") or "",
                    payload=payload,
                    outcome=e.get("outcome") or "",
                    message_id=e.get("message_id"),
                ))
    events.sort(key=lambda x: x.ts)
    return events


LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+ \[(\w+)\] (.*)$")
LOG_MSG_RE = re.compile(r"^\[CH_IVAN\] (MSG|EDIT): (.*)$")
LOG_SIG_RE = re.compile(r"^\[CH_IVAN\] -> \S+ \| action=(\w+)(?: symbol=(\S+))?(?: dir=(\S*))? sid=(\w+)")
LOG_OPEN_RE = re.compile(
    r"^\[IVAN\] OPEN(?: \((rientro)\))? (BUY|SELL) (\S+) (?:@ (\S+)|range=\[([\d.]+), ([\d.]+)\]) "
    r"TP=\[([^\]]*)\] SL=([\d.]+)(?: lot_factor=(\S+))?")
LOG_BE_RE = re.compile(r"^\[IVAN\] CHECK_AND_BE(?: be_price=([\d.None]+))?")
LOG_SL_RE = re.compile(r"^\[IVAN\] UPDATE_SL \S+ (BUY|SELL) SL=([\d.]+)")
LOG_TP_RE = re.compile(r"^\[IVAN\] UPDATE_TP \S+ (BUY|SELL) TP=([\d.]+)(?: tp_index=(\d+))?")
LOG_CLOSE_RE = re.compile(r"^\[CH_IVAN\] CLOSE_ALL_SYMBOL(?: follow-up)?(?: ref=(\S+)| price=(\S+))?")


def _num(s: str | None) -> float | None:
    if s is None or s in ("None", ""):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def load_events_bridge_log(paths: list[Path], log_utc_offset: float) -> list[Event]:
    """Ricostruisce gli eventi dal log testuale del bridge (ora locale VPS).

    Meno ricco del journal JSONL: il testo del messaggio e' troncato e le EDIT
    dei setup compaiono come nuovi OPEN.
    """
    events: list[Event] = []
    seen_sid: set[str] = set()
    off = dt.timedelta(hours=log_utc_offset)
    for p in paths:
        last_text = ""
        last_type = "NEW"
        pending: dict = {}
        with p.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                m = LOG_LINE_RE.match(raw.rstrip("\n"))
                if not m:
                    continue
                ts = dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC) - off
                body = m.group(3)
                mm = LOG_MSG_RE.match(body)
                if mm:
                    last_type, last_text = mm.group(1), mm.group(2)
                    if last_type == "MSG":
                        last_type = "NEW"
                    events.append(Event(ts, "", "", last_type, last_text, {}, outcome="MSG"))
                    continue
                mo = LOG_OPEN_RE.match(body)
                if mo:
                    tps = [float(x) for x in mo.group(7).split(",") if x.strip()]
                    pending = {"action": "OPEN", "direction": mo.group(2), "symbol": mo.group(3),
                               "entry": _num(mo.group(4)),
                               "entry_range": ([float(mo.group(5)), float(mo.group(6))]
                                               if mo.group(5) else None),
                               "tp_levels": tps, "sl": float(mo.group(8)),
                               "lot_factor": _num(mo.group(9)),
                               "allow_stack": bool(mo.group(1))}
                    continue
                mb = LOG_BE_RE.match(body)
                if mb:
                    pending = {"action": "CHECK_AND_BE", "be_price": _num(mb.group(1))}
                    continue
                ms = LOG_SL_RE.match(body)
                if ms:
                    pending = {"action": "UPDATE_SL", "direction": ms.group(1), "new_sl": float(ms.group(2))}
                    continue
                mt = LOG_TP_RE.match(body)
                if mt:
                    pending = {"action": "UPDATE_TP", "direction": mt.group(1), "new_tp": float(mt.group(2)),
                               "tp_index": int(mt.group(3)) if mt.group(3) else None}
                    continue
                mc = LOG_CLOSE_RE.match(body)
                if mc:
                    pending = {"action": "CLOSE_ALL_SYMBOL",
                               "reference_price": _num(mc.group(1) or mc.group(2))}
                    continue
                mg = LOG_SIG_RE.match(body)
                if mg:
                    sid = mg.group(4)
                    if sid in seen_sid:
                        continue
                    seen_sid.add(sid)
                    action = mg.group(1)
                    payload = dict(pending) if pending.get("action") == action else {"action": action}
                    payload.setdefault("symbol", mg.group(2) or "XAUUSD")
                    if mg.group(3):
                        payload.setdefault("direction", mg.group(3))
                    payload["timestamp"] = ts.strftime("%Y-%m-%dT%H:%M:%SZ")
                    events.append(Event(ts, sid, action, last_type, last_text, payload))
                    pending = {}
    events.sort(key=lambda x: x.ts)
    return events


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------

@dataclass
class Setup:
    sid: str
    ts: dt.datetime
    direction: str
    entry: float | None
    entry_range: list[float] | None
    sl: float
    tps: list[float]
    text: str
    is_reentry: bool = False
    lot_factor: float | None = None
    trades: int = 0
    message_id: int | None = None
    payload_ts: str = ""
    from_edit: bool = False   # aperto da UPDATE_OPEN su EDIT (messaggio completato dopo la pubblicazione)
    edit_age: dt.timedelta | None = None   # età del messaggio Telegram al momento dell'EDIT
    followups: list[Event] = field(default_factory=list)
    sids: set[str] = field(default_factory=set)
    claims: list[str] = field(default_factory=list)
    final_sl: float | None = None
    final_tps: list[float] = field(default_factory=list)
    end_ts: dt.datetime | None = None
    n_be: int = 0
    n_close: int = 0
    tp_update_ts: dt.datetime | None = None

    @property
    def stale_edit(self) -> bool:
        return self.from_edit and self.edit_age is not None and self.edit_age > STALE_EDIT_AGE

    @property
    def entry_ref(self) -> float | None:
        if self.entry is not None:
            return self.entry
        if self.entry_range:
            return (self.entry_range[0] + self.entry_range[1]) / 2
        return None

    @property
    def risk_oz(self) -> float | None:
        ref = self.entry_ref
        if ref is None:
            return None
        return abs(ref - self.sl) or None

    @property
    def sign(self) -> int:
        return 1 if self.direction == "BUY" else -1

    @property
    def n_legs(self) -> int:
        return self.trades or max(len(self.tps), 1)

    def label(self) -> str:
        where = (f"{self.entry:g}" if self.entry is not None
                 else (f"{self.entry_range[0]:g}-{self.entry_range[1]:g}" if self.entry_range else "mkt"))
        tag = (" (EDIT stantio)" if self.stale_edit else
               (" (EDIT)" if self.from_edit else (" (rientro)" if self.is_reentry else "")))
        return f"{fmt_local(self.ts)} {self.direction} @{where}{tag}"


def build_setups(events: list[Event]) -> list[Setup]:
    setups: list[Setup] = []
    cur: Setup | None = None
    for e in events:
        if e.emitted and e.payload.get("levels_only"):
            # zona scartata dal bridge (es. refuso "4401-3399"): solo SL/TP sulle posizioni aperte
            if cur is not None:
                cur.followups.append(e)
                cur.final_sl = float(e.payload.get("sl") or cur.final_sl or 0) or cur.final_sl
                if e.payload.get("tp_levels"):
                    cur.final_tps = [float(x) for x in e.payload["tp_levels"]]
                    cur.tp_update_ts = e.ts
            continue
        late_edit = (e.emitted and e.action == "UPDATE_OPEN" and e.payload.get("tp_levels") and
                     (cur is None or (e.message_id is not None and e.message_id != cur.message_id and
                                      e.ts - cur.ts > dt.timedelta(minutes=10))))
        if (e.emitted and e.action in OPEN_ACTIONS) or late_edit:
            p = e.payload
            cur = Setup(sid=e.sid, ts=e.ts, direction=p.get("direction", ""),
                        entry=p.get("entry"), entry_range=p.get("entry_range"),
                        sl=float(p.get("sl") or 0), tps=[float(x) for x in (p.get("tp_levels") or [])],
                        text=e.text, is_reentry=bool(p.get("allow_stack")),
                        lot_factor=p.get("lot_factor"), trades=int(p.get("trades") or 0),
                        message_id=e.message_id, payload_ts=str(p.get("timestamp") or ""),
                        from_edit=bool(late_edit), edit_age=telegram_age(e))
            cur.sids.add(e.sid)
            cur.final_sl = cur.sl
            cur.final_tps = list(cur.tps)
            if setups:
                setups[-1].end_ts = e.ts
            setups.append(cur)
            continue
        if cur is None:
            continue
        if e.emitted:
            cur.followups.append(e)
            cur.sids.add(e.sid)
            p = e.payload
            if e.action == "UPDATE_OPEN":
                if p.get("tp_levels"):
                    cur.final_tps = [float(x) for x in p["tp_levels"]]
                    cur.tp_update_ts = e.ts
                if p.get("sl"):
                    cur.final_sl = float(p["sl"])
            elif e.action == "UPDATE_SL" and p.get("new_sl") is not None:
                cur.final_sl = float(p["new_sl"])
            elif e.action == "UPDATE_TP" and p.get("new_tp") is not None:
                cur.tp_update_ts = e.ts
                idx = p.get("tp_index")
                if idx and 1 <= int(idx) <= len(cur.final_tps):
                    cur.final_tps[int(idx) - 1] = float(p["new_tp"])
                else:
                    cur.final_tps = [float(p["new_tp"])] * len(cur.final_tps)
            elif e.action in ("CHECK_AND_BE", "BREAK_EVEN_PRICE"):
                cur.n_be += 1
            elif e.action.startswith("CLOSE"):
                cur.n_close += 1
        elif e.text and TP_HIT_RE.search(e.text) and e.event_type != "EDIT":
            claim = " ".join(f"TP{m}" for m in TP_HIT_RE.findall(e.text))
            if claim not in cur.claims:
                cur.claims.append(claim)
    return setups


# ---------------------------------------------------------------------------
# Deal MT5
# ---------------------------------------------------------------------------

@dataclass
class Deal:
    ticket: int
    order: int
    time: dt.datetime          # ora server (naive -> UTC etichettata, da correggere con offset)
    type: int                  # 0 buy, 1 sell
    entry: int                 # 0 in, 1 out, 2 inout, 3 out_by
    magic: int
    position_id: int
    volume: float
    price: float
    commission: float
    swap: float
    profit: float
    symbol: str
    comment: str
    reason: int = 0


def _f(v, default=0.0) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _i(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def load_deals(path: Path) -> list[Deal]:
    rows: list[dict]
    if path.suffix.lower() == ".json":
        rows = json.loads(path.read_text(encoding="utf-8"))
    else:
        with path.open(encoding="utf-8", newline="") as fh:
            rows = list(csv.DictReader(fh))
    out: list[Deal] = []
    for r in rows:
        t = r.get("time")
        if isinstance(t, str) and not t.replace(".", "", 1).isdigit():
            when = parse_iso(t)
        else:
            when = dt.datetime.fromtimestamp(_f(t), UTC)
        out.append(Deal(
            ticket=_i(r.get("ticket")), order=_i(r.get("order")), time=when,
            type=_i(r.get("type")), entry=_i(r.get("entry")), magic=_i(r.get("magic")),
            position_id=_i(r.get("position_id")), volume=_f(r.get("volume")),
            price=_f(r.get("price")), commission=_f(r.get("commission")),
            swap=_f(r.get("swap")), profit=_f(r.get("profit")),
            symbol=str(r.get("symbol") or ""), comment=str(r.get("comment") or ""),
            reason=_i(r.get("reason")),
        ))
    out.sort(key=lambda d: (d.time, d.ticket))
    return out


@dataclass
class StatRow:
    when: dt.datetime | None   # ts del segnale (UTC) se presente
    status: str
    direction: str
    signal_entry: float | None
    fill: float | None
    distance_pts: float | None
    tp_index: int
    magic: int


def load_signal_stats(path: Path) -> list[StatRow]:
    rows: list[StatRow] = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8", errors="replace", newline="") as fh:
        for r in csv.reader(fh):
            if len(r) < 10 or r[1] != CHANNEL:
                continue
            if len(r) >= 15:
                when = None
                try:
                    when = parse_iso(r[14])
                except ValueError:
                    pass
                rows.append(StatRow(when, r[5], r[4], _num(r[6]), _num(r[9]), _num(r[11]),
                                    _i(r[12]), _i(r[13])))
            else:  # formato v1: ...,signal_entry,range_lo,range_hi,fill,slippage,status
                rows.append(StatRow(None, r[-1], r[4], _num(r[5]), _num(r[8]), None, 0, 0))
    return rows


EA_LINE_RE = re.compile(r"^\S+\t\d+\t(\d{2}:\d{2}:\d{2})\.\d+\t[^\t]*\t(.*)$")
EA_KEEP_RE = re.compile(
    r"Open failed|not enough money|SIGNAL_CANCELLED|OPEN skipped|MODIFY_SKIPPED|BE_SKIPPED|"
    r"BE_PENDING|BE_AT_SIGNAL_ENTRY|BE_FILL_BETTER|UPDATE_TP ticket|UPDATE_OPEN fill missing|"
    r"KILLSWITCH|DD_GUARD (?!disabled)|CLOSE_ALL|failed|error|invalid stops|market closed|"
    r"AutoTrading disabled|10019|10016|10018|10004|action=")


@dataclass
class EaNote:
    ts: dt.datetime
    text: str


def load_ea_logs(folder: Path, vps_utc_offset: float) -> list[EaNote]:
    notes: list[EaNote] = []
    if not folder.exists():
        return notes
    off = dt.timedelta(hours=vps_utc_offset)
    for p in sorted(folder.glob("*.log")):
        digits = re.sub(r"\D", "", p.stem)[:8]
        if len(digits) != 8:
            continue
        day = dt.datetime.strptime(digits, "%Y%m%d").replace(tzinfo=UTC)
        with p.open(encoding="utf-8", errors="replace") as fh:
            for raw in fh:
                m = EA_LINE_RE.match(raw.rstrip("\n"))
                if not m or not EA_KEEP_RE.search(m.group(2)):
                    continue
                if "HEARTBEAT" in m.group(2):
                    continue
                hh, mm_, ss = (int(x) for x in m.group(1).split(":"))
                ts = day.replace(hour=hh, minute=mm_, second=ss) - off
                text = m.group(2).replace("[TradinGo] ", "").strip()
                notes.append(EaNote(ts, text))
    return notes


@dataclass
class Broker:
    name: str
    deals: list[Deal]
    stats: list[StatRow] = field(default_factory=list)
    ea_notes: list[EaNote] = field(default_factory=list)
    currency: str = ""
    login: str = ""
    offset_hours: float = 0.0
    first_deal: dt.datetime | None = None
    last_deal: dt.datetime | None = None

    @property
    def first_ivan_trace(self) -> dt.datetime | None:
        ts = [r.when for r in self.stats if r.when] + [n.ts for n in self.ea_notes]
        return min(ts) if ts else None


def load_broker(name: str, folder: Path, vps_utc_offset: float) -> Broker:
    deals_path = next((folder / n for n in ("deals.json", "deals.csv") if (folder / n).exists()), None)
    if deals_path is None:
        raise FileNotFoundError(f"{folder}: manca deals.json/deals.csv")
    b = Broker(name=name, deals=load_deals(deals_path))
    b.stats = load_signal_stats(folder / "tradingo_signal_stats.csv")
    b.ea_notes = load_ea_logs(folder / "ea_logs", vps_utc_offset)
    acc = folder / "account.json"
    if acc.exists():
        try:
            info = json.loads(acc.read_text(encoding="utf-8")).get("account") or {}
            b.currency = str(info.get("currency") or "")
            b.login = str(info.get("login") or "")
        except ValueError:
            pass
    if b.deals:
        b.first_deal, b.last_deal = b.deals[0].time, b.deals[-1].time
    return b


def infer_server_offset(broker: Broker, events_by_sid: dict[str, Event]) -> float:
    """Ore di scarto tra ora server del broker e UTC, dai deal di apertura con sid nel commento."""
    diffs: list[float] = []
    for d in broker.deals:
        if d.entry != 0:
            continue
        m = COMMENT_RE.search(d.comment)
        if not m:
            continue
        ev = events_by_sid.get(m.group(2))
        if ev is None:
            continue
        diffs.append((d.time - ev.ts).total_seconds() / 3600)
    if not diffs:
        return 0.0
    return float(round(statistics.median(diffs)))


# ---------------------------------------------------------------------------
# Leg = posizione MT5 (uno split del setup)
# ---------------------------------------------------------------------------

@dataclass
class Leg:
    broker: str
    setup_sid: str
    sid: str
    tp_index: int
    position_id: int
    direction: str
    volume: float
    fill: float
    fill_ts: dt.datetime
    close_price: float | None
    close_ts: dt.datetime | None
    outcome: str            # TP / SL / BE_SL / MANUAL / EA_CLOSE / OPEN
    close_magic: int | None
    pnl_ccy: float
    pnl_oz: float | None
    close_comment: str = ""

    @property
    def closed(self) -> bool:
        return self.close_ts is not None


# ENUM_DEAL_REASON MT5
REASON_MANUAL = {0: "desktop", 1: "mobile", 2: "web"}
REASON_EXPERT, REASON_SL, REASON_TP, REASON_SO = 3, 4, 5, 6


def classify_close(direction: str, fill: float, out: Deal) -> str:
    """Esito di una chiusura: usa DEAL_REASON quando disponibile, altrimenti commento/magic."""
    sign = 1 if direction == "BUY" else -1
    gain = (out.price - fill) * sign
    m = OUT_PRICE_RE.search(out.comment)
    kind = m.group(1).lower() if m else ""
    if out.reason in REASON_MANUAL or (out.magic == 0 and out.reason != REASON_EXPERT and not kind):
        return "MANUAL"
    if out.reason == REASON_TP or kind == "tp":
        return "TP"
    if out.reason == REASON_SL or kind == "sl":
        return "BE_SL" if gain >= -BE_TOL else "SL"
    if out.reason == REASON_SO:
        return "STOP_OUT"
    return "EA_CLOSE"


def build_legs(broker: Broker, setups: list[Setup], offset_hours: float) -> list[Leg]:
    off = dt.timedelta(hours=offset_hours)
    by_pos: dict[int, list[Deal]] = defaultdict(list)
    ivan_pos: set[int] = set()
    for d in broker.deals:
        if not d.symbol.upper().startswith(("XAU", "GOLD")):
            continue
        by_pos[d.position_id].append(d)
        if MAGIC_MIN <= d.magic <= MAGIC_MAX:
            ivan_pos.add(d.position_id)
    sid_to_setup: dict[str, Setup] = {}
    for s in setups:
        for sid in s.sids:
            sid_to_setup[sid] = s
    legs: list[Leg] = []
    for pid in sorted(ivan_pos, key=lambda p: by_pos[p][0].time):
        deals = by_pos[pid]
        ins = [d for d in deals if d.entry == 0]
        outs = [d for d in deals if d.entry in (1, 3)]
        if not ins:
            continue
        d_in = ins[0]
        direction = "BUY" if d_in.type == 0 else "SELL"
        m = COMMENT_RE.search(d_in.comment)
        sid = m.group(2) if m else ""
        tp_index = int(m.group(1)) if m else (d_in.magic - MAGIC_BASE)
        fill_ts = d_in.time - off
        setup = sid_to_setup.get(sid)
        if setup is None:  # fallback temporale: ultimo setup emesso prima del fill
            cands = [s for s in setups if s.ts <= fill_ts + dt.timedelta(seconds=5)]
            setup = cands[-1] if cands else None
        pnl_ccy = sum(d.profit + d.commission + d.swap for d in deals)
        if outs:
            vol = sum(d.volume for d in outs) or 1.0
            close_price = sum(d.price * d.volume for d in outs) / vol
            last = outs[-1]
            outcome = classify_close(direction, d_in.price, last)
            close_ts = last.time - off
            close_magic = last.magic
            pnl_oz = (close_price - d_in.price) * (1 if direction == "BUY" else -1)
            comment = last.comment
            if outcome == "MANUAL":
                comment = f"manuale ({REASON_MANUAL.get(last.reason, 'magic 0')})"
        else:
            close_price, close_ts, outcome, close_magic, pnl_oz, comment = None, None, "OPEN", None, None, ""
        legs.append(Leg(broker.name, setup.sid if setup else "", sid, tp_index, pid, direction,
                        d_in.volume, d_in.price, fill_ts, close_price, close_ts, outcome,
                        close_magic, round(pnl_ccy, 2), pnl_oz, comment))
    return legs


# ---------------------------------------------------------------------------
# Motivi "non aperto"
# ---------------------------------------------------------------------------

def missing_reason(setup: Setup, tp_index: int, broker: Broker, next_ts: dt.datetime | None) -> str:
    win_lo = setup.ts - dt.timedelta(seconds=10)
    win_hi = setup.ts + dt.timedelta(minutes=3)
    first = broker.first_ivan_trace
    if first is not None and setup.ts < first - dt.timedelta(minutes=5):
        return f"EA IVAN non attivo sul terminale (prima traccia {fmt_local(first)})"
    if broker.ea_notes and setup.payload_ts:
        day_notes = [n for n in broker.ea_notes if n.ts.date() == setup.ts.date()]
        seen = any("action=" in n.text and setup.payload_ts in n.text for n in day_notes)
        if day_notes and not seen:
            busy = [n for n in day_notes if setup.ts - dt.timedelta(minutes=5) <= n.ts <= setup.ts + dt.timedelta(minutes=5)
                    and "action=" in n.text]
            what = busy[-1].text.split("action=")[1].split()[0] if busy else "n/d"
            return f"OPEN mai letto dall'EA (file segnale sovrascritto; ultima azione letta: {what})"
    for r in broker.stats:
        if r.when is None or not r.status.startswith("CANCELLED"):
            continue
        if win_lo <= r.when <= win_hi and (r.tp_index in (0, tp_index)):
            extra = ""
            if r.distance_pts is not None and r.status in ("CANCELLED_RANGE", "CANCELLED_REENTRY_DRIFT"):
                extra = f" (prezzo {r.fill:g}, distanza {r.distance_pts:g} pt)" if r.fill else \
                    f" (distanza {r.distance_pts:g} pt)"
            return f"{r.status}{extra}"
    notes = [n for n in broker.ea_notes if win_lo <= n.ts <= win_hi]
    for n in notes:
        low = n.text.lower()
        if "not enough money" in low or "10019" in low:
            return "not enough money (err 10019)"
        if "open skipped" in low:
            return "OPEN skipped: posizioni gia' aperte, EA ha modificato SL/TP (allow_stack=false)"
        if "signal_cancelled" in low:
            return "SIGNAL_CANCELLED (fuori tolleranza)"
        if "open failed" in low:
            return n.text[:80]
    if broker.first_deal is None or setup.ts + dt.timedelta(hours=1) < broker.first_deal - dt.timedelta(hours=broker.offset_hours):
        return "terminale non ancora attivo (nessun deal in quel periodo)"
    if broker.last_deal is not None and setup.ts > broker.last_deal - dt.timedelta(hours=broker.offset_hours) + dt.timedelta(hours=6):
        return "export deal terminato prima del setup (storico non disponibile)"
    if not broker.stats and not broker.ea_notes:
        return "nessuna traccia (stats/log EA non forniti)"
    return "nessuna traccia nei log EA"


# ---------------------------------------------------------------------------
# Statistiche
# ---------------------------------------------------------------------------

def fmt_local(ts: dt.datetime | None) -> str:
    if ts is None:
        return ""
    return ts.astimezone(ROME).strftime("%d/%m %H:%M")


def fmt_utc(ts: dt.datetime | None) -> str:
    return ts.strftime("%Y-%m-%d %H:%M:%S") if ts else ""


def fnum(v: float | None, nd: int = 2) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "n/d"
    return f"{v:.{nd}f}"


def drawdown(series: list[tuple[dt.datetime, float]]) -> tuple[float, float, str]:
    """(max drawdown, peggior drawdown intra-giornata, giorno) su una curva di P&L cumulato."""
    peak = cum = 0.0
    max_dd = 0.0
    daily_peak: dict[dt.date, float] = {}
    daily_dd: dict[dt.date, float] = {}
    for ts, pnl in sorted(series, key=lambda x: x[0]):
        cum += pnl
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
        day = ts.astimezone(ROME).date()
        if day not in daily_peak:
            daily_peak[day] = cum - pnl
            daily_dd[day] = 0.0
        daily_peak[day] = max(daily_peak[day], cum)
        daily_dd[day] = max(daily_dd[day], daily_peak[day] - cum)
    worst_day = max(daily_dd.items(), key=lambda kv: kv[1]) if daily_dd else (None, 0.0)
    return max_dd, worst_day[1], (worst_day[0].isoformat() if worst_day[0] else "")


def compute_stats(name: str, setups: list[Setup], legs: list[Leg], unit: str) -> dict:
    """Statistiche di un insieme di leg (un broker, l'aggregato o il teorico)."""
    closed = [lg for lg in legs if lg.closed]
    by_setup: dict[str, list[Leg]] = defaultdict(list)
    for lg in closed:
        by_setup[lg.setup_sid].append(lg)
    setup_pnl: list[tuple[dt.datetime, float, Setup]] = []
    setup_r: list[float] = []
    smap = {s.sid: s for s in setups}
    for sid, lgs in by_setup.items():
        s = smap.get(sid)
        if s is None:
            continue
        pnl = sum(lg.pnl_ccy for lg in lgs)
        last_close = max(lg.close_ts for lg in lgs if lg.close_ts)
        setup_pnl.append((last_close, pnl, s))
        risk = s.risk_oz
        if risk:
            rs = [lg.pnl_oz / risk for lg in lgs if lg.pnl_oz is not None]
            if rs:
                setup_r.append(statistics.mean(rs))
    wins = [p for _, p, _ in setup_pnl if p > 0]
    losses = [p for _, p, _ in setup_pnl if p < 0]
    gross_w, gross_l = sum(wins), -sum(losses)
    n = len(setup_pnl)
    max_dd, worst_daily_dd, worst_day = drawdown([(ts, p) for ts, p, _ in setup_pnl])
    # serie di perdite consecutive (per setup, in ordine di chiusura)
    streak = worst_streak = 0
    for _, p, _ in sorted(setup_pnl, key=lambda x: x[0]):
        streak = streak + 1 if p < 0 else 0
        worst_streak = max(worst_streak, streak)
    tp_hit: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for lg in closed:
        tp_hit[lg.tp_index][1] += 1
        if lg.outcome == "TP":
            tp_hit[lg.tp_index][0] += 1
    outcomes = Counter(lg.outcome for lg in legs)
    by_hour: Counter = Counter()
    by_wday: Counter = Counter()
    pnl_hour: dict[int, float] = defaultdict(float)
    pnl_wday: dict[str, float] = defaultdict(float)
    for _, p, s in setup_pnl:
        loc = s.ts.astimezone(ROME)
        by_hour[loc.hour] += 1
        by_wday[loc.strftime("%a")] += 1
        pnl_hour[loc.hour] += p
        pnl_wday[loc.strftime("%a")] += p
    after_be = [p for _, p, s in setup_pnl if s.n_be > 0]
    after_be_flat = sum(1 for _, p, s in setup_pnl if s.n_be > 0 and abs(p) <= max(1.0, 0.02 * (abs(p) + 1)))
    daily: dict[str, float] = defaultdict(float)
    for ts, p, _ in setup_pnl:
        daily[ts.astimezone(ROME).date().isoformat()] += p
    worst_daily_pnl = min(daily.items(), key=lambda kv: kv[1]) if daily else ("", 0.0)
    return {
        "scope": name, "unit": unit,
        "setups_closed": n,
        "legs_opened": len(legs), "legs_closed": len(closed),
        "winrate_setup_pct": 100 * len(wins) / n if n else None,
        "net_pnl": sum(p for _, p, _ in setup_pnl),
        "gross_win": gross_w, "gross_loss": gross_l,
        "profit_factor": (gross_w / gross_l) if gross_l else (math.inf if gross_w else None),
        "expectancy": (sum(p for _, p, _ in setup_pnl) / n) if n else None,
        "avg_win": statistics.mean(wins) if wins else None,
        "avg_loss": statistics.mean(losses) if losses else None,
        "avg_r": statistics.mean(setup_r) if setup_r else None,
        "max_drawdown": max_dd, "worst_daily_dd": worst_daily_dd, "worst_daily_dd_day": worst_day,
        "worst_day_pnl": worst_daily_pnl[1], "worst_day": worst_daily_pnl[0],
        "max_consecutive_losses": worst_streak,
        "tp_hit": {k: (v[0], v[1]) for k, v in sorted(tp_hit.items())},
        "outcomes": dict(outcomes),
        "by_hour": dict(sorted(by_hour.items())), "pnl_by_hour": dict(sorted(pnl_hour.items())),
        "by_wday": dict(by_wday), "pnl_by_wday": dict(pnl_wday),
        "after_be_n": len(after_be),
        "after_be_avg": statistics.mean(after_be) if after_be else None,
        "after_be_flat": after_be_flat,
        "after_be_winrate_pct": (100 * sum(1 for p in after_be if p > 0) / len(after_be)) if after_be else None,
    }


def theoretical_legs(setups: list[Setup], legs: list[Leg]) -> list[Leg]:
    """Leg "teoriche" del segnale: fill all'entry pubblicato, esito per split dedotto
    dall'evidenza dei broker (TP se un broker lo ha centrato, SL se un broker ha preso
    lo stop pieno, altrimenti prezzo mediano di chiusura dei broker). P&L in $/oz per 1 oz.
    Nessun dato prezzo indipendente: se nessun broker ha aperto lo split, l'esito e' n/d.
    """
    out: list[Leg] = []
    by_key: dict[tuple[str, int], list[Leg]] = defaultdict(list)
    for lg in legs:
        if lg.closed and lg.setup_sid:
            by_key[(lg.setup_sid, lg.tp_index)].append(lg)
    for s in setups:
        ref = s.entry_ref
        if ref is None:
            continue
        for i, tp in enumerate(s.tps, start=1):
            real = by_key.get((s.sid, i), [])
            if not real:
                continue
            outcomes = {lg.outcome for lg in real}
            close_ts = max(lg.close_ts for lg in real if lg.close_ts)
            if "TP" in outcomes:
                px, outcome = (s.final_tps[i - 1] if i - 1 < len(s.final_tps) else tp), "TP"
            elif "SL" in outcomes:
                px, outcome = (s.final_sl or s.sl), "SL"
            else:
                px = statistics.median(lg.close_price for lg in real if lg.close_price is not None)
                outcome = Counter(lg.outcome for lg in real).most_common(1)[0][0]
            pnl_oz = (px - ref) * s.sign
            out.append(Leg(broker="teorico", setup_sid=s.sid, sid=s.sid, tp_index=i, position_id=0,
                           direction=s.direction, volume=1.0, fill=ref, fill_ts=s.ts, close_price=px,
                           close_ts=close_ts, outcome=outcome, close_magic=None,
                           pnl_ccy=round(pnl_oz, 2), pnl_oz=pnl_oz))
    return out


# ---------------------------------------------------------------------------
# Simulazione conto (P&L lordo in USD da prezzo: $/oz x lotti x 100)
# ---------------------------------------------------------------------------

CONTRACT_OZ = 100.0


def simulate_account(legs: list[Leg], setups: list[Setup], balance: float, dd_pct: float,
                     payout: float, base_lot: float | None = None) -> dict:
    """Curva equity (solo trade chiusi) di un broker in USD lordi, DD giornaliero e massimo,
    riepilogo settimanale/mensile e moltiplicatore di size per rispettare `dd_pct`.

    Il P&L in USD e' ricostruito dai prezzi (XAUUSD e' quotato in USD): pnl_oz x volume x 100.
    Il drawdown intraday e' calcolato sui trade chiusi, quindi e' un limite inferiore del DD
    a equity flottante che una prop firm misurerebbe tick per tick.
    """
    closed = sorted([lg for lg in legs if lg.closed and lg.pnl_oz is not None], key=lambda x: x.close_ts)
    smap = {s.sid: s for s in setups}
    curve: list[tuple[dt.datetime, float, float]] = []
    equity = balance
    peak = balance
    max_dd = max_dd_pct = 0.0
    daily: dict[dt.date, dict] = {}
    weekly: dict[str, dict] = {}
    monthly: dict[str, dict] = {}
    seen_setups: dict[str, set] = defaultdict(set)
    for lg in closed:
        pnl = lg.pnl_oz * lg.volume * CONTRACT_OZ
        equity += pnl
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)
        max_dd_pct = max(max_dd_pct, 100 * (peak - equity) / peak if peak else 0)
        loc = lg.close_ts.astimezone(ROME)
        day = loc.date()
        d = daily.setdefault(day, {"start": equity - pnl, "low": equity - pnl, "high": equity - pnl,
                                   "end": equity, "pnl": 0.0, "legs": 0, "dd": 0.0})
        d["end"] = equity
        d["pnl"] += pnl
        d["legs"] += 1
        d["high"] = max(d["high"], equity)
        d["low"] = min(d["low"], equity)
        d["dd"] = max(d["dd"], d["high"] - equity, d["start"] - equity)
        for key, bucket in ((f"{loc.isocalendar()[0]}-W{loc.isocalendar()[1]:02d}", weekly),
                            (loc.strftime("%Y-%m"), monthly)):
            m = bucket.setdefault(key, {"pnl": 0.0, "legs": 0, "setups": 0, "start": equity - pnl, "end": equity,
                                        "peak": equity - pnl, "max_dd": 0.0, "worst_day": 0.0, "days": set()})
            m["pnl"] += pnl
            m["legs"] += 1
            m["end"] = equity
            m["peak"] = max(m["peak"], equity)
            m["max_dd"] = max(m["max_dd"], m["peak"] - equity)
            m["days"].add(day)
            if lg.setup_sid not in seen_setups[key]:
                seen_setups[key].add(lg.setup_sid)
                m["setups"] += 1
        curve.append((lg.close_ts, pnl, equity))
    for bucket in (weekly, monthly):
        for m in bucket.values():
            m["worst_day"] = max((daily[dd]["dd"] for dd in m["days"]), default=0.0)
            m["worst_day_pnl"] = min((daily[dd]["pnl"] for dd in m["days"]), default=0.0)
            m["days"] = len(m["days"])
    worst_daily = max(daily.items(), key=lambda kv: kv[1]["dd"]) if daily else (None, {"dd": 0.0})
    worst_daily_pnl = min(daily.items(), key=lambda kv: kv[1]["pnl"]) if daily else (None, {"pnl": 0.0})
    limit = balance * dd_pct / 100
    binding = max(max_dd, worst_daily[1]["dd"])
    k = (limit / binding) if binding else None
    lot_mix = Counter(lg.volume for lg in closed)
    base_lot = base_lot or (statistics.median([lg.volume for lg in closed]) if closed else None)
    net = equity - balance
    return {
        "balance": balance, "final_equity": equity, "net_pnl": net, "return_pct": 100 * net / balance,
        "max_dd": max_dd, "max_dd_pct": max_dd_pct,
        "worst_daily_dd": worst_daily[1]["dd"], "worst_daily_dd_day": worst_daily[0],
        "worst_daily_dd_pct": 100 * worst_daily[1]["dd"] / balance,
        "worst_day_pnl": worst_daily_pnl[1]["pnl"], "worst_day": worst_daily_pnl[0],
        "legs": len(closed), "setups": len({lg.setup_sid for lg in closed if lg.setup_sid in smap}),
        "days": len(daily), "daily": daily, "weekly": weekly, "monthly": monthly, "curve": curve,
        "dd_pct": dd_pct, "dd_limit": limit, "binding_dd": binding, "size_multiplier": k,
        "base_lot": base_lot, "lot_mix": dict(lot_mix), "suggested_lot": (base_lot * k) if (k and base_lot) else None,
        "payout": payout, "scaled_net": net * k if k else None,
        "scaled_payout": net * k * payout if k else None,
        "scaled_max_dd": max_dd * k if k else None, "scaled_worst_daily_dd": worst_daily[1]["dd"] * k if k else None,
    }


def simulation_markdown(name: str, sim: dict) -> list[str]:
    intro = ("Equity ricostruita sui trade chiusi in USD ($/oz x lotti x 100; commissioni/swap esclusi, conversione "
             "valuta non necessaria). Il DD intragiornaliero e' misurato dal massimo (o dal saldo di inizio giornata) "
             "al minimo della giornata sui trade chiusi: un DD a equity flottante e' >= a questo valore.")
    L = [f"## Simulazione conto {sim['balance']:,.0f} USD — {name} (solo trade IVAN, P&L lordo da prezzo)", "",
         intro, ""]
    L += md_table(["metrica", "valore"], [
        ["saldo iniziale", f"{sim['balance']:,.2f} USD"],
        ["equity finale", f"{sim['final_equity']:,.2f} USD"],
        ["P&L netto", f"{sim['net_pnl']:+,.2f} USD ({sim['return_pct']:+.2f}%)"],
        ["trade chiusi / setup / giorni operativi", f"{sim['legs']} / {sim['setups']} / {sim['days']}"],
        ["max drawdown (dal picco)", f"{sim['max_dd']:,.2f} USD ({sim['max_dd_pct']:.2f}%)"],
        ["peggior DD giornaliero", f"{sim['worst_daily_dd']:,.2f} USD ({sim['worst_daily_dd_pct']:.2f}%) il {sim['worst_daily_dd_day']}"],
        ["peggior giornata (P&L)", f"{sim['worst_day_pnl']:+,.2f} USD il {sim['worst_day']}"],
        ["lotto base (mediana) / lotti usati", (f"{sim['base_lot']:g} / " if sim["base_lot"] else "n/d / ") +
         ", ".join(f"{v:g} x{n}" for v, n in sorted(sim["lot_mix"].items()))],
    ])
    L += ["", f"### Sizing per conto prop {sim['balance']:,.0f} USD con DD max {sim['dd_pct']:g}% (giornaliero e totale), payout {sim['payout'] * 100:.0f}%", ""]
    if sim["size_multiplier"]:
        k = sim["size_multiplier"]
        L += md_table(["voce", "valore"], [
            ["limite DD", f"{sim['dd_limit']:,.2f} USD"],
            ["DD vincolante osservato (max fra totale e giornaliero)", f"{sim['binding_dd']:,.2f} USD"],
            ["moltiplicatore di size per toccare esattamente il limite", f"x{k:.2f}"],
            ["lotto equivalente (su lotto base)", f"{sim['suggested_lot']:.3f}" if sim["suggested_lot"] else "n/d"],
            ["lotto prudenziale (50% del limite, DD flottante non misurato)", f"{sim['suggested_lot'] / 2:.3f}" if sim["suggested_lot"] else "n/d"],
            ["P&L del periodo alla size scalata", f"{sim['scaled_net']:+,.2f} USD"],
            ["quota trader (payout)", f"{sim['scaled_payout']:+,.2f} USD"],
            ["max DD / peggior DD giornaliero alla size scalata", f"{sim['scaled_max_dd']:,.2f} / {sim['scaled_worst_daily_dd']:,.2f} USD"],
        ])
        L.append("")
        L.append("Nota: il moltiplicatore usa il DD storico sui trade chiusi; il DD flottante intraday (posizioni aperte "
                 "in perdita prima del BE/TP) e' piu' alto e su un conto prop viene misurato tick per tick. "
                 "Usare il lotto prudenziale o inferiore.")
    else:
        L.append("Nessun drawdown osservato: impossibile derivare un moltiplicatore.")
    L += ["", "### Riepilogo settimanale", ""]
    L += md_table(["settimana", "setup", "trade", "giorni", "P&L USD", "equity fine", "max DD sett.", "peggior DD giorn.", "peggior giornata"],
                  [[w, m["setups"], m["legs"], m["days"], f"{m['pnl']:+,.2f}", f"{m['end']:,.2f}",
                    f"{m['max_dd']:,.2f}", f"{m['worst_day']:,.2f}", f"{m['worst_day_pnl']:+,.2f}"]
                   for w, m in sorted(sim["weekly"].items())])
    L += ["", "### Riepilogo mensile", ""]
    L += md_table(["mese", "setup", "trade", "giorni", "P&L USD", "equity fine", "max DD mese", "peggior DD giorn."],
                  [[w, m["setups"], m["legs"], m["days"], f"{m['pnl']:+,.2f}", f"{m['end']:,.2f}",
                    f"{m['max_dd']:,.2f}", f"{m['worst_day']:,.2f}"] for w, m in sorted(sim["monthly"].items())])
    L += ["", "### Giornaliero", ""]
    L += md_table(["giorno", "trade", "P&L USD", "equity fine", "DD intraday (chiusi)", "DD %"],
                  [[d.isoformat(), v["legs"], f"{v['pnl']:+,.2f}", f"{v['end']:,.2f}", f"{v['dd']:,.2f}",
                    f"{100 * v['dd'] / sim['balance']:.2f}%"] for d, v in sorted(sim["daily"].items())])
    return L


def write_simulation_csv(out: Path, name: str, sim: dict) -> None:
    with (out / f"ivan_sim_{name}_daily.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["day", "legs", "pnl_usd", "equity_start", "equity_end", "intraday_dd_usd", "intraday_dd_pct"])
        for d, v in sorted(sim["daily"].items()):
            w.writerow([d.isoformat(), v["legs"], round(v["pnl"], 2), round(v["start"], 2), round(v["end"], 2),
                        round(v["dd"], 2), round(100 * v["dd"] / sim["balance"], 3)])
    with (out / f"ivan_sim_{name}_equity.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["close_ts_utc", "pnl_usd", "equity_usd"])
        for ts, pnl, eq in sim["curve"]:
            w.writerow([fmt_utc(ts), round(pnl, 2), round(eq, 2)])


# ---------------------------------------------------------------------------
# Discrepanze
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    setup: Setup
    kind: str
    severity: int          # 3 alta, 2 media, 1 bassa
    detail: str


def find_issues(setups: list[Setup], legs: list[Leg], brokers: list[Broker],
                reasons: dict[tuple[str, str, int], str]) -> list[Issue]:
    issues: list[Issue] = []
    by_setup_broker: dict[tuple[str, str], list[Leg]] = defaultdict(list)
    for lg in legs:
        by_setup_broker[(lg.setup_sid, lg.broker)].append(lg)
    for s in setups:
        if s.from_edit:
            opened = [b.name for b in brokers if by_setup_broker.get((s.sid, b.name))]
            entry_txt = s.entry_ref if s.entry_ref is not None else 'mkt'
            if s.stale_edit:
                issues.append(Issue(s, "EDIT di messaggio stantio riemesso come OPEN", 3,
                                    f"il bridge ha riemesso come nuovo setup l'EDIT di un messaggio Telegram di "
                                    f"{s.edit_age.days} giorni prima (entry {entry_txt}); "
                                    f"eseguito su {', '.join(opened) or 'nessun broker'}"))
            else:
                age = f"{int(s.edit_age.total_seconds())} s" if s.edit_age is not None else "n/d"
                issues.append(Issue(s, "apertura via EDIT (messaggio completato dopo la pubblicazione)", 1,
                                    f"il segnale è stato pubblicato incompleto e completato con un EDIT dopo {age}: "
                                    f"aperto su {', '.join(opened) or 'nessun broker'} "
                                    f"(entry {entry_txt} vs prezzo di fill: vedi tabella)"))
        for b in brokers:
            lgs = by_setup_broker.get((s.sid, b.name), [])
            dup = [k for k, v in Counter(lg.tp_index for lg in lgs).items() if v > 1]
            if dup:
                issues.append(Issue(s, "split duplicati", 2,
                                    f"{b.name}: T{','.join(map(str, sorted(dup)))} aperti piu' volte "
                                    f"({len(lgs)} posizioni per {s.n_legs} split previsti)"))
            ea_closes = sorted(lg.close_ts for lg in lgs if lg.outcome == "EA_CLOSE" and lg.close_ts)
            if len(ea_closes) >= 2 and (ea_closes[-1] - ea_closes[0]) > dt.timedelta(seconds=60):
                span = (ea_closes[-1] - ea_closes[0]).total_seconds()
                issues.append(Issue(s, "CLOSE_ALL lento", 2,
                                    f"{b.name}: {len(ea_closes)} leg chiuse dall'EA in {span:.0f}s "
                                    f"({fmt_local(ea_closes[0])} → {fmt_local(ea_closes[-1])})"))
        counts = {b.name: len(by_setup_broker.get((s.sid, b.name), [])) for b in brokers}
        if len(set(counts.values())) > 1:
            parts = []
            for b in brokers:
                miss = [i for i in range(1, s.n_legs + 1)
                        if i not in {lg.tp_index for lg in by_setup_broker.get((s.sid, b.name), [])}]
                why = {reasons.get((s.sid, b.name, i), "") for i in miss}
                parts.append(f"{b.name}={counts[b.name]}/{s.n_legs}" + (f" [{'; '.join(sorted(w for w in why if w))}]" if miss else ""))
            all_why = [reasons.get((s.sid, b.name, i), "") for b in brokers for i in range(1, s.n_legs + 1)
                       if i not in {lg.tp_index for lg in by_setup_broker.get((s.sid, b.name), [])}]
            only_inactive = bool(all_why) and all("non attivo" in w or "non disponibile" in w for w in all_why)
            issues.append(Issue(s, "esecuzione disomogenea" + (" (terminali non attivi)" if only_inactive else ""),
                                1 if only_inactive else 3, "; ".join(parts)))
        # BE ordinato ma stop pieno preso dopo
        if s.n_be:
            be_ts = min(e.ts for e in s.followups if e.action in ("CHECK_AND_BE", "BREAK_EVEN_PRICE"))
            for b in brokers:
                bad = [lg for lg in by_setup_broker.get((s.sid, b.name), [])
                       if lg.outcome == "SL" and lg.close_ts and lg.close_ts > be_ts + dt.timedelta(seconds=30)]
                if bad:
                    issues.append(Issue(s, "BE non applicato", 3,
                                        f"{b.name}: T{','.join(str(lg.tp_index) for lg in bad)} chiuso in SL pieno "
                                        f"({fnum(bad[0].pnl_oz)} $/oz) dopo il messaggio BE delle {fmt_local(be_ts)}"))
        # TP modificati ma chiusi al TP originale
        if s.final_tps != s.tps:
            for lg in legs:
                if lg.setup_sid != s.sid or lg.outcome != "TP" or lg.close_price is None:
                    continue
                if s.tp_update_ts and lg.close_ts and lg.close_ts <= s.tp_update_ts + dt.timedelta(seconds=30):
                    continue
                i = lg.tp_index - 1
                if (i < len(s.final_tps) and i < len(s.tps) and s.final_tps[i] != s.tps[i]
                        and abs(lg.close_price - s.tps[i]) < abs(lg.close_price - s.final_tps[i])):
                    issues.append(Issue(s, "TP spostato non recepito", 2,
                                        f"{lg.broker}: T{lg.tp_index} chiuso a {lg.close_price:g} "
                                        f"(TP originale {s.tps[i]:g}, TP aggiornato {s.final_tps[i]:g})"))
        # chiusure manuali
        manual = [lg for lg in legs if lg.setup_sid == s.sid and lg.outcome == "MANUAL"]
        if manual:
            per_b = Counter(lg.broker for lg in manual)
            tot = sum(lg.pnl_ccy for lg in manual)
            issues.append(Issue(s, "chiusura manuale (magic 0)", 2,
                                ", ".join(f"{b}: {n} leg" for b, n in sorted(per_b.items())) +
                                f" — P&L {fnum(tot)} (valute miste se piu' broker)"))
        # posizioni ancora aperte
        still = [lg for lg in legs if lg.setup_sid == s.sid and not lg.closed]
        if still:
            issues.append(Issue(s, "posizione ancora aperta", 1,
                                ", ".join(f"{lg.broker} T{lg.tp_index}" for lg in still)))
        # slippage / spread: differenza fill fra broker per lo stesso split
        for i in range(1, s.n_legs + 1):
            fills = {lg.broker: lg.fill for lg in legs if lg.setup_sid == s.sid and lg.tp_index == i}
            if len(fills) >= 2:
                spread = max(fills.values()) - min(fills.values())
                if spread >= 1.0:
                    issues.append(Issue(s, "fill divergenti tra broker", 1,
                                        f"T{i}: " + ", ".join(f"{b} {p:g}" for b, p in sorted(fills.items())) +
                                        f" (range {spread:.2f} $)"))
        # fill lontano dall'entry del segnale
        ref = s.entry_ref
        if ref is not None:
            far = [lg for lg in legs if lg.setup_sid == s.sid and abs(lg.fill - ref) >= 3.0]
            if far:
                worst = max(far, key=lambda lg: abs(lg.fill - ref))
                issues.append(Issue(s, "fill lontano dall'entry", 2,
                                    f"{len(far)} leg oltre 3 $ dall'entry {ref:g}; peggiore {worst.broker} "
                                    f"T{worst.tp_index} fill {worst.fill:g} ({worst.fill - ref:+.2f} $)"))
    return issues


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def write_csvs(out: Path, setups: list[Setup], legs: list[Leg], brokers: list[Broker],
               reasons: dict[tuple[str, str, int], str], stats: list[dict]) -> None:
    out.mkdir(parents=True, exist_ok=True)
    with (out / "ivan_setups.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        head = ["signal_id", "ts_utc", "ora_italia", "direction", "entry", "entry_range", "sl", "tp1", "tp2", "tp3", "tp4",
                "reentry", "n_be", "n_close_msgs", "final_sl", "final_tps", "ivan_claims", "followups"]
        for b in brokers:
            head += [f"{b.name}_legs", f"{b.name}_outcomes", f"{b.name}_pnl_{b.currency or 'ccy'}",
                     f"{b.name}_pnl_oz", f"{b.name}_missing"]
        w.writerow(head)
        for s in setups:
            row = [s.sid, fmt_utc(s.ts), fmt_local(s.ts), s.direction, s.entry if s.entry is not None else "",
                   f"{s.entry_range[0]:g}-{s.entry_range[1]:g}" if s.entry_range else "", s.sl,
                   *[(s.tps[i] if i < len(s.tps) else "") for i in range(4)],
                   int(s.is_reentry), s.n_be, s.n_close, s.final_sl,
                   " ".join(f"{x:g}" for x in s.final_tps), " | ".join(s.claims),
                   " | ".join(f"{fmt_local(e.ts)} {e.action}" for e in s.followups)]
            for b in brokers:
                lgs = sorted([lg for lg in legs if lg.setup_sid == s.sid and lg.broker == b.name],
                             key=lambda lg: lg.tp_index)
                miss = [f"T{i}:{reasons.get((s.sid, b.name, i), '')}" for i in range(1, s.n_legs + 1)
                        if i not in {lg.tp_index for lg in lgs}]
                row += [len(lgs), " ".join(f"T{lg.tp_index}={lg.outcome}" for lg in lgs),
                        round(sum(lg.pnl_ccy for lg in lgs), 2),
                        round(sum(lg.pnl_oz or 0 for lg in lgs), 2), " | ".join(miss)]
            w.writerow(row)
    with (out / "ivan_legs.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["broker", "setup_signal_id", "leg_signal_id", "tp_index", "position_id", "direction", "volume",
                    "signal_entry", "fill", "fill_minus_entry", "fill_ts_utc", "close_price", "close_ts_utc",
                    "outcome", "close_magic", "pnl_ccy", "pnl_oz", "r_multiple", "close_comment"])
        smap = {s.sid: s for s in setups}
        for lg in sorted(legs, key=lambda x: (x.fill_ts, x.broker, x.tp_index)):
            s = smap.get(lg.setup_sid)
            ref = s.entry_ref if s else None
            risk = s.risk_oz if s else None
            w.writerow([lg.broker, lg.setup_sid, lg.sid, lg.tp_index, lg.position_id, lg.direction, lg.volume,
                        ref if ref is not None else "", lg.fill,
                        round(lg.fill - ref, 2) if ref is not None else "", fmt_utc(lg.fill_ts),
                        lg.close_price if lg.close_price is not None else "", fmt_utc(lg.close_ts),
                        lg.outcome, lg.close_magic if lg.close_magic is not None else "", lg.pnl_ccy,
                        round(lg.pnl_oz, 2) if lg.pnl_oz is not None else "",
                        round(lg.pnl_oz / risk, 2) if (lg.pnl_oz is not None and risk) else "",
                        lg.close_comment])
    with (out / "ivan_stats.csv").open("w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["scope", "metric", "value"])
        for st in stats:
            for k, v in st.items():
                if k == "scope":
                    continue
                if isinstance(v, dict):
                    v = json.dumps(v, ensure_ascii=False)
                elif isinstance(v, float):
                    v = "inf" if math.isinf(v) else round(v, 3)
                w.writerow([st["scope"], k, v])


def md_table(head: list[str], rows: list[list]) -> list[str]:
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return out


def stats_table(stats: list[dict]) -> list[str]:
    def pf(v):
        if v is None:
            return "n/d"
        return "inf" if math.isinf(v) else f"{v:.2f}"
    head = ["metrica"] + [f"{s['scope']} ({s['unit']})" for s in stats]
    rows = [
        ["setup chiusi", *[s["setups_closed"] for s in stats]],
        ["leg aperte / chiuse", *[f"{s['legs_opened']} / {s['legs_closed']}" for s in stats]],
        ["winrate setup %", *[fnum(s["winrate_setup_pct"], 1) for s in stats]],
        ["P&L netto", *[fnum(s["net_pnl"]) for s in stats]],
        ["profit factor", *[pf(s["profit_factor"]) for s in stats]],
        ["expectancy / setup", *[fnum(s["expectancy"]) for s in stats]],
        ["media vincita / perdita", *[f"{fnum(s['avg_win'])} / {fnum(s['avg_loss'])}" for s in stats]],
        ["R medio (per setup, $/oz / rischio)", *[fnum(s["avg_r"]) for s in stats]],
        ["max drawdown", *[fnum(s["max_drawdown"]) for s in stats]],
        ["peggior DD intragiornaliero", *[f"{fnum(s['worst_daily_dd'])} ({s['worst_daily_dd_day']})" for s in stats]],
        ["peggior giornata (P&L)", *[f"{fnum(s['worst_day_pnl'])} ({s['worst_day']})" for s in stats]],
        ["max perdite consecutive (setup)", *[s["max_consecutive_losses"] for s in stats]],
        ["setup con messaggio BE: n / winrate % / P&L medio",
         *[f"{s['after_be_n']} / {fnum(s['after_be_winrate_pct'], 0)} / {fnum(s['after_be_avg'])}" for s in stats]],
    ]
    for i in range(1, 5):
        rows.append([f"TP{i} centrato (leg chiuse)",
                     *[(f"{s['tp_hit'][i][0]}/{s['tp_hit'][i][1]} ({100 * s['tp_hit'][i][0] / s['tp_hit'][i][1]:.0f}%)"
                        if s["tp_hit"].get(i) and s["tp_hit"][i][1] else "-") for s in stats]])
    rows.append(["esiti leg", *[", ".join(f"{k} {v}" for k, v in sorted(s["outcomes"].items())) for s in stats]])
    return md_table(head, rows)


def write_markdown(out: Path, setups: list[Setup], legs: list[Leg], brokers: list[Broker],
                   reasons: dict[tuple[str, str, int], str], stats: list[dict], issues: list[Issue],
                   d_from: dt.date, d_to: dt.date, sources: list[str], extra: list[str] | None = None) -> Path:
    L: list[str] = []
    L += [f"# Report IVAN VIP oro — segnale vs esecuzione ({d_from:%d/%m/%Y} → {d_to:%d/%m/%Y})", "",
          f"_generato {dt.datetime.now(UTC):%Y-%m-%d %H:%M} UTC · orari in ora italiana (UTC in CSV)_", "",
          "Fonti: " + "; ".join(sources), ""]
    L += ["## Broker", ""]
    L += md_table(["broker", "conto", "valuta", "offset ora server vs UTC", "primo deal", "ultimo deal", "leg IVAN", "stats EA", "note log EA"],
                  [[b.name, b.login or "n/d", b.currency or "n/d", f"{b.offset_hours:+.0f}h",
                    fmt_local(b.first_deal - dt.timedelta(hours=b.offset_hours)) if b.first_deal else "-",
                    fmt_local(b.last_deal - dt.timedelta(hours=b.offset_hours)) if b.last_deal else "-",
                    sum(1 for lg in legs if lg.broker == b.name), len(b.stats), len(b.ea_notes)] for b in brokers])
    note = ("Per broker il P&L e' nella valuta del conto (lotti diversi: non sommare tra broker). "
            "\"Teorico\" = segnale di Ivan con fill all'entry pubblicato, 1 oz, esito per split dedotto "
            "dall'evidenza dei broker (TP se almeno un broker lo ha centrato, SL se almeno uno ha preso lo stop "
            "pieno, altrimenti prezzo mediano di chiusura reale). Non e' disponibile una serie prezzi indipendente: "
            "gli split mai aperti da nessun broker non hanno esito teorico.")
    L += ["", "## Statistiche", "", note, ""]
    L += stats_table(stats)
    L += ["", "### Distribuzione per ora (Italia) e giorno — setup chiusi, P&L", ""]
    for s in stats:
        L.append(f"- **{s['scope']}** ora: " + ", ".join(f"{h:02d}h n={n} P&L={s['pnl_by_hour'][h]:.2f}" for h, n in s["by_hour"].items()))
        L.append("  giorno: " + ", ".join(f"{d} n={n} P&L={s['pnl_by_wday'][d]:.2f}" for d, n in s["by_wday"].items()))
    if extra:
        L += ["", *extra]
    L += ["", "## Discrepanze", ""]
    if not issues:
        L.append("Nessuna discrepanza rilevata.")
    else:
        L += md_table(["sev", "setup", "tipo", "dettaglio"],
                      [[{3: "ALTA", 2: "media", 1: "bassa"}[i.severity], i.setup.label(), i.kind, i.detail]
                       for i in sorted(issues, key=lambda i: (-i.severity, i.setup.ts))])
    L += ["", "## Setup", ""]
    for s in setups:
        L.append(f"### {s.label()}  `{s.sid}`" + (" — RIENTRO" if s.is_reentry else ""))
        L.append("")
        L.append(f"- Messaggio ({fmt_utc(s.ts)} UTC): `{s.text.replace(chr(10), ' | ')[:160]}`")
        where = (f"{s.entry:g}" if s.entry is not None
                 else (f"{s.entry_range[0]:g}-{s.entry_range[1]:g}" if s.entry_range else "mercato"))
        L.append(f"- Segnale: **{s.direction}** entry {where}"
                 f" · SL {s.sl:g} · TP {' / '.join(f'{x:g}' for x in s.tps)}"
                 + (f" · rischio {s.risk_oz:.1f} $/oz" if s.risk_oz else ""))
        if s.final_tps != s.tps or (s.final_sl is not None and s.final_sl != s.sl):
            L.append(f"- Livelli finali dopo modifiche: SL {s.final_sl:g} · TP {' / '.join(f'{x:g}' for x in s.final_tps)}")
        if s.followups:
            L.append("- Messaggi successivi: " + "; ".join(
                f"{fmt_local(e.ts)} {e.action}" +
                (f" be={e.payload.get('be_price')}" if e.action == 'CHECK_AND_BE' and e.payload.get('be_price') else "") +
                (f" sl={e.payload.get('new_sl')}" if e.action == 'UPDATE_SL' else "") +
                (f" tp={e.payload.get('new_tp')}" if e.action == 'UPDATE_TP' else "") +
                (f" ref={e.payload.get('reference_price')}" if e.action.startswith('CLOSE') and e.payload.get('reference_price') else "") +
                (f" «{e.text[:40].replace(chr(10), ' ')}»" if e.action.startswith('CLOSE') else "")
                for e in s.followups))
        if s.claims:
            L.append("- Ivan dichiara: " + "; ".join(s.claims))
        rows = []
        for b in brokers:
            lgs = {lg.tp_index: lg for lg in legs if lg.setup_sid == s.sid and lg.broker == b.name}
            ref = s.entry_ref
            for i in range(1, s.n_legs + 1):
                lg = lgs.get(i)
                if lg is None:
                    rows.append([b.name, f"T{i}", "-", "-", "-", "-", "**non aperto**", "-", "-",
                                 reasons.get((s.sid, b.name, i), "")])
                    continue
                rows.append([b.name, f"T{i}", f"{lg.fill:g}", fmt_local(lg.fill_ts),
                             f"{lg.fill - ref:+.2f}" if ref is not None else "-",
                             f"{lg.close_price:g} {fmt_local(lg.close_ts)}" if lg.close_price is not None else "aperta",
                             lg.outcome, f"{lg.pnl_ccy:.2f} {b.currency}",
                             fnum(lg.pnl_oz), lg.close_comment])
        L.append("")
        L += md_table(["broker", "split", "fill", "ora fill", "fill-entry $", "chiusura", "esito", "P&L", "$/oz", "note"], rows)
        L.append("")
    path = out / "ivan_report.md"
    path.write_text("\n".join(L), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def run(events: list[Event], brokers: list[Broker], d_from: dt.date, d_to: dt.date, out: Path,
        sources: list[str], sim: dict | None = None) -> dict:
    lo = dt.datetime.combine(d_from, dt.time(), UTC)
    hi = dt.datetime.combine(d_to, dt.time(23, 59, 59), UTC)
    all_setups = build_setups(events)
    setups = [s for s in all_setups if lo <= s.ts <= hi]
    events_by_sid = {e.sid: e for e in events if e.sid}
    legs: list[Leg] = []
    all_legs: list[Leg] = []
    for b in brokers:
        b.offset_hours = infer_server_offset(b, events_by_sid)
        blegs = build_legs(b, all_setups, b.offset_hours)
        all_legs += blegs
        legs += [lg for lg in blegs if lg.setup_sid in {s.sid for s in setups}]
    reasons: dict[tuple[str, str, int], str] = {}
    for idx, s in enumerate(setups):
        nxt = setups[idx + 1].ts if idx + 1 < len(setups) else None
        for b in brokers:
            have = {lg.tp_index for lg in legs if lg.setup_sid == s.sid and lg.broker == b.name}
            for i in range(1, s.n_legs + 1):
                if i not in have:
                    reasons[(s.sid, b.name, i)] = missing_reason(s, i, b, nxt)
    stats = [compute_stats(b.name, setups, [lg for lg in legs if lg.broker == b.name], b.currency or "ccy")
             for b in brokers]
    theo = theoretical_legs(setups, legs)
    stats.append(compute_stats("teorico", setups, theo, "$/oz"))
    issues = find_issues(setups, legs, brokers, reasons)
    write_csvs(out, setups, legs, brokers, reasons, stats)
    sim_res = None
    sim_md: list[str] = []
    if sim:
        s_lo = dt.datetime.combine(sim["from"], dt.time(), UTC)
        sim_setups = [s for s in all_setups if s_lo <= s.ts <= hi]
        sim_sids = {s.sid for s in sim_setups}
        sim_legs = [lg for lg in all_legs if lg.broker == sim["broker"] and lg.setup_sid in sim_sids]
        sim_res = simulate_account(sim_legs, sim_setups, sim["balance"], sim["dd_pct"], sim["payout"])
        sim_md = simulation_markdown(f"{sim['broker']} dal {sim['from']:%d/%m/%Y}", sim_res)
        write_simulation_csv(out, sim["broker"], sim_res)
    md = write_markdown(out, setups, legs, brokers, reasons, stats, issues, d_from, d_to, sources, sim_md)
    return {"setups": setups, "legs": legs, "stats": stats, "issues": issues, "markdown": md,
            "theoretical": theo, "simulation": sim_res}


def parse_broker_arg(arg: str) -> tuple[str, Path]:
    if "=" not in arg:
        raise argparse.ArgumentTypeError("--broker NAME=DIR")
    name, folder = arg.split("=", 1)
    return name.strip(), Path(folder)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--events", nargs="*", default=[], help="cartella o file events_*.jsonl del bridge")
    ap.add_argument("--bridge-log", nargs="*", default=[], help="log testuali tradingo_*.log (fallback)")
    ap.add_argument("--broker", action="append", default=[], type=parse_broker_arg, help="NAME=DIR")
    ap.add_argument("--from", dest="d_from", required=True, help="YYYY-MM-DD (UTC)")
    ap.add_argument("--to", dest="d_to", required=True, help="YYYY-MM-DD (UTC, inclusivo)")
    ap.add_argument("--out", required=True, help="cartella output")
    ap.add_argument("--vps-utc-offset", type=float, default=2.0,
                    help="ore locali VPS - UTC per log bridge/EA (default 2 = CEST)")
    ap.add_argument("--sim-broker", help="broker su cui simulare un conto (es. vantage)")
    ap.add_argument("--sim-balance", type=float, default=10000.0, help="saldo iniziale USD (default 10000)")
    ap.add_argument("--sim-from", help="inizio simulazione YYYY-MM-DD (default = --from)")
    ap.add_argument("--sim-dd-pct", type=float, default=7.0, help="DD max %% giornaliero e totale (default 7)")
    ap.add_argument("--sim-payout", type=float, default=0.7, help="quota payout trader (default 0.7)")
    args = ap.parse_args(argv)

    event_files: list[Path] = []
    for e in args.events:
        p = Path(e)
        event_files += sorted(p.glob("events_*.jsonl")) if p.is_dir() else [p]
    events = load_events_jsonl(event_files)
    sources = [f"{len(event_files)} file journal bridge"] if event_files else []
    if args.bridge_log:
        log_events = load_events_bridge_log([Path(p) for p in args.bridge_log], args.vps_utc_offset)
        known = {e.sid for e in events if e.sid}
        events += [e for e in log_events if e.sid and e.sid not in known]
        events.sort(key=lambda x: x.ts)
        sources.append(f"{len(args.bridge_log)} log bridge")
    if not events:
        ap.error("nessun evento CH_IVAN caricato (--events / --bridge-log)")
    brokers = [load_broker(name, folder, args.vps_utc_offset) for name, folder in args.broker]
    sources += [f"{b.name}: {len(b.deals)} deal" for b in brokers]
    sim = None
    if args.sim_broker:
        if args.sim_broker not in {b.name for b in brokers}:
            ap.error(f"--sim-broker {args.sim_broker} non tra i broker forniti")
        sim = {"broker": args.sim_broker, "balance": args.sim_balance,
               "from": dt.date.fromisoformat(args.sim_from or args.d_from),
               "dd_pct": args.sim_dd_pct, "payout": args.sim_payout}
    res = run(events, brokers, dt.date.fromisoformat(args.d_from), dt.date.fromisoformat(args.d_to),
              Path(args.out), sources, sim)
    print(f"setup: {len(res['setups'])} · leg: {len(res['legs'])} · discrepanze: {len(res['issues'])}")
    print(f"report: {res['markdown']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
