"""Traduzione segnale TradinGo -> order request DXtrade (dxSCA).

Funzioni pure e testabili: nessuna chiamata di rete, nessuno stato globale.
Il modello DXtrade differisce da MT4/MT5 su tre punti che questo modulo
normalizza:

1. **Quantità in unità, non in lotti.** ``quantity = lotti * instrument.lotSize``,
   arrotondata a ``quantityIncrement``.
2. **SL/TP non sono campi dell'ordine.** Vanno inviati come ordini di
   protezione: gruppo ``IF-THEN`` (MARKET di apertura + STOP + LIMIT con
   ``quantity: 0``) in apertura, oppure come POST separati con
   ``positionCode`` su una posizione già aperta.
3. **Non esiste il magic number.** L'identità del trade si porta con
   ``orderCode`` (client id univoco, quindi idempotente) e con ``metadata``,
   che la piattaforma restituisce insieme all'ordine.

La valutazione di ``entry_range`` replica ``EvaluateEntryRange`` dell'EA MQL5:
prezzo dentro il range -> ``EXECUTED_IN_RANGE``; fuori ma entro la tolleranza
in punti -> ``EXECUTED_TOLERANCE``; oltre -> ``CANCELLED_RANGE``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

# Simboli MT -> simboli DXtrade. Il naming è configurabile per broker: DXtrade
# usa di norma la forma con slash per il forex e i metalli.
DEFAULT_SYMBOL_MAP = {
    "XAUUSD": "XAU/USD",
    "XAGUSD": "XAG/USD",
    "EURUSD": "EUR/USD",
    "GBPUSD": "GBP/USD",
    "USDJPY": "USD/JPY",
    "AUDUSD": "AUD/USD",
    "USDCAD": "USD/CAD",
    "USDCHF": "USD/CHF",
    "NZDUSD": "NZD/USD",
    "EURJPY": "EUR/JPY",
    "GBPJPY": "GBP/JPY",
}

_CODE_SAFE = re.compile(r"[^A-Za-z0-9_.-]")
# Vincoli metadata dichiarati dall'API: max 32 coppie, chiavi <= 32 char
# sul pattern ^[a-zA-Z0-9_-\.]+$, valori <= 256 char.
_META_VALUE_MAX = 256

TIF_GTC = "GTC"

STATUS_IN_RANGE = "EXECUTED_IN_RANGE"
STATUS_TOLERANCE = "EXECUTED_TOLERANCE"
STATUS_DIRECT = "EXECUTED_DIRECT"
STATUS_OPEN_NOW = "EXECUTED_OPEN_NOW"
STATUS_CANCELLED = "CANCELLED_RANGE"


class MappingError(ValueError):
    """Segnale non traducibile in ordini DXtrade."""


@dataclass
class PlannedRequest:
    """Una singola chiamata POST /orders da eseguire."""

    intent: str
    payload: dict
    tp_index: int = 0
    order_code: str = ""
    note: str = ""


@dataclass
class EntryDecision:
    execute: bool
    status: str
    price: float = 0.0
    distance_points: float = 0.0


@dataclass
class InstrumentMeta:
    """Sottoinsieme di ``GET /instruments/{symbol}`` che ci serve."""

    symbol: str
    lot_size: float = 1.0
    price_increment: float = 0.01
    quantity_increment: float = 0.0
    description: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict) -> "InstrumentMeta":
        if not data or not data.get("symbol"):
            raise MappingError("instrument senza symbol")
        return cls(
            symbol=str(data["symbol"]),
            lot_size=_pos_float(data.get("lotSize"), 1.0),
            price_increment=_pos_float(data.get("priceIncrement"), 0.01),
            quantity_increment=_pos_float(data.get("quantityIncrement"), 0.0),
            description=str(data.get("description") or ""),
            raw=dict(data),
        )


# ── simboli e quantità ────────────────────────────────────────────────────


def map_symbol(symbol: str, symbol_map: dict[str, str] | None = None) -> str:
    """XAUUSD -> XAU/USD (override per broker via ``symbol_map``)."""
    if not symbol:
        raise MappingError("symbol mancante")
    key = symbol.strip().upper()
    table = {**DEFAULT_SYMBOL_MAP, **{k.upper(): v for k, v in (symbol_map or {}).items()}}
    return table.get(key, symbol.strip())


def lots_to_units(lots: float, instrument: InstrumentMeta) -> float:
    """Lotti MT -> unità DXtrade, arrotondate a ``quantityIncrement``."""
    if lots is None or float(lots) <= 0:
        raise MappingError(f"lotto non valido: {lots!r}")
    units = float(lots) * instrument.lot_size
    step = instrument.quantity_increment
    if step and step > 0:
        units = round(round(units / step) * step, 10)
    if units <= 0:
        raise MappingError(
            f"quantità arrotondata a 0: lots={lots} lotSize={instrument.lot_size} "
            f"step={step}"
        )
    return _clean_float(units)


# ── identità del trade ────────────────────────────────────────────────────


def order_code(
    channel_id: str, signal_id: str, tp_index: int, kind: str = "O"
) -> str:
    """Client order id deterministico: idempotenza lato piattaforma.

    Un secondo POST con lo stesso ``orderCode`` viene rifiutato con 409/100,
    che il client traduce in ``duplicate`` invece di aprire un doppione.
    """
    channel = _short_channel(channel_id)
    sid = _sanitize(signal_id or "nosig")
    return _sanitize(f"TG-{channel}-{sid}-T{int(tp_index)}{kind}")


def metadata_for(
    channel_id: str,
    signal_id: str,
    tp_index: int,
    bridge_version: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Metadata per ritrovare il trade dopo un restart (sostituto del magic)."""
    meta = {
        "tg_channel": _sanitize(channel_id or ""),
        "tg_signal": _sanitize(signal_id or ""),
        "tg_tp": str(int(tp_index)),
    }
    if bridge_version:
        meta["tg_bridge"] = _sanitize(str(bridge_version))
    for key, value in (extra or {}).items():
        meta[_sanitize(str(key))[:32]] = str(value)[:_META_VALUE_MAX]
    return {k: v[:_META_VALUE_MAX] for k, v in meta.items() if v}


