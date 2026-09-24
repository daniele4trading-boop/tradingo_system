"""Client REST DXtrade (DXsca web API) per l'executor.

Stesso contratto verificato da Pattern GO sul conto Velotrade (copiato, non
importato: i due sistemi restano isolati):

    POST /login                          -> {"sessionToken", "timeout"}
    GET  /accounts/{account}/metrics
    GET  /accounts/{account}/positions
    GET  /accounts/{account}/orders
    GET  /instruments/query?symbols=XAU
    POST /marketdata                     -> eventi Quote
    POST /accounts/{account}/orders      -> invio ordine
    DELETE /accounts/{account}/orders/{orderCode}

Velotrade rifiuta gli SL/TP allegati all'ordine d'ingresso (``legs`` /
``orderRequests``), quindi l'executor prova ordini di protezione separati
(``STOP``/``LIMIT`` con ``positionEffect=CLOSE`` e ``positionCode``) e, se il
broker li rifiuta, sorveglia i livelli e chiude a mercato (soft stop).
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

import requests

LOG = logging.getLogger("dx_executor.client")


class DXTradeError(RuntimeError):
    pass


class AuthError(DXTradeError):
    pass


@dataclass(frozen=True)
class Quote:
    symbol: str
    bid: float
    ask: float
    time: datetime

    @property
    def spread(self) -> float:
        return self.ask - self.bid


@dataclass(frozen=True)
class Instrument:
    symbol: str
    price_increment: float
    quantity_increment: float
    lot_size: float


@dataclass(frozen=True)
class AccountMetrics:
    equity: float
    balance: float
    open_pl: float
    open_positions: int
    open_orders: int
    margin_free: float | None = None


class BrokerClient(Protocol):
    """Interfaccia usata dall'executor (implementata dal client REST e dai mock)."""

    def quote(self, symbol: str) -> Quote: ...
    def instrument(self, symbol: str) -> Instrument: ...
    def metrics(self) -> AccountMetrics: ...
    def positions(self) -> list[dict[str, Any]]: ...
    def orders(self) -> list[dict[str, Any]]: ...
    def place_market_order(
        self, symbol: str, side: str, quantity: float, client_order_id: str
    ) -> dict[str, Any]: ...
    def place_protective_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        client_order_id: str,
        position_code: str,
        kind: str,
    ) -> dict[str, Any]: ...
    def cancel_order(self, order_code: str) -> Any: ...
    def close_position(
        self, symbol: str, side: str, quantity: float, client_order_id: str, position_code: str
    ) -> dict[str, Any]: ...


