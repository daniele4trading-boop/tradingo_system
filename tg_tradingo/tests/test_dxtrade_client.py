"""Test del client REST DXtrade contro un server finto locale."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from dxtrade_client import (
    ERR_DUPLICATE_ORDER_ID,
    DXTradeAuthError,
    DXTradeClient,
    DXTradeError,
)

TOKEN = "session-token-1"


class FakeDXtrade:
    """Piattaforma finta: registra le richieste e restituisce risposte scriptate."""

    def __init__(self):
        self.requests: list[dict] = []
        self.tokens = [TOKEN]
        self.expire_next_auth = False
        self.fail_queue: list[tuple[int, dict]] = []
        self.seen_order_codes: set[str] = set()
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None

    # ── ciclo di vita ─────────────────────────────────────────────────
    def start(self) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args):  # silenzia il logging di stdlib
                pass

            def _read_body(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                if not raw:
                    return None
                try:
                    return json.loads(raw.decode("utf-8"))
                except ValueError:
                    return {"raw": raw.decode("utf-8", "replace")}

            def _send(self, status: int, payload, etag: str | None = None):
                body = json.dumps(payload).encode("utf-8") if payload is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                if etag:
                    self.send_header("ETag", etag)
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def _handle(self, method: str):
                body = self._read_body()
                fake.requests.append(
                    {
                        "method": method,
                        "path": self.path,
                        "body": body,
                        "auth": self.headers.get("Authorization"),
                        "if_match": self.headers.get("If-Match"),
                    }
                )
                status, payload, etag = fake.route(method, self.path, body, self.headers)
                self._send(status, payload, etag)

            def do_GET(self):
                self._handle("GET")

            def do_POST(self):
                self._handle("POST")

            def do_PUT(self):
                self._handle("PUT")

            def do_DELETE(self):
                self._handle("DELETE")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.server.server_address
        return f"http://{host}:{port}/dxsca-web"

    def stop(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        if self.thread:
            self.thread.join(timeout=5)

    # ── routing ───────────────────────────────────────────────────────
    def route(self, method: str, path: str, body, headers):
        if self.fail_queue:
            status, payload = self.fail_queue.pop(0)
            return status, payload, None

        path = path.split("/dxsca-web", 1)[-1]

        if path == "/login" and method == "POST":
            if body.get("password") != "pw":
                return 401, {"errorCode": 3, "description": "Incorrect username or password"}, None
            return 200, {"sessionToken": self.tokens[-1], "timeout": "600000"}, None

        auth = headers.get("Authorization") or ""
        if not auth.startswith("DXAPI "):
            return 401, {"errorCode": 3, "description": "missing token"}, None
        if self.expire_next_auth:
            self.expire_next_auth = False
            self.tokens.append(f"session-token-{len(self.tokens) + 1}")
            return 401, {"errorCode": 3, "description": "token expired"}, None
        if auth.split(" ", 1)[1] != self.tokens[-1]:
            return 401, {"errorCode": 3, "description": "stale token"}, None

        if path == "/ping":
            return 200, {}, None
        if path == "/logout":
            return 200, {}, None
        if path == "/users":
            return 200, {"users": [{"accounts": [{"account": "default:demo1"}]}]}, None
        if path.startswith("/instruments/"):
            symbol = path.rsplit("/", 1)[-1].replace("%2F", "/")
            if symbol not in ("XAU/USD", "EUR/USD"):
                return 404, {"errorCode": 2, "description": "Entity not found at server"}, None
            return 200, _instrument(symbol), None
        if path.endswith("/positions"):
            return 200, {"positions": _positions()}, "etag-pos-1"
        if path.endswith("/metrics"):
            return 200, {"balance": 10000.0, "equity": 10012.5, "currency": "USD"}, None
        if path.endswith("/portfolio"):
            return 200, {"balances": [{"currency": "USD", "value": 10000.0}], "positions": _positions()}, None
        if path.endswith("/orders") and method == "POST":
            return self._place(body)
        if path.endswith("/orders") and method == "PUT":
            if not headers.get("If-Match"):
                return 409, {"errorCode": 33, "description": "conditional request required"}, None
            return 200, {"orderId": 777, "updateOrderId": 778}, None
        if path.endswith("/close") and method == "POST":
            return 200, {"closed": True}, None
        if path == "/marketdata" and method == "POST":
            return 200, {"quotes": [{"symbol": "XAU/USD", "bid": 4000.0, "ask": 4000.4}]}, None
        return 404, {"errorCode": 2, "description": "Entity not found at server"}, None

    def _place(self, body):
        codes = [o["orderCode"] for o in body.get("orders", [])] if "orders" in body else [body.get("orderCode")]
        for code in codes:
            if code in self.seen_order_codes:
                return (
                    409,
                    {"errorCode": ERR_DUPLICATE_ORDER_ID, "description": f"Order with this id already exists ({code})"},
                    None,
                )
        self.seen_order_codes.update(c for c in codes if c)
        return 200, {"orderId": 63649, "updateOrderId": 63649}, None


def _instrument(symbol: str) -> dict:
    if symbol == "XAU/USD":
        return {
            "symbol": "XAU/USD",
            "description": "Gold vs US Dollar",
            "type": "METAL",
            "lotSize": 100,
            "priceIncrement": 0.01,
            "quantityIncrement": 1,
            "currency": "USD",
        }
    return {
        "symbol": "EUR/USD",
        "description": "Euro vs US Dollar",
        "type": "FOREX",
        "lotSize": 100000,
        "priceIncrement": 0.00001,
        "quantityIncrement": 1000,
        "currency": "USD",
    }


def _positions() -> list[dict]:
    return [
        {
            "account": "default:demo1",
            "positionCode": "63649",
            "symbol": "XAU/USD",
            "quantity": 10,
            "side": "SELL",
            "openPrice": 4053.48,
            "openTime": "2026-08-03T09:26:47Z",
        },
        {
            "account": "default:demo1",
            "positionCode": "63650",
            "symbol": "XAU/USD",
            "quantity": 10,
            "side": "SELL",
            "openPrice": 4053.48,
            "openTime": "2026-08-03T09:26:47Z",
        },
    ]


@pytest.fixture
def fake():
    server = FakeDXtrade()
    base_url = server.start()
    server.base_url = base_url
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def client(fake):
    return DXTradeClient(
        base_url=fake.base_url,
        username="user",
        password="pw",
        domain="default",
        account="default:demo1",
        min_interval_sec=0.0,
        sleep=lambda _s: None,
    )


def test_login_sets_token_and_auth_header(client, fake):
    token = client.login()
    assert token == TOKEN
    client.positions()
    assert fake.requests[-1]["auth"] == f"DXAPI {TOKEN}"


def test_login_with_wrong_password_raises_auth_error(fake):
    bad = DXTradeClient(
        base_url=fake.base_url,
        username="user",
        password="nope",
        min_interval_sec=0.0,
        sleep=lambda _s: None,
    )
    with pytest.raises(DXTradeError) as err:
        bad.login()
    assert err.value.status == 401
    assert err.value.code == 3


def test_expired_token_triggers_relogin_and_retry(client, fake):
    client.login()
    fake.expire_next_auth = True
    positions = client.positions()
    assert len(positions) == 2
    assert client.token == fake.tokens[-1] != TOKEN


def test_account_code_is_url_encoded_in_path(client, fake):
    client.positions()
    assert "/accounts/default%3Ademo1/positions" in fake.requests[-1]["path"]


def test_positions_exposes_etag(client):
    client.positions()
    assert client.last_etag == "etag-pos-1"


def test_instrument_returns_metadata(client):
    data = client.instrument("XAU/USD")
    assert data["lotSize"] == 100
    assert data["priceIncrement"] == 0.01


def test_unknown_instrument_raises_entity_not_found(client):
    with pytest.raises(DXTradeError) as err:
        client.instrument("FOO/BAR")
    assert err.value.is_api_not_permitted  # 404 / errorCode 2


def test_place_order_returns_order_id(client):
    result = client.place_order(
        {"orderCode": "TG-GOLD-abc-T1", "type": "MARKET", "instrument": "XAU/USD",
         "quantity": 10, "positionEffect": "OPEN", "side": "SELL", "tif": "GTC"}
    )
    assert result["orderId"] == 63649


def test_duplicate_order_code_is_reported_not_raised(client):
    payload = {"orderCode": "TG-GOLD-dup-T1", "type": "MARKET", "instrument": "XAU/USD",
               "quantity": 10, "positionEffect": "OPEN", "side": "SELL", "tif": "GTC"}
    assert client.place_order(payload)["orderId"] == 63649
    assert client.place_order(payload) == {
        "duplicate": True,
        "error": "Order with this id already exists (TG-GOLD-dup-T1)",
    }


def test_modify_order_requires_etag(client):
    with pytest.raises(DXTradeError) as err:
        client.modify_order({"orderCode": "x"})
    assert err.value.status == 409
    assert client.modify_order({"orderCode": "x"}, etag="etag-pos-1")["orderId"] == 777


def test_bulk_close_payload(client, fake):
    client.bulk_close(instrument="XAU/USD", close_positions=True, cancel_orders=True)
    body = fake.requests[-1]["body"]
    assert body == {
        "closePositions": True,
        "cancelOrders": True,
        "instrument": "XAU/USD",
    }


def test_dry_run_does_not_send_mutating_requests(fake):
    client = DXTradeClient(
        base_url=fake.base_url,
        username="user",
        password="pw",
        account="default:demo1",
        dry_run=True,
        min_interval_sec=0.0,
        sleep=lambda _s: None,
    )
    result = client.place_order({"orderCode": "TG-X-1"})
    assert result["dryRun"] is True
    assert all(r["method"] != "POST" or r["path"].endswith("/login") for r in fake.requests)


def test_dry_run_still_reads_positions(fake):
    client = DXTradeClient(
        base_url=fake.base_url,
        username="user",
        password="pw",
        account="default:demo1",
        dry_run=True,
        min_interval_sec=0.0,
        sleep=lambda _s: None,
    )
    assert len(client.positions()) == 2


def test_rate_limit_429_is_retried(client, fake):
    client.login()
    fake.fail_queue = [(429, {"errorCode": 429, "description": "Too Many Requests"})]
    assert len(client.positions()) == 2


def test_server_error_is_retried_then_raised(client, fake):
    client.login()
    fake.fail_queue = [(500, {"errorCode": 110, "description": "system error"})] * 5
    with pytest.raises(DXTradeError) as err:
        client.positions()
    assert err.value.status == 500


def test_missing_token_on_protected_call_raises_after_relogin(fake):
    client = DXTradeClient(
        base_url=fake.base_url,
        username="user",
        password="pw",
        account="default:demo1",
        min_interval_sec=0.0,
        sleep=lambda _s: None,
    )
    client.login()
    fake.tokens.append("rotated-token")  # invalida il token del client
    fake.expire_next_auth = False
    positions = client.positions()  # 401 -> relogin trasparente
    assert len(positions) == 2


def test_context_manager_logs_out(fake):
    client = DXTradeClient(
        base_url=fake.base_url,
        username="user",
        password="pw",
        account="default:demo1",
        min_interval_sec=0.0,
        sleep=lambda _s: None,
    )
    with client:
        client.ping()
    assert any(r["path"].endswith("/logout") for r in fake.requests)
    assert client.token is None


def test_auth_error_type_for_bad_credentials(fake):
    client = DXTradeClient(
        base_url=fake.base_url,
        username="user",
        password="nope",
        min_interval_sec=0.0,
        sleep=lambda _s: None,
    )
    with pytest.raises(DXTradeAuthError):
        client.login()
