"""Client REST DXtrade (dxSCA) — solo stdlib, nessuna dipendenza nuova.

API di riferimento: ``https://<host>/dxsca-web`` + doc broker su
``https://<host>/developers/`` (DXtrade REST API Specification).

Autenticazione a token: ``POST /login`` -> ``sessionToken``, poi header
``Authorization: DXAPI <token>``. Il token scade per inattività: si rinnova
con ``POST /ping`` oppure, su 401, con un nuovo login trasparente.

Il client non conosce i segnali TradinGo: traduce solo chiamate HTTP.
La traduzione segnale -> ordini sta in ``dxtrade_mapper.py``.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Iterable, Sequence

log = logging.getLogger("TradinGo.dxtrade")

DEFAULT_TIMEOUT_SEC = 15.0
# Limiti standard dichiarati dalla piattaforma: 10 req/s trading e reading,
# 1 req/s login. Restiamo larghi: i segnali TradinGo sono ~40 al giorno.
DEFAULT_MIN_INTERVAL_SEC = 0.12
DEFAULT_RETRIES = 3

# Error code DXtrade usati per decisioni di controllo.
ERR_ENTITY_NOT_FOUND = 2
ERR_AUTH_FAILED = 3
ERR_BAD_REQUEST = 33
ERR_DUPLICATE_ORDER_ID = 100


class DXTradeError(Exception):
    """Errore applicativo restituito dalla piattaforma."""

    def __init__(self, status: int, code: int | None, message: str, body: Any = None):
        super().__init__(f"HTTP {status} code={code}: {message}")
        self.status = status
        self.code = code
        self.message = message
        self.body = body

    @property
    def is_duplicate_order(self) -> bool:
        return self.status == 409 and self.code == ERR_DUPLICATE_ORDER_ID

    @property
    def is_api_not_permitted(self) -> bool:
        """404/2 arriva sia per entità inesistente sia per API non abilitata."""
        return self.status == 404 and self.code == ERR_ENTITY_NOT_FOUND


class DXTradeAuthError(DXTradeError):
    pass


class DXTradeClient:
    """Client sincrono minimale per il sottoinsieme di API che ci serve."""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        domain: str = "default",
        account: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SEC,
        retries: int = DEFAULT_RETRIES,
        min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC,
        dry_run: bool = False,
        opener: Any = None,
        sleep=time.sleep,
        monotonic=time.monotonic,
    ):
        if not base_url:
            raise ValueError("base_url is required (es. https://dx.broker.com/dxsca-web)")
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.domain = domain
        self.account = account
        self.timeout = timeout
        self.retries = max(1, int(retries))
        self.min_interval_sec = max(0.0, float(min_interval_sec))
        self.dry_run = bool(dry_run)
        self._opener = opener or urllib.request.build_opener()
        self._sleep = sleep
        self._monotonic = monotonic
        self._token: str | None = None
        self._token_timeout_ms: int | None = None
        self._last_call_at: float = 0.0
        self.last_etag: str | None = None

    # ── sessione ──────────────────────────────────────────────────────────

    @property
    def token(self) -> str | None:
        return self._token

    def login(self) -> str:
        payload = {
            "username": self.username,
            "domain": self.domain,
            "password": self.password,
        }
        body, _ = self._raw_request(
            "POST", "/login", payload, authenticated=False, retries=1
        )
        token = (body or {}).get("sessionToken")
        if not token:
            raise DXTradeAuthError(200, None, "login senza sessionToken", body)
        self._token = token
        timeout = (body or {}).get("timeout")
        try:
            self._token_timeout_ms = int(timeout) if timeout is not None else None
        except (TypeError, ValueError):
            self._token_timeout_ms = None
        log.info("DXtrade login ok (timeout=%sms)", self._token_timeout_ms)
        return token

    def ping(self) -> None:
        self._request("POST", "/ping", {})

    def logout(self) -> None:
        if not self._token:
            return
        try:
            self._request("POST", "/logout", {})
        finally:
            self._token = None

    def __enter__(self) -> "DXTradeClient":
        self.login()
        return self

    def __exit__(self, *exc) -> None:
        try:
            self.logout()
        except Exception as err:  # logout best-effort
            log.warning("DXtrade logout fallito: %s", err)

    # ── reference data ────────────────────────────────────────────────────

    def instrument(self, symbol: str) -> dict:
        body, _ = self._request("GET", f"/instruments/{_seg(symbol)}")
        return _first_instrument(body) or {}

    def instruments_query(self, symbols: Sequence[str]) -> list[dict]:
        query = urllib.parse.urlencode({"symbols": ",".join(symbols)})
        body, _ = self._request("GET", f"/instruments/query?{query}")
        return _instrument_list(body)

    def account_instrument(self, symbol: str, account: str | None = None) -> dict:
        acct = self._account(account)
        body, _ = self._request(
            "GET", f"/accounts/{_seg(acct)}/instruments/{_seg(symbol)}"
        )
        return _first_instrument(body) or {}

    def market_data(
        self,
        symbols: Sequence[str],
        event_types: Iterable[str] = ("Quote",),
        account: str | None = None,
    ) -> dict:
        payload: dict[str, Any] = {
            "account": self._account(account),
            "symbols": list(symbols),
            "eventTypes": [{"type": t} for t in event_types],
        }
        body, _ = self._request("POST", "/marketdata", payload)
        return body or {}

    # ── account ───────────────────────────────────────────────────────────

    def users(self) -> dict:
        body, _ = self._request("GET", "/users")
        return body or {}

    def positions(self, account: str | None = None) -> list[dict]:
        acct = self._account(account)
        body, etag = self._request("GET", f"/accounts/{_seg(acct)}/positions")
        self.last_etag = etag
        return _as_list(body, "positions")

    def orders(self, account: str | None = None) -> list[dict]:
        acct = self._account(account)
        body, _ = self._request("GET", f"/accounts/{_seg(acct)}/orders")
        return _as_list(body, "orders")

    def orders_history(self, account: str | None = None, **params) -> list[dict]:
        acct = self._account(account)
        path = f"/accounts/{_seg(acct)}/orders/history"
        if params:
            path = f"{path}?{urllib.parse.urlencode(params)}"
        body, _ = self._request("GET", path)
        return _as_list(body, "orders")

    def portfolio(self, account: str | None = None) -> dict:
        acct = self._account(account)
        body, _ = self._request("GET", f"/accounts/{_seg(acct)}/portfolio")
        return body or {}

    def metrics(self, account: str | None = None) -> dict:
        acct = self._account(account)
        body, _ = self._request("GET", f"/accounts/{_seg(acct)}/metrics")
        return body or {}

    # ── trading ───────────────────────────────────────────────────────────

    def place_order(self, payload: dict, account: str | None = None) -> dict:
        """POST /accounts/{account}/orders.

        Il payload può essere un singolo ordine o un gruppo (``orders`` +
        ``contingencyType``). ``orderCode`` deve essere univoco per account:
        è la nostra chiave di idempotenza, quindi un retry su duplicato viene
        riportato come ``{"duplicate": true}`` invece di sollevare.
        """
        acct = self._account(account)
        try:
            body, _ = self._request(
                "POST", f"/accounts/{_seg(acct)}/orders", payload
            )
        except DXTradeError as err:
            if err.is_duplicate_order:
                log.warning(
                    "DXtrade ordine duplicato (orderCode già presente): %s", err.message
                )
                return {"duplicate": True, "error": err.message}
            raise
        return body if isinstance(body, dict) else {"result": body}

    def modify_order(
        self, payload: dict, etag: str | None = None, account: str | None = None
    ) -> dict:
        """PUT /accounts/{account}/orders (richiede richiesta condizionale)."""
        acct = self._account(account)
        headers = {"If-Match": etag} if etag else None
        body, _ = self._request(
            "PUT", f"/accounts/{_seg(acct)}/orders", payload, headers=headers
        )
        return body if isinstance(body, dict) else {"result": body}

    def cancel_order(self, order_code: str, account: str | None = None) -> dict:
        acct = self._account(account)
        body, _ = self._request(
            "DELETE", f"/accounts/{_seg(acct)}/orders/{_seg(order_code)}"
        )
        return body if isinstance(body, dict) else {"result": body}

    def bulk_close(
        self,
        instrument: str | None = None,
        close_positions: bool = True,
        cancel_orders: bool = False,
        comment: str | None = None,
        account: str | None = None,
    ) -> dict:
        """POST /accounts/{account}/close.

        Attenzione: filtra solo per strumento, non per canale. Con più canali
        sullo stesso simbolo (XAUUSD) chiuderebbe anche le posizioni degli
        altri: usare la chiusura per ``positionCode``.
        """
        acct = self._account(account)
        payload: dict[str, Any] = {
            "closePositions": bool(close_positions),
            "cancelOrders": bool(cancel_orders),
        }
        if instrument:
            payload["instrument"] = instrument
        if comment:
            payload["comment"] = comment
        body, _ = self._request("POST", f"/accounts/{_seg(acct)}/close", payload)
        return body if isinstance(body, dict) else {"result": body}

    # ── interno ───────────────────────────────────────────────────────────

    def _account(self, account: str | None) -> str:
        acct = account or self.account
        if not acct:
            raise ValueError("account code non configurato")
        return acct

    def _request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        retries: int | None = None,
    ) -> tuple[Any, str | None]:
        mutating = method in ("POST", "PUT", "DELETE") and path not in (
            "/login",
            "/ping",
            "/logout",
            "/marketdata",
        )
        if mutating and self.dry_run:
            log.info("[DRY-RUN] %s %s payload=%s", method, path, json.dumps(payload or {}))
            return {"dryRun": True, "method": method, "path": path, "payload": payload}, None
        if not self._token:
            self.login()
        return self._raw_request(
            method, path, payload, headers=headers, retries=retries
        )

    def _raw_request(
        self,
        method: str,
        path: str,
        payload: Any = None,
        headers: dict[str, str] | None = None,
        authenticated: bool = True,
        retries: int | None = None,
    ) -> tuple[Any, str | None]:
        attempts = self.retries if retries is None else max(1, int(retries))
        url = f"{self.base_url}{path}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        relogin_done = False
        last_err: Exception | None = None

        for attempt in range(1, attempts + 1):
            self._throttle()
            req = urllib.request.Request(url, data=data, method=method)
            req.add_header("Accept", "application/json")
            if data is not None:
                req.add_header("Content-Type", "application/json")
            if authenticated and self._token:
                req.add_header("Authorization", f"DXAPI {self._token}")
            for key, value in (headers or {}).items():
                req.add_header(key, value)

            try:
                with self._opener.open(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                    etag = resp.headers.get("ETag")
                    return _decode(raw), etag
            except urllib.error.HTTPError as err:
                raw = err.read()
                body = _decode(raw)
                code, message = _error_fields(body, err)
                if err.code == 401 and authenticated and not relogin_done:
                    log.info("DXtrade token scaduto: nuovo login e retry")
                    relogin_done = True
                    self._token = None
                    self.login()
                    continue
                if err.code == 401:
                    raise DXTradeAuthError(err.code, code, message, body) from err
                if err.code == 429 and attempt < attempts:
                    delay = self._backoff(attempt)
                    log.warning("DXtrade 429: retry tra %.1fs", delay)
                    self._sleep(delay)
                    continue
                if 500 <= err.code < 600 and attempt < attempts:
                    delay = self._backoff(attempt)
                    log.warning("DXtrade %s: retry tra %.1fs", err.code, delay)
                    self._sleep(delay)
                    continue
                raise DXTradeError(err.code, code, message, body) from err
            except (urllib.error.URLError, TimeoutError, OSError) as err:
                last_err = err
                if attempt < attempts:
                    delay = self._backoff(attempt)
                    log.warning("DXtrade rete (%s): retry tra %.1fs", err, delay)
                    self._sleep(delay)
                    continue
                raise DXTradeError(0, None, f"errore di rete: {err}") from err

        raise DXTradeError(0, None, f"richiesta non completata: {last_err}")

    def _throttle(self) -> None:
        if self.min_interval_sec <= 0:
            return
        elapsed = self._monotonic() - self._last_call_at
        if elapsed < self.min_interval_sec:
            self._sleep(self.min_interval_sec - elapsed)
        self._last_call_at = self._monotonic()

    @staticmethod
    def _backoff(attempt: int) -> float:
        return min(8.0, 0.5 * (2 ** (attempt - 1)))


# ── helper di parsing ─────────────────────────────────────────────────────


def _seg(value: str) -> str:
    """Path segment URL-encoded (gli account code contengono ``:``)."""
    return urllib.parse.quote(str(value), safe="")


def _decode(raw: bytes) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {"raw": raw.decode("utf-8", "replace")}


def _error_fields(body: Any, err: urllib.error.HTTPError) -> tuple[int | None, str]:
    if isinstance(body, dict):
        code = body.get("errorCode", body.get("error_code"))
        message = body.get("description") or body.get("message") or err.reason
        try:
            code = int(code) if code is not None else None
        except (TypeError, ValueError):
            code = None
        return code, str(message)
    return None, str(err.reason)


def _as_list(body: Any, key: str) -> list[dict]:
    if body is None:
        return []
    if isinstance(body, list):
        return body
    if isinstance(body, dict):
        value = body.get(key)
        if isinstance(value, list):
            return value
        for candidate in ("content", "items", "data"):
            value = body.get(candidate)
            if isinstance(value, list):
                return value
    return []


def _instrument_list(body: Any) -> list[dict]:
    return _as_list(body, "instruments")


def _first_instrument(body: Any) -> dict | None:
    items = _instrument_list(body)
    if items:
        return items[0]
    if isinstance(body, dict) and body.get("symbol"):
        return body
    return None