class DXTradeClient:
    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        account: str,
        domain: str = "default",
        timeout: float = 20.0,
        max_retries: int = 5,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.account = account
        self.domain = domain
        self.timeout = timeout
        self.max_retries = max_retries
        self._http = session or requests.Session()
        self._token: str | None = None

    # --- auth ----------------------------------------------------------------

    def login(self) -> str:
        resp = self._http.post(
            f"{self.base_url}/login",
            json={"username": self.username, "domain": self.domain, "password": self.password},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise AuthError(f"login failed: {resp.status_code} {resp.text[:200]}")
        token = resp.json().get("sessionToken")
        if not token:
            raise AuthError("login response without sessionToken")
        self._token = token
        LOG.info("dxtrade login ok")
        return token

    @property
    def token(self) -> str:
        if self._token is None:
            self.login()
        assert self._token is not None
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"DXAPI {self.token}", "Content-Type": "application/json"}

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        url = f"{self.base_url}{path}"
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                resp = self._http.request(
                    method, url, json=json_body, params=params,
                    headers=self._headers(), timeout=self.timeout,
                )
            except requests.RequestException as exc:
                last = exc
                self._sleep_backoff(attempt, f"network error: {exc}")
                continue
            if resp.status_code in (401, 403):
                LOG.warning("sessione scaduta (%s), rifaccio login", resp.status_code)
                self._token = None
                last = AuthError(resp.text[:200])
                self._sleep_backoff(attempt, "auth")
                continue
            if resp.status_code >= 500 or resp.status_code == 429:
                last = DXTradeError(f"{resp.status_code} {resp.text[:200]}")
                self._sleep_backoff(attempt, f"http {resp.status_code}")
                continue
            if resp.status_code >= 400:
                raise DXTradeError(f"{method} {path}: {resp.status_code} {resp.text[:300]}")
            if not resp.content:
                return None
            return resp.json()
        raise DXTradeError(f"{method} {path} failed after {self.max_retries} attempts: {last}")

    def _sleep_backoff(self, attempt: int, reason: str) -> None:
        delay = min(30.0, (2.0**attempt) * 0.5) * (0.75 + random.random() * 0.5)
        LOG.warning("retry %d (%s), attendo %.1fs", attempt + 1, reason, delay)
        time.sleep(delay)

    # --- dati ---------------------------------------------------------------

    def instrument(self, symbol: str) -> Instrument:
        data = self._request("GET", "/instruments/query", params={"symbols": symbol})
        items = (data or {}).get("instruments") or []
        if not items:
            raise DXTradeError(f"strumento {symbol} non trovato")
        it = items[0]
        return Instrument(
            symbol=it["symbol"],
            price_increment=float(it["priceIncrement"]),
            quantity_increment=float(it.get("quantityIncrement", 0.01)),
            lot_size=float(it.get("lotSize", 1.0)),
        )

    def quote(self, symbol: str) -> Quote:
        data = self._request(
            "POST", "/marketdata",
            json_body={
                "account": self.account,
                "symbols": [symbol],
                "eventTypes": [{"type": "Quote", "format": "COMPACT"}],
            },
        )
        for event in (data or {}).get("events", []):
            if event.get("type") == "Quote":
                return Quote(
                    symbol=event["symbol"],
                    bid=float(event["bid"]),
                    ask=float(event["ask"]),
                    time=_parse_time(event["time"]),
                )
        raise DXTradeError(f"nessuna quotazione per {symbol}")

    def metrics(self) -> AccountMetrics:
        data = self._request("GET", f"/accounts/{self.account}/metrics")
        items = (data or {}).get("metrics") or []
        if not items:
            raise DXTradeError("metrics vuote")
        m = items[0]
        return AccountMetrics(
            equity=float(m["equity"]),
            balance=float(m["balance"]),
            open_pl=float(m.get("openPL", 0.0)),
            open_positions=int(m.get("openPositionsCount", 0)),
            open_orders=int(m.get("openOrdersCount", 0)),
            margin_free=float(m["marginFree"]) if m.get("marginFree") is not None else None,
        )

    def positions(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"/accounts/{self.account}/positions")
        return list((data or {}).get("positions") or [])

    def orders(self) -> list[dict[str, Any]]:
        data = self._request("GET", f"/accounts/{self.account}/orders")
        return list((data or {}).get("orders") or [])

    # --- ordini -------------------------------------------------------------

    def place_market_order(
        self, symbol: str, side: str, quantity: float, client_order_id: str
    ) -> dict[str, Any]:
        """Ingresso a mercato. ``client_order_id`` deterministico: il broker rifiuta i
        duplicati, quindi un retry dopo timeout non apre una seconda posizione."""
        body = {
            "account": self.account,
            "orderCode": client_order_id,
            "type": "MARKET",
            "instrument": symbol,
            "quantity": quantity,
            "positionEffect": "OPEN",
            "side": side,
            "tif": "IOC",
        }
        return self._request("POST", f"/accounts/{self.account}/orders", json_body=body) or {}

    def place_protective_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        price: float,
        client_order_id: str,
        position_code: str,
        kind: str,
    ) -> dict[str, Any]:
        """Ordine di chiusura separato: ``kind`` = ``SL`` (STOP) o ``TP`` (LIMIT)."""
        body: dict[str, Any] = {
            "account": self.account,
            "orderCode": client_order_id,
            "type": "STOP" if kind == "SL" else "LIMIT",
            "instrument": symbol,
            "quantity": quantity,
            "positionEffect": "CLOSE",
            "positionCode": position_code,
            "side": _opposite(side),
            "tif": "GTC",
        }
        if kind == "SL":
            body["stopPrice"] = price
        else:
            body["limitPrice"] = price
        return self._request("POST", f"/accounts/{self.account}/orders", json_body=body) or {}

    def cancel_order(self, order_code: str) -> Any:
        return self._request("DELETE", f"/accounts/{self.account}/orders/{order_code}")

    def close_position(
        self, symbol: str, side: str, quantity: float, client_order_id: str, position_code: str
    ) -> dict[str, Any]:
        body = {
            "account": self.account,
            "orderCode": client_order_id,
            "type": "MARKET",
            "instrument": symbol,
            "quantity": quantity,
            "positionEffect": "CLOSE",
            "positionCode": position_code,
            "side": _opposite(side),
            "tif": "IOC",
        }
        return self._request("POST", f"/accounts/{self.account}/orders", json_body=body) or {}


def _opposite(side: str) -> str:
    return "SELL" if side.upper() == "BUY" else "BUY"


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
