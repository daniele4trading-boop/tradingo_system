"""Traduce i payload JSON del bridge (contratto ``docs/EA_SPEC.md``) in operazioni
DXtrade, con le stesse protezioni dell'EA MT5 2.28:

* tolleranza fuori zona all'apertura, livelli fuori scala scartati;
* ``UPDATE_SL`` che allarga il rischio limitato a rischio × fattore;
* BE al livello più protettivo tra fill ed entry del segnale, in coda finché legale;
* TP/SL avversi o già attraversati non applicati;
* guard su heartbeat del bridge, killswitch, perdita giornaliera e drawdown.

In modalità ``paper`` nessun ordine raggiunge il broker: fill e chiusure sono
simulati sulle quotazioni reali del conto e registrati nel journal.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ExecutorConfig
from .dxclient import BrokerClient, DXTradeError, Quote
from .journal import Journal, Leg, Setup, iso, utc_now

LOG = logging.getLogger("dx_executor")

OPEN_ACTIONS = ("OPEN", "OPEN_NOW", "UPDATE_OPEN")


class Executor:
    def __init__(self, cfg: ExecutorConfig, client: BrokerClient, journal: Journal) -> None:
        self.cfg = cfg
        self.client = client
        self.journal = journal
        self.state = journal.state
        self.live = cfg.is_live
        self._last_quote: dict[str, Quote] = {}
        if cfg.execution.mode == "live" and not self.live:
            raise RuntimeError("mode=live richiede execution.live_orders_ack=I_AUTHORIZE_LIVE_ORDERS")
        if not self.live and self.state.paper_balance == 0.0 and not self.state.setups:
            self.state.paper_balance = cfg.risk.initial_balance

    # ------------------------------------------------------------------ ciclo

    def tick(self) -> None:
        self._refresh_equity_baselines()
        payload = self._read_signal()
        if payload is not None:
            try:
                self.process_signal(payload)
            finally:
                self._clear_signal()
                self.journal.save()
        self.manage()
        self._write_status()

    def _read_signal(self) -> dict | None:
        path = self.cfg.signals_dir / self.cfg.paths.signal_file
        if not path.exists():
            return None
        try:
            text = path.read_text(encoding="utf-8-sig").strip()
        except OSError:
            return None
        if not text:
            return None
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return None  # scrittura in corso: riprova al prossimo giro
        action = str(payload.get("action") or "NONE")
        if action == "NONE":
            return None
        return payload

    def _clear_signal(self) -> None:
        # Come l'EA (InpClearSignalAfterProcess): un riavvio non rigioca il JSON.
        path = self.cfg.signals_dir / self.cfg.paths.signal_file
        tmp = path.with_suffix(".dxtmp")
        try:
            tmp.write_text('{"action":"NONE"}\n', encoding="utf-8")
            os.replace(tmp, path)
        except OSError as exc:
            LOG.warning("clear signal file fallito: %s", exc)

    # ----------------------------------------------------------- dispatcher

    def process_signal(self, payload: dict) -> None:
        action = str(payload.get("action", "NONE")).upper()
        key = "|".join(
            str(payload.get(k, "")) for k in ("timestamp", "message_id", "event_type", "action")
        )
        if key in self.state.processed:
            self.journal.event("DUPLICATE_IGNORED", key=key)
            return
        self.state.processed[key] = iso(utc_now())

        symbol = self._map_symbol(payload.get("symbol"))
        if payload.get("symbol") and not symbol and action != "CLOSE_ALL_SYMBOL":
            self.journal.event("SYMBOL_IGNORED", symbol=payload.get("symbol"), action=action)
            return
        self.journal.event(
            "SIGNAL", action=action, symbol=symbol, signal_id=payload.get("signal_id"),
            direction=payload.get("direction"), entry=payload.get("entry"),
            sl=payload.get("sl"), tp_levels=payload.get("tp_levels"),
        )
        try:
            if action in OPEN_ACTIONS:
                self._handle_open(payload, symbol, action)
            elif action == "UPDATE_SL":
                self._handle_update_sl(payload, symbol)
            elif action == "UPDATE_TP":
                self._handle_update_tp(payload, symbol)
            elif action == "CHECK_AND_BE":
                self._handle_be(symbol, float(payload.get("be_price") or 0.0), use_signal_entry=True)
            elif action == "BREAK_EVEN_PRICE":
                self._handle_be_fixed(symbol, float(payload.get("be_price") or 0.0))
            elif action == "CLOSE_HALF_BE":
                self._handle_close_half_be(symbol)
            elif action == "CHECK_AND_CLOSE":
                self._close_legs(
                    [(s, leg) for s, leg in self.state.open_legs(symbol)
                     if s.direction == str(payload.get("direction", "")).upper()],
                    "CHECK_AND_CLOSE",
                )
            elif action == "CLOSE_ALL_SYMBOL":
                self._close_legs(self.state.open_legs(symbol or None), "CLOSE_ALL_SYMBOL")
            elif action == "CLOSE_SELECTIVE":
                self._handle_close_selective(payload, symbol)
            elif action == "CHECK_AND_CLOSE_TP":
                idx = int(payload.get("tp_index") or 1)
                self._close_legs(
                    [(s, leg) for s, leg in self.state.open_legs(symbol) if leg.tp_index == idx],
                    f"CHECK_AND_CLOSE_TP_{idx}",
                )
            else:
                self.journal.event("UNKNOWN_ACTION", action=action)
        except DXTradeError as exc:
            LOG.error("azione %s fallita: %s", action, exc)
            self.journal.event("ACTION_ERROR", action=action, error=str(exc))

    # ----------------------------------------------------------------- open

    def _handle_open(self, payload: dict, symbol: str, action: str) -> None:
        if not symbol:
            return
        signal_id = str(payload.get("signal_id") or payload.get("timestamp") or "")
        direction = str(payload.get("direction", "")).upper()
        if direction not in ("BUY", "SELL"):
            self.journal.event("OPEN_REJECTED", reason="direction", signal_id=signal_id)
            return
        existing = self.state.find_setup(signal_id) if signal_id else None
        if existing and existing.open_legs and action == "UPDATE_OPEN":
            self._apply_levels_to_setup(existing, payload)
            return
        if existing and existing.open_legs and not payload.get("allow_stack"):
            self.journal.event("OPEN_SKIPPED_DUPLICATE", signal_id=signal_id)
            return

        blocked = self._open_blocked()
        if blocked:
            self.journal.event("OPEN_BLOCKED", reason=blocked, signal_id=signal_id)
            return

        quote = self._quote(symbol)
        ref = quote.ask if direction == "BUY" else quote.bid
        entry = float(payload.get("entry") or 0.0)
        zone = payload.get("entry_range") or ([entry, entry] if entry > 0 else [ref, ref])
        lo, hi = sorted(float(z) for z in zone[:2])
        dist = 0.0 if lo <= ref <= hi else min(abs(ref - lo), abs(ref - hi))
        if dist > self.cfg.execution.off_market_tolerance:
            self.journal.event(
                "SIGNAL_CANCELLED", reason="off_market", signal_id=signal_id,
                price=ref, zone=[lo, hi], distance=round(dist, 2),
            )
            return

        sl = self._sane_level(float(payload.get("sl") or 0.0), ref)
        tps = [self._sane_level(float(t), ref) for t in (payload.get("tp_levels") or [])]
        tps = [t for t in tps if t > 0 and ((t > ref) if direction == "BUY" else (t < ref))]
        if sl > 0 and ((sl >= ref) if direction == "BUY" else (sl <= ref)):
            self.journal.event("SIGNAL_CANCELLED", reason="sl_crossed", signal_id=signal_id, sl=sl, price=ref)
            return
        n_legs = len(tps) or int(payload.get("trades") or 1)
        n_legs = max(1, min(n_legs, self.cfg.execution.max_legs_per_setup))
        open_now = len(self.state.open_legs())
        if open_now + n_legs > self.cfg.risk.max_open_legs:
            self.journal.event("OPEN_BLOCKED", reason="max_open_legs", open=open_now, wanted=n_legs)
            return
        qty = self._leg_quantity(float(payload.get("lot_factor") or 1.0))

        setup = Setup(
            signal_id=signal_id or f"anon-{iso(utc_now())}", symbol=symbol,
            direction=direction, entry=entry or ref, opened_at=iso(utc_now()),
        )
        for i in range(n_legs):
            tp = tps[i] if i < len(tps) else 0.0
            leg = self._open_leg(setup, i + 1, qty, sl, tp, quote)
            if leg is not None:
                setup.legs.append(leg)
        if setup.legs:
            self.state.setups.append(setup)
        self.journal.event(
            "SETUP_OPENED", signal_id=setup.signal_id, symbol=symbol, direction=direction,
            legs=len(setup.legs), quantity=qty, sl=sl, tps=tps, mode=self._mode_name(),
        )

    def _open_leg(self, setup: Setup, tp_index: int, qty: float, sl: float, tp: float, quote: Quote) -> Leg | None:
        side = setup.direction
        client_id = self.state.next_client_id(f"O{tp_index}")
        fill = quote.ask if side == "BUY" else quote.bid
        leg = Leg(
            tp_index=tp_index, quantity=qty, side=side, fill=fill, sl=sl, tp=tp,
            opened_at=iso(utc_now()), client_id=client_id,
        )
        if not self.live:
            self.journal.event("PAPER_FILL", client_id=client_id, tp_index=tp_index, price=fill, quantity=qty)
            return leg
        before = {str(p.get("positionCode")) for p in self._symbol_positions(setup.symbol)}
        try:
            self.client.place_market_order(setup.symbol, side, qty, client_id)
        except DXTradeError as exc:
            self.journal.event("ORDER_REJECTED", client_id=client_id, error=str(exc))
            return None
        pos = self._find_new_position(setup.symbol, side, before)
        if pos is None:
            self.journal.event("FILL_NOT_FOUND", client_id=client_id)
            return None
        leg.position_code = str(pos.get("positionCode") or pos.get("id"))
        leg.fill = float(pos.get("openPrice") or fill)
        self.journal.event("FILL", client_id=client_id, position_code=leg.position_code, price=leg.fill)
        self._protect(setup, leg)
        return leg

    def _find_new_position(self, symbol: str, side: str, before: set[str]) -> dict | None:
        for p in self._symbol_positions(symbol):
            code = str(p.get("positionCode") or p.get("id"))
            if code not in before and str(p.get("side", side)).upper() == side:
                return p
        return None

    def _protect(self, setup: Setup, leg: Leg) -> None:
        """Piazza SL/TP come ordini separati; se il broker li rifiuta resta il soft stop."""
        if not self.live or not self.cfg.execution.hard_protection:
            leg.soft = True
            return
        leg.sl_order = self._replace_protective(setup, leg, "SL", leg.sl, leg.sl_order)
        leg.tp_order = self._replace_protective(setup, leg, "TP", leg.tp, leg.tp_order)
        leg.soft = not (leg.sl_order or leg.tp_order)

    def _replace_protective(self, setup: Setup, leg: Leg, kind: str, price: float, old_order: str) -> str:
        if old_order:
            try:
                self.client.cancel_order(old_order)
            except DXTradeError as exc:
                self.journal.event("CANCEL_FAILED", order=old_order, error=str(exc))
        if price <= 0:
            return ""
        cid = self.state.next_client_id(kind)
        try:
            resp = self.client.place_protective_order(
                setup.symbol, leg.side, leg.quantity, price, cid, leg.position_code, kind
            )
        except DXTradeError as exc:
            self.journal.event("PROTECTION_REJECTED", order_kind=kind, position_code=leg.position_code, error=str(exc))
            return ""
        return str(resp.get("orderCode") or cid)

    # -------------------------------------------------------------- modifiche

    def _apply_levels_to_setup(self, setup: Setup, payload: dict) -> None:
        quote = self._quote(setup.symbol)
        new_sl = float(payload.get("sl") or payload.get("new_sl") or 0.0)
        tps = [float(t) for t in (payload.get("tp_levels") or [])]
        for leg in setup.open_legs:
            sl = self._guarded_sl(leg, new_sl, quote) if new_sl > 0 else leg.sl
            tp = leg.tp
            if leg.tp_index - 1 < len(tps):
                cand = self._sane_level(tps[leg.tp_index - 1], leg.fill)
                if cand > 0 and self._tp_profitable(leg, cand):
                    tp = cand
            self._set_levels(setup, leg, sl, tp, "UPDATE_OPEN")
        if len(tps) > len(setup.legs):
            self.journal.event("UPDATE_OPEN_DRIFT_GUARD", signal_id=setup.signal_id, legs=len(setup.legs), tps=len(tps))

    def _handle_update_sl(self, payload: dict, symbol: str) -> None:
        new_sl = float(payload.get("new_sl") or payload.get("sl") or 0.0)
        if new_sl <= 0:
            return
        quote = self._quote(symbol)
        for setup, leg in self.state.open_legs(symbol):
            sl = self._guarded_sl(leg, new_sl, quote)
            if sl != leg.sl:
                self._set_levels(setup, leg, sl, leg.tp, "UPDATE_SL")

    def _handle_update_tp(self, payload: dict, symbol: str) -> None:
        new_tp = float(payload.get("new_tp") or 0.0)
        tps = payload.get("tp_levels") or []
        if new_tp <= 0 and tps:
            new_tp = float(tps[0])
        if new_tp <= 0:
            return
        tp_index = int(payload.get("tp_index") or 0)
        for setup, leg in self.state.open_legs(symbol):
            if tp_index > 0 and leg.tp_index != tp_index:
                continue
            tp = self._sane_level(new_tp, leg.fill)
            if tp <= 0 or not self._tp_profitable(leg, tp):
                self.journal.event("MODIFY_SKIPPED_ADVERSE_TP", client_id=leg.client_id, tp=new_tp)
                continue
            self._set_levels(setup, leg, leg.sl, tp, "UPDATE_TP")

    def _handle_be(self, symbol: str, signal_entry: float, use_signal_entry: bool) -> None:
        quote = self._quote(symbol)
        for setup, leg in self.state.open_legs(symbol):
            target = self._be_target(leg, signal_entry if use_signal_entry else 0.0)
            self._apply_be(setup, leg, target, quote)

    def _handle_be_fixed(self, symbol: str, be: float) -> None:
        if be <= 0:
            return
        quote = self._quote(symbol)
        for setup, leg in self.state.open_legs(symbol):
            self._apply_be(setup, leg, be, quote)

    def _handle_close_half_be(self, symbol: str) -> None:
        quote = self._quote(symbol)
        inc = self.cfg.execution.quantity_increment
        for setup, leg in self.state.open_legs(symbol):
            half = round(int(leg.quantity / 2 / inc) * inc, 4)
            if half >= inc and half < leg.quantity:
                self._close_partial(setup, leg, half, quote, "CLOSE_HALF")
            self._apply_be(setup, leg, self._be_target(leg, 0.0), quote)

    def _be_target(self, leg: Leg, signal_entry: float) -> float:
        if signal_entry <= 0 or abs(signal_entry - leg.fill) > self.cfg.execution.be_signal_entry_max_gap:
            return leg.fill
        return max(leg.fill, signal_entry) if leg.side == "BUY" else min(leg.fill, signal_entry)

    def _apply_be(self, setup: Setup, leg: Leg, target: float, quote: Quote) -> None:
        # Mai peggiorare uno stop già più protettivo.
        if leg.sl > 0 and ((target <= leg.sl) if leg.side == "BUY" else (target >= leg.sl)):
            self.journal.event("BE_ALREADY_BETTER", client_id=leg.client_id, sl=leg.sl, target=target)
            return
        legal = (target < quote.bid) if leg.side == "BUY" else (target > quote.ask)
        if not legal:
            leg.pending_be = target
            self.journal.event("BE_PENDING", client_id=leg.client_id, target=target, bid=quote.bid, ask=quote.ask)
            return
        leg.pending_be = 0.0
        self._set_levels(setup, leg, target, leg.tp, "BREAK_EVEN")

    def _guarded_sl(self, leg: Leg, new_sl: float, quote: Quote) -> float:
        """Stop che si allontana seguito al massimo fino a rischio×fattore; stop già
        attraversato dal mercato ignorato (lo gestisce la chiusura)."""
        is_buy = leg.side == "BUY"
        if (new_sl >= quote.bid) if is_buy else (new_sl <= quote.ask):
            self.journal.event("MODIFY_SKIPPED_CROSSED_SL", client_id=leg.client_id, sl=new_sl)
            return leg.sl
        factor = self.cfg.execution.max_sl_widen_factor
        if leg.sl > 0 and factor > 0 and ((new_sl < leg.sl) if is_buy else (new_sl > leg.sl)):
            max_risk = abs(leg.fill - leg.sl) * factor
            if abs(leg.fill - new_sl) > max_risk:
                capped = round(leg.fill - max_risk if is_buy else leg.fill + max_risk, 2)
                self.journal.event("UPDATE_SL_WIDEN_CAPPED", client_id=leg.client_id, new_sl=new_sl, applied=capped)
                return capped
        return new_sl

    @staticmethod
    def _tp_profitable(leg: Leg, tp: float) -> bool:
        return tp > leg.fill if leg.side == "BUY" else tp < leg.fill

    def _set_levels(self, setup: Setup, leg: Leg, sl: float, tp: float, reason: str) -> None:
        if sl == leg.sl and tp == leg.tp:
            return
        leg.sl, leg.tp = sl, tp
        if self.live and not leg.soft:
            self._protect(setup, leg)
        self.journal.event("LEVELS_SET", reason=reason, client_id=leg.client_id, sl=sl, tp=tp, soft=leg.soft)

    # --------------------------------------------------------------- chiusure

    def _handle_close_selective(self, payload: dict, symbol: str) -> None:
        keep = str(payload.get("keep", "")).upper()
        legs = self.state.open_legs(symbol)
        if not legs:
            return
        if keep == "ALL_BUT_NEWEST":
            newest = max(s.opened_ts for s, _ in legs)
            block = [(s, leg) for s, leg in legs if newest - s.opened_ts <= self.cfg.execution.batch_window_sec]
            if len(block) == len(legs):
                self.journal.event("CLOSE_SELECTIVE_SKIPPED", reason="single_block")
                return
            self._close_legs(block, "CLOSE_SELECTIVE_ALL_BUT_NEWEST")
            return
        if keep not in ("BEST", "HIGHEST", "LOWEST"):
            self.journal.event("CLOSE_SELECTIVE_SKIPPED", reason=f"keep={keep}")
            return
        keep_higher = keep == "HIGHEST" or (keep == "BEST" and legs[0][0].direction == "SELL")
        keep_price = (max if keep_higher else min)(leg.fill for _, leg in legs)
        self._close_legs([(s, leg) for s, leg in legs if abs(leg.fill - keep_price) > 0.05], f"CLOSE_SELECTIVE_{keep}")

    def _close_legs(self, legs: list[tuple[Setup, Leg]], reason: str) -> None:
        for setup, leg in legs:
            self._close_leg(setup, leg, self._quote(setup.symbol), reason)

    def _close_leg(self, setup: Setup, leg: Leg, quote: Quote, reason: str, price: float | None = None) -> None:
        exit_price = price if price is not None else (quote.bid if leg.side == "BUY" else quote.ask)
        if self.live:
            for order in (leg.sl_order, leg.tp_order):
                if order:
                    try:
                        self.client.cancel_order(order)
                    except DXTradeError as exc:
                        self.journal.event("CANCEL_FAILED", order=order, error=str(exc))
            if price is None:
                cid = self.state.next_client_id("C")
                try:
                    self.client.close_position(setup.symbol, leg.side, leg.quantity, cid, leg.position_code)
                except DXTradeError as exc:
                    self.journal.event("CLOSE_FAILED", client_id=leg.client_id, error=str(exc))
                    return
        self._mark_closed(leg, exit_price, reason)

    def _close_partial(self, setup: Setup, leg: Leg, qty: float, quote: Quote, reason: str) -> None:
        exit_price = quote.bid if leg.side == "BUY" else quote.ask
        if self.live:
            cid = self.state.next_client_id("P")
            try:
                self.client.close_position(setup.symbol, leg.side, qty, cid, leg.position_code)
            except DXTradeError as exc:
                self.journal.event("CLOSE_FAILED", client_id=leg.client_id, error=str(exc))
                return
        pnl = self._pnl(leg, exit_price, qty)
        leg.quantity = round(leg.quantity - qty, 4)
        self.state.paper_balance += pnl if not self.live else 0.0
        self.journal.event("PARTIAL_CLOSED", client_id=leg.client_id, quantity=qty, price=exit_price, pnl=round(pnl, 4), reason=reason)
        if self.live and not leg.soft:
            self._protect(setup, leg)

    def _mark_closed(self, leg: Leg, exit_price: float, reason: str) -> None:
        leg.status = "CLOSED"
        leg.close_price = exit_price
        leg.close_reason = reason
        leg.closed_at = iso(utc_now())
        leg.pnl = round(self._pnl(leg, exit_price, leg.quantity), 4)
        if not self.live:
            self.state.paper_balance += leg.pnl
        self.journal.event(
            "LEG_CLOSED", client_id=leg.client_id, tp_index=leg.tp_index, reason=reason,
            fill=leg.fill, price=exit_price, pnl=leg.pnl, mode=self._mode_name(),
        )

    @staticmethod
    def _pnl(leg: Leg, exit_price: float, qty: float) -> float:
        sign = 1.0 if leg.side == "BUY" else -1.0
        return (exit_price - leg.fill) * sign * qty

    # ------------------------------------------------------------- gestione

    def manage(self) -> None:
        """Sorveglia SL/TP soft, BE in coda e chiusure avvenute lato broker."""
        symbols = {s.symbol for s, _ in self.state.open_legs()}
        if not symbols:
            return
        changed = False
        for symbol in symbols:
            try:
                quote = self._quote(symbol)
            except DXTradeError as exc:
                LOG.warning("quote %s non disponibile: %s", symbol, exc)
                continue
            broker_codes: set[str] | None = None
            if self.live:
                try:
                    broker_codes = {str(p.get("positionCode") or p.get("id")) for p in self._symbol_positions(symbol)}
                except DXTradeError:
                    broker_codes = None
            for setup, leg in self.state.open_legs(symbol):
                changed = True
                if broker_codes is not None and leg.position_code and leg.position_code not in broker_codes:
                    self._reconcile_broker_close(setup, leg, quote)
                    continue
                if leg.pending_be > 0:
                    self._apply_be(setup, leg, leg.pending_be, quote)
                if leg.soft or not self.live:
                    self._check_soft_levels(setup, leg, quote)
        if changed:
            self._check_drawdown_guard()
            self.journal.save()

    def _check_soft_levels(self, setup: Setup, leg: Leg, quote: Quote) -> None:
        mark = quote.bid if leg.side == "BUY" else quote.ask
        sl_hit = leg.sl > 0 and ((mark <= leg.sl) if leg.side == "BUY" else (mark >= leg.sl))
        tp_hit = leg.tp > 0 and ((mark >= leg.tp) if leg.side == "BUY" else (mark <= leg.tp))
        if sl_hit:
            self._close_leg(setup, leg, quote, "SL", price=None if self.live else leg.sl)
        elif tp_hit:
            self._close_leg(setup, leg, quote, "TP", price=None if self.live else leg.tp)

    def _reconcile_broker_close(self, setup: Setup, leg: Leg, quote: Quote) -> None:
        remaining = set()
        try:
            remaining = {str(o.get("orderCode")) for o in self.client.orders()}
        except DXTradeError:
            pass
        reason = "BROKER_CLOSE"
        if leg.sl_order and leg.sl_order not in remaining and (not leg.tp_order or leg.tp_order in remaining):
            reason, price = "SL", leg.sl
        elif leg.tp_order and leg.tp_order not in remaining:
            reason, price = "TP", leg.tp
        else:
            price = quote.bid if leg.side == "BUY" else quote.ask
        for order in (leg.sl_order, leg.tp_order):
            if order and order in remaining:
                try:
                    self.client.cancel_order(order)
                except DXTradeError as exc:
                    self.journal.event("CANCEL_FAILED", order=order, error=str(exc))
        self._mark_closed(leg, price, reason)

    # ---------------------------------------------------------------- guard

    def _open_blocked(self) -> str:
        if (self.cfg.signals_dir / self.cfg.paths.killswitch_file).exists():
            return "killswitch"
        hb_age = self._heartbeat_age()
        if self.cfg.risk.heartbeat_max_age_sec > 0 and hb_age is not None and hb_age > self.cfg.risk.heartbeat_max_age_sec:
            return f"heartbeat_stale_{int(hb_age)}s"
        used = self._allowance_used()
        if used >= self.cfg.risk.block_new_at_allowance_used:
            return f"risk_allowance_{used:.0%}"
        return ""

    def _heartbeat_age(self) -> float | None:
        path = self.cfg.signals_dir / self.cfg.paths.heartbeat_file
        try:
            raw = json.loads(path.read_text(encoding="utf-8-sig"))
            ts = datetime.fromisoformat(str(raw["ts_utc"]).replace("Z", "+00:00"))
        except (OSError, ValueError, KeyError):
            return None
        return (utc_now() - ts.astimezone(timezone.utc)).total_seconds()

    def equity(self) -> float:
        if self.live:
            return self.client.metrics().equity
        floating = 0.0
        for setup, leg in self.state.open_legs():
            q = self._last_quote.get(setup.symbol)
            if q:
                floating += self._pnl(leg, q.bid if leg.side == "BUY" else q.ask, leg.quantity)
        return self.state.paper_balance + floating

    def _refresh_equity_baselines(self) -> None:
        try:
            eq = self.equity()
        except DXTradeError:
            return
        today = utc_now().strftime("%Y-%m-%d")
        if self.state.day != today:
            self.state.day, self.state.day_start_equity = today, eq
        self.state.peak_equity = max(self.state.peak_equity, eq)

    def _allowance_used(self) -> float:
        """Quota consumata del limite più stretto tra perdita giornaliera e DD totale."""
        try:
            eq = self.equity()
        except DXTradeError:
            return 0.0
        base = self.cfg.risk.initial_balance or self.state.day_start_equity
        if base <= 0:
            return 0.0
        used = 0.0
        if self.cfg.risk.daily_loss_pct > 0 and self.state.day_start_equity > 0:
            used = max(used, (self.state.day_start_equity - eq) / (base * self.cfg.risk.daily_loss_pct))
        if self.cfg.risk.max_dd_pct > 0:
            used = max(used, (base - eq) / (base * self.cfg.risk.max_dd_pct))
        return max(0.0, used)

    def _check_drawdown_guard(self) -> None:
        used = self._allowance_used()
        if used >= self.cfg.risk.close_all_at_allowance_used and self.state.open_legs():
            self.journal.event("EMERGENCY_CLOSE", allowance_used=round(used, 3))
            self._close_legs(self.state.open_legs(), "EMERGENCY_DD")

    # -------------------------------------------------------------- utility

    def _map_symbol(self, raw: Any) -> str:
        if not raw:
            return ""
        sym = str(raw).upper()
        for src, dst in self.cfg.broker.symbol_map.items():
            if sym.startswith(src.upper()):
                return dst
        return ""

    def _quote(self, symbol: str) -> Quote:
        q = self.client.quote(symbol)
        self._last_quote[symbol] = q
        return q

    def _symbol_positions(self, symbol: str) -> list[dict]:
        return [p for p in self.client.positions() if str(p.get("symbol", "")).upper() == symbol.upper()]

    def _sane_level(self, level: float, ref: float) -> float:
        pct = self.cfg.execution.max_level_deviation_pct
        if level <= 0 or ref <= 0:
            return 0.0
        if pct > 0 and abs(level - ref) / ref * 100.0 > pct:
            self.journal.event("LEVEL_OUT_OF_SCALE", level=level, price=ref)
            return 0.0
        return level

    def _leg_quantity(self, factor: float) -> float:
        inc = self.cfg.execution.quantity_increment
        qty = self.cfg.execution.quantity_per_tp * (factor if factor > 0 else 1.0)
        return max(inc, round(round(qty / inc) * inc, 4))

    def _mode_name(self) -> str:
        return "live" if self.live else "paper"

    def _write_status(self) -> None:
        try:
            eq = self.equity()
        except DXTradeError:
            eq = 0.0
        status = {
            "ts_utc": iso(utc_now()),
            "mode": self._mode_name(),
            "equity": round(eq, 2),
            "paper_balance": round(self.state.paper_balance, 2),
            "open_legs": len(self.state.open_legs()),
            "blocked": self._open_blocked(),
            "quotes": {s: {"bid": q.bid, "ask": q.ask, "time": iso(q.time)} for s, q in self._last_quote.items()},
        }
        path = self.journal.state_dir / "dx_executor_status.json"
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(status, indent=2), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass


def default_state_dir(cfg: ExecutorConfig) -> Path:
    return cfg.state_dir
