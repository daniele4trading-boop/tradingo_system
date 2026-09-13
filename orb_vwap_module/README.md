# ORB + VWAP false-breakout (Oro) — modulo intraday standalone

Backtest Python di un setup **break-in / rientro / trigger VWAP** sull'Opening Range
della sessione New York. Isolato dal resto del repo (nessun import da `tg_tradingo`,
StatArb, Pattern GO o gold-agent). Magic number riservato per un futuro EA: **20260910**.

## Dati

CSV `export_<SIMBOLO>_<TF>_<periodo>.csv` (schema `ts, open, high, low, close, n_ticks,
volume, spread_med, ...`, `ts` UTC naive = apertura barra). Il loader replica quello
canonico del gold-agent (indice `ts`, ordinato, deduplicato). Il campo `vwap` del CSV
(intra-barra) **non** viene usato: il VWAP di sessione è ricalcolato su M5.

Servono `M5` (trigger) e `M15` (ORB) del simbolo nella cartella dati.

## Esecuzione

```bash
python -m orb_vwap_module.run_backtest --data-dir <dir csv> --out <dir output> --grid
```

Opzioni: `--months 12`, `--end`, `--capital`, `--cutoff 22:00`, `--volume-filter`,
`--vol-n/--vol-k`, `--atr-period/--atr-mult`, `--sl-buffer`, `--min-risk-dist`.

Output: `REPORT.md`, `summary.json`, `trades_A/B.csv`, `setups_A/B.csv`,
`equity_A/B.csv`, `grid_B.csv` (24 celle: ATR {7,14,21} × k {1.5,2,2.5,3} × volume on/off).

## Struttura

| file | ruolo |
|---|---|
| `session.py` | fuso/DST via `zoneinfo`, apertura/cutoff, timeframe ORB e trigger, finestra trigger parametrici |
| `orb.py` | High/Low della finestra ORB |
| `vwap.py` | VWAP cumulato per sessione, ATR, media volume (solo barre precedenti) |
| `setup_engine.py` | macchina a stati IDLE→ARMED→CONFIRMED→TRIGGERED / INVALID / EXPIRED / DISARMED |
| `position_manager.py` | sizing, variante A (1 ticket) e B (2 gambe, BE + trailing ATR) |
| `backtester.py` | loop sessioni, equity, log trade e setup |
| `report.py` | metriche, A/B, griglia, REPORT.md |

Test: `python -m pytest orb_vwap_module/tests`.