# ── valutazione entry (porting EvaluateEntryRange dell'EA) ────────────────


def evaluate_entry(
    direction: str,
    bid: float | None,
    ask: float | None,
    entry_range: Sequence[float] | None,
    price_increment: float,
    tolerance_points: int,
) -> EntryDecision:
    """Decide se eseguire a mercato, come fa l'EA MQL5.

    Il prezzo di riferimento è ``ask`` per BUY e ``bid`` per SELL. Senza
    ``entry_range`` l'esecuzione è sempre diretta (``EXECUTED_DIRECT``).
    """
    side = (direction or "").upper()
    if side not in ("BUY", "SELL"):
        raise MappingError(f"direction non valida: {direction!r}")
    if not entry_range:
        return EntryDecision(True, STATUS_DIRECT)

    if len(entry_range) != 2:
        raise MappingError(f"entry_range non valido: {entry_range!r}")
    lo, hi = min(entry_range), max(entry_range)
    price = ask if side == "BUY" else bid
    if price is None or price <= 0:
        raise MappingError("quote non disponibile per valutare entry_range")

    if lo <= price <= hi:
        return EntryDecision(True, STATUS_IN_RANGE, price, 0.0)

    distance_price = (lo - price) if price < lo else (price - hi)
    point = price_increment if price_increment and price_increment > 0 else 0.01
    distance_points = distance_price / point
    if distance_points <= float(tolerance_points):
        return EntryDecision(True, STATUS_TOLERANCE, price, distance_points)
    return EntryDecision(False, STATUS_CANCELLED, price, distance_points)


# ── costruzione ordini ────────────────────────────────────────────────────


def opposite(side: str) -> str:
    return "SELL" if (side or "").upper() == "BUY" else "BUY"


