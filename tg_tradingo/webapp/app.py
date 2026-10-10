"""TG TradinGo monitoring webapp (FastAPI).

Fase 1: dashboard read-only con semafori. Fase 3 (ordini) predisposta ma
disabilitata via config (`orders_enabled: false`).

Avvio (da C:\\TG_TradinGo):
    python -m webapp.app
Config: webapp_config.json accanto a tradingo_config.json
(override con env TRADINGO_WEBAPP_CONFIG).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    RedirectResponse,
)
from fastapi.staticfiles import StaticFiles

_PKG_DIR = Path(__file__).resolve().parent
_BASE_DIR = _PKG_DIR.parent
if str(_BASE_DIR) not in sys.path:
    sys.path.insert(0, str(_BASE_DIR))

from webapp.admin_mgr import AdminManager  # noqa: E402
from webapp.auth import RateLimiter, SessionManager, verify_password  # noqa: E402
from webapp.collector import Collector  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("TradinGoWeb")

CONFIG_FILE = os.environ.get(
    "TRADINGO_WEBAPP_CONFIG",
    str(_BASE_DIR / "webapp_config.json"),
)

COOKIE_NAME = "tg_session"


def load_config() -> dict:
    path = CONFIG_FILE
    if not os.path.exists(path):
        example = _BASE_DIR / "webapp_config.example.json"
        if example.exists():
            path = str(example)
    # utf-8-sig: Windows PowerShell Set-Content -Encoding UTF8 often writes a BOM
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


CONFIG = load_config()


def _session_hours(cfg: dict) -> float:
    """Prefer session_days (default 180 ≈ 6 months); fall back to session_hours."""
    if cfg.get("session_days") is not None:
        return float(cfg["session_days"]) * 24.0
    return float(cfg.get("session_hours", 180 * 24))


sessions = SessionManager(
    CONFIG.get("secret_key", ""),
    hours=_session_hours(CONFIG),
)
limiter = RateLimiter(
    max_attempts=int(CONFIG.get("login_max_attempts", 5)),
    window_sec=int(CONFIG.get("login_lockout_minutes", 15)) * 60,
)
collector = Collector(CONFIG)
admin_mgr = AdminManager(Path(_BASE_DIR) / "webapp_admin_data.json")


def _is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    proto = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
    if proto == "https":
        return True
    # Tailscale Serve terminates TLS; backend sees HTTP. Trust ts.net Host.
    host = (request.headers.get("host") or request.url.hostname or "").lower()
    return host.endswith(".ts.net")


def _set_session_cookie(resp, request: Request, token: str) -> None:
    """Persist session on iOS Safari (esp. via Tailscale HTTPS Serve)."""
    secure = _is_https(request)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=sessions.ttl_sec)
    resp.set_cookie(
        COOKIE_NAME,
        token,
        max_age=sessions.ttl_sec,
        expires=expires_at,
        httponly=True,
        samesite="lax",
        path="/",
        secure=secure,
    )


@asynccontextmanager
async def lifespan(_app: FastAPI):
    collector.start()
    days = sessions.ttl_sec / 86400.0
    log.info(
        "TG TradinGo webapp avviata (Fase 2) — sessione cookie ~%.0f giorni (%ss)",
        days,
        sessions.ttl_sec,
    )
    yield
    collector.stop()


app = FastAPI(
    title="TG TradinGo Monitor",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    lifespan=lifespan,
)
app.mount("/static", StaticFiles(directory=str(_PKG_DIR / "static")), name="static")


def current_user(request: Request) -> str | None:
    return sessions.verify(request.cookies.get(COOKIE_NAME))


def require_admin(request: Request) -> str | None:
    user = current_user(request)
    admin_users = CONFIG.get("admin_users", ["daniele"])
    if user and user.lower() in [u.lower() for u in admin_users]:
        return user
    return None


@app.get("/")
def index(request: Request):
    if not current_user(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(_PKG_DIR / "static" / "index.html")


@app.get("/login")
def login_page(request: Request):
    if current_user(request):
        return RedirectResponse("/", status_code=302)
    return FileResponse(_PKG_DIR / "static" / "login.html")


@app.post("/login")
def login(request: Request, username: str = Form(""), password: str = Form(""), website: str = Form("")):
    ip = request.client.host if request.client else "?"
    keys = [f"ip:{ip}", f"user:{username.lower()}"]

    # Honeypot: bots fill the hidden "website" field -> treat as failure.
    if website:
        for k in keys:
            limiter.record_failure(k)
        return RedirectResponse("/login?err=1", status_code=302)

    for k in keys:
        if limiter.is_locked(k):
            retry = max(limiter.retry_after_sec(k), 1)
            log.warning("login lockout %s (retry in %ss)", k, retry)
            return RedirectResponse(f"/login?locked={retry}", status_code=302)

    user_cfg = next(
        (u for u in CONFIG.get("users", []) if u.get("username", "").lower() == username.lower()),
        None,
    )
    ok = bool(user_cfg) and verify_password(password, user_cfg.get("password_hash", ""))
    if not ok:
        for k in keys:
            limiter.record_failure(k)
        log.warning("login fallito user=%r ip=%s", username, ip)
        return RedirectResponse("/login?err=1", status_code=302)

    for k in keys:
        limiter.reset(k)
    token = sessions.create(user_cfg["username"])
    # Safari/iOS often drops Set-Cookie on 302 after POST. Serve a 200 page that
    # sets the cookie, then redirects with JS/meta — session then persists.
    html = (
        "<!DOCTYPE html><html><head>"
        '<meta charset="utf-8">'
        '<meta http-equiv="refresh" content="0;url=/">'
        "<title>Accesso…</title></head><body>"
        "<p>Accesso riuscito, reindirizzamento…</p>"
        '<script>location.replace("/");</script>'
        "</body></html>"
    )
    resp = HTMLResponse(content=html, status_code=200)
    _set_session_cookie(resp, request, token)
    log.info(
        "login ok user=%s ip=%s session_days≈%.0f secure=%s",
        user_cfg["username"],
        ip,
        sessions.ttl_sec / 86400.0,
        _is_https(request),
    )
    return resp


@app.get("/logout")
def logout(request: Request):
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(
        COOKIE_NAME,
        path="/",
        secure=_is_https(request),
    )
    return resp


@app.get("/api/status")
def api_status(request: Request):
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "non autenticato"}, status_code=401)
    snap = collector.snapshot()
    snap["user"] = user
    return JSONResponse(snap)


@app.post("/api/order")
def api_order(request: Request):
    """Fase 3 (inserimento ordini): predisposta, disabilitata da config."""
    user = current_user(request)
    if not user:
        return JSONResponse({"error": "non autenticato"}, status_code=401)
    if not CONFIG.get("orders_enabled", False):
        return JSONResponse(
            {"error": "ordini disabilitati (fase 3 non attiva)"}, status_code=403
        )
    return JSONResponse({"error": "non implementato"}, status_code=501)


@app.get("/admin")
def admin_page(request: Request):
    if not require_admin(request):
        return RedirectResponse("/login", status_code=302)
    return FileResponse(_PKG_DIR / "static" / "admin.html")


@app.get("/api/admin/status")
def api_admin_status(request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "non autorizzato"}, status_code=403)
    snap = collector.snapshot()
    return JSONResponse({
        "mt5_terminals": snap.get("mt5_terminals", []),
        "alerts": snap.get("alerts", []),
        "accounts_cfg": admin_mgr.get_accounts(),
        "costs": admin_mgr.get_costs(),
        "withdrawals": admin_mgr.get_withdrawals(),
    })


@app.post("/api/admin/account")
async def api_admin_account(request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "non autorizzato"}, status_code=403)
    body = await request.json()
    admin_mgr.set_account(body.get("terminal_id"), body)
    return JSONResponse({"ok": True})


@app.post("/api/admin/cost")
async def api_admin_cost(request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "non autorizzato"}, status_code=403)
    body = await request.json()
    admin_mgr.add_cost(body)
    return JSONResponse({"ok": True})


@app.delete("/api/admin/cost/{cost_id}")
def api_admin_cost_delete(request: Request, cost_id: str):
    if not require_admin(request):
        return JSONResponse({"error": "non autorizzato"}, status_code=403)
    admin_mgr.delete_cost(cost_id)
    return JSONResponse({"ok": True})


@app.post("/api/admin/withdrawal")
async def api_admin_withdrawal(request: Request):
    if not require_admin(request):
        return JSONResponse({"error": "non autorizzato"}, status_code=403)
    body = await request.json()
    admin_mgr.add_withdrawal(body)
    return JSONResponse({"ok": True})


@app.delete("/api/admin/withdrawal/{w_id}")
def api_admin_withdrawal_delete(request: Request, w_id: str):
    if not require_admin(request):
        return JSONResponse({"error": "non autorizzato"}, status_code=403)
    admin_mgr.delete_withdrawal(w_id)
    return JSONResponse({"ok": True})


def main() -> None:
    import uvicorn

    uvicorn.run(
        app,
        host=CONFIG.get("host", "127.0.0.1"),
        port=int(CONFIG.get("port", 8600)),
        log_level="info",
    )


if __name__ == "__main__":
    main()
