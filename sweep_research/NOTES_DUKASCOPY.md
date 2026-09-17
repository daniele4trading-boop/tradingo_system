Note operative (mantenute a mano dal lead; incluse in FINDINGS.md a ogni run).

### Inventario QLAB riusato (Contabo `C:\quantlab`, `C:\quantlab-data`)

- Warehouse `mktdata`: Parquet+zstd, catalogo DuckDB, timestamp UTC naive, layout
  `bars/symbol=<S>/source=dukascopy/tf=<TF>/anchor=0/year=<Y>/<Y>-<M>.parquet` e
  `ticks/symbol=<S>/source=dukascopy/year=<Y>/<Y>-<M>-<D>.parquet`.
- XAUUSD verificato: tick 2015-01-01 23:00 → 2026-07-24 20:00; M1/M5/M15/H1/H4/D1 stesso range.
- Copiato in locale (sola lettura) per 2022-07 → 2026-07: tick giornalieri (1239 giorni, ~2,2 GB) e barre M1/M5/M15 (55 file mensili per TF).
- Componenti QLAB vendorizzati in `src/primitives.py`, `src/sessions.py`, `src/profile.py` (attribuzione nei docstring):
  `true_range/atr`, `swing_flags/swing_points` (conferma causale a N barre), killzone/sessioni NY, POC/VAH/VAL.
  Il detector `qlab/events/liquidity.py` NON è stato riusato (definizione più restrittiva di quella S0);
  le colonne `vwap` intra-barra QLAB non sono usate (VWAP di sessione ricalcolato in `features_context.session_vwap`).
- Non trovati in `C:\quantlab`: triple-barrier, purged CV, FDR, Deflated Sharpe, Optuna (da implementare in S3/S4).

### Nuovi simboli Dukascopy (append-only nel warehouse Contabo)

Sync esterno completato: `XAGUSD`, `DOLLARIDXUSD`, `USTBONDTRUSD`, timeframe
`M1/M5/M15/H1`, 48 file mensili per simbolo nel periodo 2022-08 → 2026-07.
Path Windows: `C:\quantlab-data\bars\symbol=<SYM>\source=dukascopy\tf=<TF>\anchor=0`.
Le barre esterne sono state copiate in locale. Sono inoltre disponibili 1 238 giorni
di tick XAGUSD in `C:\quantlab-data\ticks\symbol=XAGUSD`.

Codici verificati con `mktdata.dukascopy.verify_divisor` (divisore 1000, prezzi plausibili):
`XAGUSD` (bid mediano 30.411 il 2024-06-03), `DOLLARIDXUSD` (104.471), `USTBONDTRUSD` (116.835, CFD T-Bond: **proxy** tassi).
`USDIDXUSD` non esiste su Dukascopy.

Script: `C:\quantlab\scripts\sync_sweep_external.py` (registra i simboli in
`mktdata.symbols` e chiama `mktdata.sync` per il periodo richiesto). Le barre M1 dei
tre simboli sono ora locali in `data_root`; il rerun S0 le usa per popolare
`dxy_intraday_trend`, `ust_proxy_change` e `xagusd_divergence`.

### Calendario news

Nessuna fonte gratuita riproducibile integrata: `news_high_impact_within_30min` è NaN. Proposta: archivio settimanale
ForexFactory (`https://nfs.faireconomy.media/ff_calendar_thisweek.json`, solo settimana corrente → accumulare) oppure
export CSV storico Investing.com/Myfxbook; salvare in `C:\quantlab-data\external\news\`.

### Cosa NON ha funzionato

- Primo run 4 anni terminato per OOM (7 GB RAM): cache dei tick per giorno e copie di M1 in oggetti Python;
  risolto con caricamento lazy e tipi datetime64 (picco 1,6 GB, 17,5 min).
- Dukascopy non fornisce trade print: `delta_est`/`cvd_session`/`vpin` usano volumi di quotazione bid/ask con
  classificazione sul mid del tick precedente (Lee-Ready quote-based, marcato PROXY nello schema).