def build_market_open(
    instrument: str,
    side: str,
    quantity: float,
    code: str,
    metadata: dict[str, str] | None = None,
    tif: str = TIF_GTC,
) -> dict:
    payload = {
        "orderCode": code,
        "type": "MARKET",
        "instrument": instrument,
        "quantity": quantity,
        "positionEffect": "OPEN",
        "side": _side(side),
        "tif": tif,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def build_limit_open(
    instrument: str,
    side: str,
    quantity: float,
    limit_price: float,
    code: str,
    metadata: dict[str, str] | None = None,
    tif: str = TIF_GTC,
) -> dict:
    payload = build_market_open(instrument, side, quantity, code, metadata, tif)
    payload["type"] = "LIMIT"
    payload["limitPrice"] = _clean_float(limit_price)
    return payload


def build_protection(
    instrument: str,
    open_side: str,
    kind: str,
    price: float,
    code: str,
    position_code: str | None = None,
    metadata: dict[str, str] | None = None,
    tif: str = TIF_GTC,
) -> dict:
    """Ordine di protezione: ``SL`` -> STOP, ``TP`` -> LIMIT.

    Con ``position_code`` l'ordine si aggancia a una posizione esistente e la
    quantità resta 0 (la piattaforma usa quella della posizione). Dentro un
    gruppo IF-THEN il ``position_code`` è assente perché la posizione non
    esiste ancora.
    """
    kind = kind.upper()
    if kind not in ("SL", "TP"):
        raise MappingError(f"protezione non valida: {kind!r}")
    payload: dict[str, Any] = {
        "orderCode": code,
        "type": "STOP" if kind == "SL" else "LIMIT",
        "instrument": instrument,
        "quantity": 0,
        "positionEffect": "CLOSE",
        "side": opposite(open_side),
        "tif": tif,
    }
    if kind == "SL":
        payload["stopPrice"] = _clean_float(price)
    else:
        payload["limitPrice"] = _clean_float(price)
    if position_code:
        payload["positionCode"] = str(position_code)
    if metadata:
        payload["metadata"] = metadata
    return payload


def build_close_position(
    instrument: str,
    open_side: str,
    position_code: str,
    code: str,
    metadata: dict[str, str] | None = None,
) -> dict:
    """Chiusura a mercato di una singola posizione (quantity 0 = tutta)."""
    if not position_code:
        raise MappingError("position_code richiesto per la chiusura")
    payload: dict[str, Any] = {
        "orderCode": code,
        "type": "MARKET",
        "instrument": instrument,
        "quantity": 0,
        "positionEffect": "CLOSE",
        "positionCode": str(position_code),
        "side": opposite(open_side),
        "tif": TIF_GTC,
    }
    if metadata:
        payload["metadata"] = metadata
    return payload


def build_if_then_group(orders: Sequence[dict]) -> dict:
    if len(orders) < 2:
        raise MappingError("un gruppo IF-THEN richiede almeno 2 ordini")
    return {"orders": list(orders), "contingencyType": "IF-THEN"}


# ── piani per action del bridge ───────────────────────────────────────────


def plan_open(
    signal: dict,
    instrument: InstrumentMeta,
    symbol_map: dict[str, str] | None = None,
    bridge_version: str | None = None,
) -> list[PlannedRequest]:
    """``OPEN`` / ``OPEN_NOW`` -> una richiesta per ticket.

    Con SL/TP presenti si usa un gruppo IF-THEN per ticket (apertura +
    protezioni in un colpo). ``OPEN_NOW`` resta nudo, come sull'EA: le
    protezioni arrivano con l'``UPDATE_OPEN`` successivo.
    """
    action = (signal.get("action") or "").upper()
    if action not in ("OPEN", "OPEN_NOW"):
        raise MappingError(f"plan_open non gestisce action={action!r}")

    side = _side(signal.get("direction"))
    symbol = map_symbol(signal.get("symbol", ""), symbol_map)
    channel_id = signal.get("channel_id", "")
    signal_id = signal.get("signal_id", "")
    trades = max(1, int(signal.get("trades") or 1))
    units = lots_to_units(signal.get("fixed_lot"), instrument)
    sl = _opt_float(signal.get("sl"))
    tps = [float(t) for t in (signal.get("tp_levels") or []) if _opt_float(t)]

    plans: list[PlannedRequest] = []
    for i in range(1, trades + 1):
        code = order_code(channel_id, signal_id, i)
        meta = metadata_for(channel_id, signal_id, i, bridge_version)
        open_order = build_market_open(symbol, side, units, code, meta)
        tp = tps[i - 1] if i - 1 < len(tps) else None

        if action == "OPEN_NOW" or (sl is None and tp is None):
            plans.append(
                PlannedRequest(
                    intent=STATUS_OPEN_NOW if action == "OPEN_NOW" else STATUS_DIRECT,
                    payload=open_order,
                    tp_index=i,
                    order_code=code,
                    note="apertura nuda, protezioni con UPDATE_OPEN",
                )
            )
            continue

        group = [open_order]
        if sl is not None:
            group.append(
                build_protection(
                    symbol, side, "SL", sl, order_code(channel_id, signal_id, i, "SL")
                )
            )
        if tp is not None:
            group.append(
                build_protection(
                    symbol, side, "TP", tp, order_code(channel_id, signal_id, i, "TP")
                )
            )
        plans.append(
            PlannedRequest(
                intent="OPEN_WITH_PROTECTIONS",
                payload=build_if_then_group(group) if len(group) > 1 else open_order,
                tp_index=i,
                order_code=code,
            )
        )
    return plans


def plan_update_open(
    signal: dict,
    instrument: InstrumentMeta,
    position_codes: dict[int, str],
    symbol_map: dict[str, str] | None = None,
    bridge_version: str | None = None,
) -> list[PlannedRequest]:
    """``UPDATE_OPEN`` -> protezioni sulle posizioni già aperte + fill dei buchi.

    ``position_codes`` mappa ``tp_index -> positionCode`` (lo stato che sul
    lato MT5 è il magic number). Gli slot mancanti vengono aperti, come fa
    l'EA 2.15 con ``EXECUTED_UPDATE_FILL``. Le due protezioni vanno in due
    POST distinti: l'API non accetta due protezioni in una sola richiesta su
    una posizione esistente.
    """
    side = _side(signal.get("direction"))
    symbol = map_symbol(signal.get("symbol", ""), symbol_map)
    channel_id = signal.get("channel_id", "")
    signal_id = signal.get("signal_id", "")
    trades = max(1, int(signal.get("trades") or 1))
    sl = _opt_float(signal.get("sl"))
    tps = [float(t) for t in (signal.get("tp_levels") or []) if _opt_float(t)]
    units = lots_to_units(signal.get("fixed_lot"), instrument)

    plans: list[PlannedRequest] = []
    for i in range(1, trades + 1):
        tp = tps[i - 1] if i - 1 < len(tps) else None
        position_code = position_codes.get(i)
        meta = metadata_for(channel_id, signal_id, i, bridge_version)

        if not position_code:
            group = [build_market_open(symbol, side, units, order_code(channel_id, signal_id, i, "F"), meta)]
            if sl is not None:
                group.append(
                    build_protection(
                        symbol, side, "SL", sl,
                        order_code(channel_id, signal_id, i, "FSL"),
                    )
                )
            if tp is not None:
                group.append(
                    build_protection(
                        symbol, side, "TP", tp,
                        order_code(channel_id, signal_id, i, "FTP"),
                    )
                )
            plans.append(
                PlannedRequest(
                    intent="EXECUTED_UPDATE_FILL",
                    payload=build_if_then_group(group) if len(group) > 1 else group[0],
                    tp_index=i,
                    order_code=group[0]["orderCode"],
                    note="slot mancante: apre solo questo ticket",
                )
            )
            continue

        if sl is not None:
            plans.append(
                PlannedRequest(
                    intent="SET_SL",
                    payload=build_protection(
                        symbol, side, "SL", sl,
                        order_code(channel_id, signal_id, i, "SL"),
                        position_code=position_code,
                        metadata=meta,
                    ),
                    tp_index=i,
                )
            )
        if tp is not None:
            plans.append(
                PlannedRequest(
                    intent="SET_TP",
                    payload=build_protection(
                        symbol, side, "TP", tp,
                        order_code(channel_id, signal_id, i, "TP"),
                        position_code=position_code,
                        metadata=meta,
                    ),
                    tp_index=i,
                )
            )
    return plans


def plan_close(
    signal: dict,
    positions: Sequence[dict],
    symbol_map: dict[str, str] | None = None,
    suffix: str = "C",
) -> list[PlannedRequest]:
    """``CLOSE_ALL_SYMBOL`` / ``CHECK_AND_CLOSE`` -> una chiusura per posizione.

    Si chiude per ``positionCode``, non con il bulk close per strumento: più
    canali condividono XAUUSD e il bulk chiuderebbe anche i loro trade.
    """
    symbol = map_symbol(signal.get("symbol", ""), symbol_map)
    channel_id = signal.get("channel_id", "")
    signal_id = signal.get("signal_id", "")
    plans: list[PlannedRequest] = []
    for idx, pos in enumerate(positions, start=1):
        code = pos.get("positionCode")
        if not code:
            continue
        plans.append(
            PlannedRequest(
                intent="CLOSE_POSITION",
                payload=build_close_position(
                    symbol,
                    pos.get("side", "BUY"),
                    str(code),
                    order_code(channel_id, signal_id, idx, suffix),
                ),
                tp_index=idx,
                note=f"positionCode={code}",
            )
        )
    return plans


def select_positions(
    positions: Sequence[dict],
    symbol: str | None = None,
    side: str | None = None,
) -> list[dict]:
    """Filtra la snapshot posizioni. Le posizioni non portano metadata: il
    legame con il canale va tenuto nello stato locale ``positionCode``."""
    out = []
    for pos in positions:
        if symbol and str(pos.get("symbol", "")).upper() != symbol.upper():
            continue
        if side and str(pos.get("side", "")).upper() != side.upper():
            continue
        out.append(pos)
    return out


# ── util ──────────────────────────────────────────────────────────────────


def _side(value: Any) -> str:
    side = str(value or "").upper()
    if side not in ("BUY", "SELL"):
        raise MappingError(f"side non valido: {value!r}")
    return side


def _sanitize(value: str) -> str:
    return _CODE_SAFE.sub("-", str(value))


def _short_channel(channel_id: str) -> str:
    text = str(channel_id or "").upper()
    if text.startswith("CH_"):
        text = text[3:]
    return _sanitize(text) or "NA"


def _pos_float(value: Any, default: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return num if num > 0 else default


def _opt_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _clean_float(value: float) -> float:
    num = float(value)
    return int(num) if num.is_integer() else round(num, 10)


# ── CLI offline: mostra gli ordini che verrebbero inviati ──────────────────


def plan_signal(
    signal: dict,
    instrument: InstrumentMeta,
    position_codes: dict[int, str] | None = None,
    symbol_map: dict[str, str] | None = None,
    bridge_version: str | None = None,
) -> list[PlannedRequest]:
    """Dispatch per action; ``positions``/``position_codes`` quando servono."""
    action = (signal.get("action") or "").upper()
    if action in ("OPEN", "OPEN_NOW"):
        return plan_open(signal, instrument, symbol_map, bridge_version)
    if action == "UPDATE_OPEN":
        return plan_update_open(
            signal, instrument, position_codes or {}, symbol_map, bridge_version
        )
    raise MappingError(f"action non ancora mappata: {action!r}")


def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys

    parser = argparse.ArgumentParser(
        description="Mostra gli ordini DXtrade generati da un segnale (offline)"
    )
    parser.add_argument("signal", help="file JSON del segnale (signal_ch_*.json)")
    parser.add_argument("--lot-size", type=float, default=100.0)
    parser.add_argument("--price-increment", type=float, default=0.01)
    parser.add_argument("--quantity-increment", type=float, default=1.0)
    parser.add_argument(
        "--position-codes",
        default="",
        help="mappa tp_index:positionCode separata da virgola (es. 1:63649,2:63650)",
    )
    parser.add_argument(
        "--lots",
        type=float,
        default=None,
        help="sovrascrive fixed_lot (utile su segnali vecchi senza il campo)",
    )
    args = parser.parse_args(argv)

    with open(args.signal, "r", encoding="utf-8") as handle:
        signal = json.load(handle)
    if args.lots is not None:
        signal["fixed_lot"] = args.lots
    instrument = InstrumentMeta(
        symbol=map_symbol(signal.get("symbol", "")),
        lot_size=args.lot_size,
        price_increment=args.price_increment,
        quantity_increment=args.quantity_increment,
    )
    codes: dict[int, str] = {}
    for chunk in filter(None, (c.strip() for c in args.position_codes.split(","))):
        idx, _, code = chunk.partition(":")
        codes[int(idx)] = code

    try:
        plans = plan_signal(signal, instrument, codes)
    except MappingError as err:
        print(f"Segnale non mappabile: {err}", file=sys.stderr)
        return 1

    for plan in plans:
        header = f"[{plan.intent}] tp_index={plan.tp_index}"
        if plan.note:
            header += f"  ({plan.note})"
        print(header)
        print(json.dumps(plan.payload, indent=2, ensure_ascii=False))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
