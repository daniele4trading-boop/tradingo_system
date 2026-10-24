"""Diagnostica pre-integrazione DXtrade: risponde alle domande aperte.

Non invia ordini. Verifica in sola lettura ciò che decide il design:

* l'utente ha il permesso REST API? (404/errorCode 2 = non abilitata)
* quali ``account code`` sono visibili
* come si chiamano i simboli sul broker (``XAU/USD`` vs ``XAUUSD``)
* ``lotSize`` / ``priceIncrement`` / ``quantityIncrement`` per convertire i lotti
* l'account è hedging o netting (più posizioni sullo stesso simbolo/lato?)
* quote disponibili via REST per valutare ``entry_range``

Uso:

    export DXTRADE_BASE_URL="https://dx.broker.com/dxsca-web"
    export DXTRADE_USERNAME="..." DXTRADE_PASSWORD="..."
    export DXTRADE_DOMAIN="default"          # opzionale
    export DXTRADE_ACCOUNT="default:12345"   # opzionale, altrimenti scoperto
    python dxtrade_probe.py --symbols XAUUSD,EURUSD

Le credenziali si passano solo da environment: niente segreti nel repo.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys

from dxtrade_client import DXTradeClient, DXTradeError
from dxtrade_mapper import InstrumentMeta, MappingError, lots_to_units, map_symbol

DEFAULT_SYMBOLS = ("XAUUSD", "EURUSD")
PROBE_LOTS = (0.01, 0.10, 0.20)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Diagnostica API DXtrade")
    parser.add_argument("--base-url", default=os.environ.get("DXTRADE_BASE_URL", ""))
    parser.add_argument("--username", default=os.environ.get("DXTRADE_USERNAME", ""))
    parser.add_argument("--domain", default=os.environ.get("DXTRADE_DOMAIN", "default"))
    parser.add_argument("--account", default=os.environ.get("DXTRADE_ACCOUNT", ""))
    parser.add_argument(
        "--symbols",
        default=",".join(DEFAULT_SYMBOLS),
        help="simboli MT da risolvere sul broker (virgola)",
    )
    parser.add_argument("--json", action="store_true", help="output JSON")
    parser.add_argument("--verbose", action="store_true")
    return parser


def probe(client: DXTradeClient, symbols: list[str]) -> dict:
    report: dict = {"base_url": client.base_url, "checks": {}, "warnings": []}
    checks = report["checks"]

    client.login()
    checks["login"] = "ok"

    try:
        users = client.users()
        report["users"] = users
        accounts = _account_codes(users)
        report["accounts"] = accounts
        checks["users"] = "ok"
    except DXTradeError as err:
        accounts = []
        checks["users"] = _fail(err)
        if err.is_api_not_permitted:
            report["warnings"].append(
                "GET /users ha risposto 404/2: l'utente potrebbe non avere il "
                "permesso REST API. Chiedi al broker di abilitarlo."
            )

    if not client.account and accounts:
        client.account = accounts[0]
        report["warnings"].append(
            f"account non configurato: uso il primo trovato ({client.account})"
        )
    report["account"] = client.account

    if client.account:
        for name, call in (
            ("metrics", client.metrics),
            ("portfolio", client.portfolio),
        ):
            try:
                report[name] = call()
                checks[name] = "ok"
            except DXTradeError as err:
                checks[name] = _fail(err)

        try:
            positions = client.positions()
            report["positions"] = positions
            checks["positions"] = f"ok ({len(positions)})"
            report["hedging_hint"] = _hedging_hint(positions)
        except DXTradeError as err:
            checks["positions"] = _fail(err)

    report["instruments"] = {}
    for mt_symbol in symbols:
        dx_symbol = map_symbol(mt_symbol)
        entry: dict = {"mt_symbol": mt_symbol, "tried": []}
        data = None
        for candidate in _symbol_candidates(mt_symbol, dx_symbol):
            entry["tried"].append(candidate)
            try:
                data = client.instrument(candidate)
            except DXTradeError:
                data = None
            if data and data.get("symbol"):
                entry["resolved"] = candidate
                break
        if not data or not data.get("symbol"):
            entry["error"] = "nessun simbolo risolto"
            report["warnings"].append(
                f"{mt_symbol}: nessun candidato risolto, serve una mappatura "
                f"esplicita in config (tried: {', '.join(entry['tried'])})"
            )
            report["instruments"][mt_symbol] = entry
            continue

        try:
            meta = InstrumentMeta.from_api(data)
        except MappingError as err:
            entry["error"] = str(err)
            report["instruments"][mt_symbol] = entry
            continue

        entry.update(
            {
                "symbol": meta.symbol,
                "description": meta.description,
                "lot_size": meta.lot_size,
                "price_increment": meta.price_increment,
                "quantity_increment": meta.quantity_increment,
                "units_per_lot": {},
            }
        )
        for lots in PROBE_LOTS:
            try:
                entry["units_per_lot"][str(lots)] = lots_to_units(lots, meta)
            except MappingError as err:
                entry["units_per_lot"][str(lots)] = f"errore: {err}"
        report["instruments"][mt_symbol] = entry

        if client.account:
            try:
                quotes = client.market_data([meta.symbol])
                entry["quote"] = quotes
                checks[f"marketdata:{mt_symbol}"] = "ok"
            except DXTradeError as err:
                checks[f"marketdata:{mt_symbol}"] = _fail(err)
                report["warnings"].append(
                    f"quote REST non disponibili per {meta.symbol}: senza prezzo "
                    "la valutazione di entry_range va fatta via WebSocket (Push API)"
                )

    return report


def _symbol_candidates(mt_symbol: str, dx_symbol: str) -> list[str]:
    seen: list[str] = []
    for candidate in (dx_symbol, mt_symbol, mt_symbol.upper(), f"{mt_symbol}.spot"):
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen


def _account_codes(users: dict) -> list[str]:
    codes: list[str] = []
    if not isinstance(users, dict):
        return codes
    containers = [users]
    for key in ("users", "accounts", "content"):
        value = users.get(key)
        if isinstance(value, list):
            containers.extend(v for v in value if isinstance(v, dict))
    for item in containers:
        for key in ("accounts", "accountCodes"):
            value = item.get(key)
            if isinstance(value, list):
                for acct in value:
                    code = acct.get("account") if isinstance(acct, dict) else acct
                    if code and code not in codes:
                        codes.append(str(code))
        code = item.get("account")
        if isinstance(code, str) and code not in codes:
            codes.append(code)
    return codes


def _hedging_hint(positions: list[dict]) -> str:
    seen: dict[tuple[str, str], int] = {}
    for pos in positions:
        key = (str(pos.get("symbol", "")), str(pos.get("side", "")))
        seen[key] = seen.get(key, 0) + 1
    stacked = {k: v for k, v in seen.items() if v > 1}
    if stacked:
        return f"hedging probabile: più posizioni su stesso simbolo/lato {stacked}"
    return (
        "indeterminato: nessuna coppia simbolo/lato con più posizioni aperte. "
        "Serve un test manuale (2 ordini stesso lato) per distinguere hedging da netting."
    )


def _fail(err: DXTradeError) -> str:
    return f"errore HTTP {err.status} code={err.code}: {err.message}"


def render(report: dict) -> str:
    lines = [
        "=" * 78,
        "DXtrade probe",
        "=" * 78,
        f"base_url : {report.get('base_url')}",
        f"account  : {report.get('account') or '(non risolto)'}",
        "",
        "Controlli:",
    ]
    for name, status in (report.get("checks") or {}).items():
        lines.append(f"  {name:<22} {status}")

    accounts = report.get("accounts")
    if accounts:
        lines += ["", f"Account visibili: {', '.join(accounts)}"]

    metrics = report.get("metrics")
    if isinstance(metrics, dict) and metrics:
        lines += ["", "Metriche account:"]
        for key in ("balance", "equity", "availableFunds", "marginUtilization", "currency"):
            if key in metrics:
                lines.append(f"  {key:<22} {metrics[key]}")

    lines += ["", "Strumenti:"]
    for mt_symbol, entry in (report.get("instruments") or {}).items():
        if entry.get("error"):
            lines.append(f"  {mt_symbol:<8} ERRORE: {entry['error']} (tried: {', '.join(entry.get('tried', []))})")
            continue
        lines.append(
            f"  {mt_symbol:<8} -> {entry['symbol']:<12} lotSize={entry['lot_size']} "
            f"priceIncrement={entry['price_increment']} qtyIncrement={entry['quantity_increment']}"
        )
        units = entry.get("units_per_lot") or {}
        if units:
            detail = "  ".join(f"{lots} lot = {value} unità" for lots, value in units.items())
            lines.append(f"           {detail}")

    if report.get("hedging_hint"):
        lines += ["", f"Modello posizioni: {report['hedging_hint']}"]

    warnings = report.get("warnings") or []
    if warnings:
        lines += ["", "Avvisi:"]
        lines += [f"  - {w}" for w in warnings]

    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    password = os.environ.get("DXTRADE_PASSWORD", "")
    missing = [
        name
        for name, value in (
            ("--base-url / DXTRADE_BASE_URL", args.base_url),
            ("--username / DXTRADE_USERNAME", args.username),
            ("DXTRADE_PASSWORD", password),
        )
        if not value
    ]
    if missing:
        print("Parametri mancanti: " + ", ".join(missing), file=sys.stderr)
        return 2

    client = DXTradeClient(
        base_url=args.base_url,
        username=args.username,
        password=password,
        domain=args.domain,
        account=args.account or None,
    )
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    try:
        report = probe(client, symbols)
    except DXTradeError as err:
        print(f"Probe interrotto: {_fail(err)}", file=sys.stderr)
        return 1
    finally:
        try:
            client.logout()
        except DXTradeError:
            pass

    print(json.dumps(report, indent=2, ensure_ascii=False) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
