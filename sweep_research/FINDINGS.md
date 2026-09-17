# FINDINGS

## Inventario dati

| Simbolo/timeframe | Righe | Min timestamp | Max timestamp | File |
|---|---|---|---|---|
| XAUUSD/M5 | 288032 | 2022-07-03 22:00:00 | 2026-07-24 20:55:00 | 55 |
| XAUUSD/M15 | 96018 | 2022-07-03 22:00:00 | 2026-07-24 20:45:00 | 55 |
| XAUUSD/M1 | 1439638 | 2022-07-03 22:00:00 | 2026-07-24 20:59:00 | 55 |
| XAUUSD/ticks | 1412071 | 2022-08-01 00:00:00 | 2026-07-24 20:59:00 | 1239 |
| DOLLARIDXUSD/M1 | 1262741 | 2022-08-01 00:00:00 | 2026-07-24 20:59:00 | 48 |
| USTBONDTRUSD/M1 | 796743 | 2022-08-01 00:00:00 | 2026-07-24 20:46:00 | 48 |
| XAGUSD/M1 | 1396099 | 2022-08-01 01:00:00 | 2026-07-24 20:59:00 | 48 |

## Conteggi eventi

| TF | Level type | Dir | Totale | Da | A |
|---|---|---|---|---|---|
| M15 | equal_high | sweep_high | 1991 | 2022-08-01 07:30:00 | 2026-07-24 08:45:00 |
| M15 | equal_low | sweep_low | 2038 | 2022-08-03 00:00:00 | 2026-07-24 05:30:00 |
| M15 | swing_high_10 | sweep_high | 1307 | 2022-08-01 07:30:00 | 2026-07-24 07:45:00 |
| M15 | swing_high_20 | sweep_high | 1040 | 2022-08-01 07:30:00 | 2026-07-22 00:45:00 |
| M15 | swing_high_5 | sweep_high | 2576 | 2022-08-01 12:30:00 | 2026-07-24 08:45:00 |
| M15 | swing_low_10 | sweep_low | 1398 | 2022-08-01 00:15:00 | 2026-07-24 01:15:00 |
| M15 | swing_low_20 | sweep_low | 1243 | 2022-08-03 15:00:00 | 2026-07-24 01:15:00 |
| M15 | swing_low_5 | sweep_low | 2729 | 2022-08-01 15:15:00 | 2026-07-24 05:30:00 |
| M5 | equal_high | sweep_high | 8446 | 2022-08-01 01:25:00 | 2026-07-24 14:35:00 |
| M5 | equal_low | sweep_low | 8913 | 2022-08-01 00:05:00 | 2026-07-24 16:35:00 |
| M5 | swing_high_10 | sweep_high | 3811 | 2022-08-01 01:25:00 | 2026-07-24 10:25:00 |
| M5 | swing_high_20 | sweep_high | 3485 | 2022-08-01 07:20:00 | 2026-07-24 08:45:00 |
| M5 | swing_high_5 | sweep_high | 7148 | 2022-08-01 03:00:00 | 2026-07-24 14:35:00 |
| M5 | swing_low_10 | sweep_low | 4091 | 2022-08-01 04:40:00 | 2026-07-24 13:35:00 |
| M5 | swing_low_20 | sweep_low | 3763 | 2022-08-01 00:05:00 | 2026-07-24 05:55:00 |
| M5 | swing_low_5 | sweep_low | 7872 | 2022-08-01 00:05:00 | 2026-07-24 16:35:00 |

## Feature calcolate / proxy / mancanti

| Feature | Fonte | Proxy/missing | Outcome |
|---|---|---|---|
| event_id | events |  | no |
| symbol | config |  | no |
| tf | events |  | no |
| bar_ts_utc | bars |  | no |
| event_ts_utc | bars |  | no |
| event_ts_ny | calendar |  | no |
| ny_date | calendar |  | no |
| dir | events |  | no |
| sign | events |  | no |
| trade_sign | config |  | no |
| level_type | levels |  | no |
| level_price | levels |  | no |
| extreme_price | bars |  | no |
| penetration_pts | bars |  | no |
| penetration_half_spreads | bars |  | no |
| subspread_sweep | bars |  | no |
| bar_open | bars |  | no |
| bar_high | bars |  | no |
| bar_low | bars |  | no |
| bar_close | bars |  | no |
| bar_volume | bars |  | no |
| bar_n_ticks | bars |  | no |
| level_form_ts | bars |  | no |
| level_age_bars | events |  | no |
| level_touch_count | bars |  | no |
| level_points | levels |  | no |
| also_n | levels |  | no |
| wick_ratio | bars |  | no |
| atr_tf | bars |  | no |
| dist_from_level_atr | bars |  | no |
| liquidity_density | bars |  | no |
| time_beyond_level_sec | ticks |  | no |
| displacement_60s | ticks | NaN se nessun midpoint tick supera il livello | no |
| displacement_180s | ticks | NaN se nessun midpoint tick supera il livello | no |
| t_extreme | ticks |  | no |
| displacement_60s_truncated | ticks |  | no |
| displacement_180s_truncated | ticks |  | no |
| ticks_missing | ticks | MISSING: file tick non presente | no |
| delta_est | bars M1 | PROXY: quote-based Lee-Ready | no |
| vpin | bars M1 | APPROX: VPIN a bucket temporali | no |
| cvd_session | bars M1 | PROXY: quote-based Lee-Ready | no |
| delta_divergence_flag | bars TF |  | no |
| delta_divergence_mag | bars TF |  | no |
| spread_at_event | bars TF |  | no |
| vol_vs_hourly_median | bars TF |  | no |
| spread_expansion | bars TF |  | no |
| minutes_from_london_open | calendar |  | no |
| minutes_from_ny_open | calendar |  | no |
| session | calendar |  | no |
| killzone_flag | calendar |  | no |
| day_of_week | calendar |  | no |
| is_month_end | calendar |  | no |
| news_high_impact_within_30min | calendar | MISSING: nessuna fonte news | no |
| vs_session_vwap | bars M1 |  | no |
| vs_poc | bars M1 |  | no |
| vs_vah | bars M1 |  | no |
| vs_val | bars M1 |  | no |
| efficiency_ratio | bars TF |  | no |
| vol_burst_flag | bars TF |  | no |
| volatility_regime | bars TF |  | no |
| overnight_range | bars M1 | NaN se l'ora NY dell'evento è <03:00 o >=18:00, oppure mancano dati | no |
| asia_gap | bars M1 | NaN solo se manca uno dei riferimenti M1 | no |
| dxy_intraday_trend | external symbol | MISSING: external symbol assente | no |
| ust_proxy_change | external symbol | PROXY: T-Bond CFD per tassi | no |
| xagusd_divergence | external symbol | MISSING: external symbol assente | no |
| entry_c1 | outcomes |  | sì |
| entry_c1_ts | outcomes |  | sì |
| exec_slip_bp | outcomes |  | sì |
| fwd_ret_5_bp | outcomes |  | sì |
| fwd_ret_5_dir_bp | outcomes |  | sì |
| fwd_ret_5_c1_bp | outcomes |  | sì |
| fwd_ret_5_c1_dir_bp | outcomes |  | sì |
| fwd_ret_5_c1_ex_bp | outcomes |  | sì |
| fwd_ret_5_c1_ex_dir_bp | outcomes |  | sì |
| fwd_ret_15_bp | outcomes |  | sì |
| fwd_ret_15_dir_bp | outcomes |  | sì |
| fwd_ret_15_c1_bp | outcomes |  | sì |
| fwd_ret_15_c1_dir_bp | outcomes |  | sì |
| fwd_ret_15_c1_ex_bp | outcomes |  | sì |
| fwd_ret_15_c1_ex_dir_bp | outcomes |  | sì |
| fwd_ret_30_bp | outcomes |  | sì |
| fwd_ret_30_dir_bp | outcomes |  | sì |
| fwd_ret_30_c1_bp | outcomes |  | sì |
| fwd_ret_30_c1_dir_bp | outcomes |  | sì |
| fwd_ret_30_c1_ex_bp | outcomes |  | sì |
| fwd_ret_30_c1_ex_dir_bp | outcomes |  | sì |
| fwd_ret_60_bp | outcomes |  | sì |
| fwd_ret_60_dir_bp | outcomes |  | sì |
| fwd_ret_60_c1_bp | outcomes |  | sì |
| fwd_ret_60_c1_dir_bp | outcomes |  | sì |
| fwd_ret_60_c1_ex_bp | outcomes |  | sì |
| fwd_ret_60_c1_ex_dir_bp | outcomes |  | sì |
| fwd_ret_120_bp | outcomes |  | sì |
| fwd_ret_120_dir_bp | outcomes |  | sì |
| fwd_ret_120_c1_bp | outcomes |  | sì |
| fwd_ret_120_c1_dir_bp | outcomes |  | sì |
| fwd_ret_120_c1_ex_bp | outcomes |  | sì |
| fwd_ret_120_c1_ex_dir_bp | outcomes |  | sì |
| cost_rt_bp | outcomes |  | sì |
| mfe_120_pts | outcomes |  | sì |
| mae_120_pts | outcomes |  | sì |
| mfe_120_atr | outcomes |  | sì |
| mae_120_atr | outcomes |  | sì |

## Operazioni Dukascopy

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

## Test statistici eseguiti: S0=0, S1=40, S2=494, totale=534

- ipotesi provate: 2

### Confronto popolazione vecchia/nuova
| h | dir | population | n | mean | t |
|---|---|---|---|---|---|
| 30 | sweep_high | dedup_all_full | 15990 | 0.319 | 1.613 |
| 30 | sweep_high | dedup_all | 8870 | 0.433 | 1.569 |
| 30 | sweep_high | subspread_only | 7120 | 0.177 | 0.884 |
| 30 | sweep_low | dedup_all_full | 16441 | 0.278 | 1.188 |
| 30 | sweep_low | dedup_all | 9713 | 0.423 | 1.264 |
| 30 | sweep_low | subspread_only | 6728 | 0.07 | 0.348 |
| 120 | sweep_high | dedup_all_full | 15990 | 1.138 | 2.111 |
| 120 | sweep_high | dedup_all | 8870 | 1.226 | 1.782 |
| 120 | sweep_high | subspread_only | 7120 | 1.03 | 1.897 |
| 120 | sweep_low | dedup_all_full | 16441 | 0.283 | 0.521 |
| 120 | sweep_low | dedup_all | 9713 | 0.813 | 1.115 |
| 120 | sweep_low | subspread_only | 6728 | -0.482 | -1.017 |

- S2 popolazione filtrata: 18632 eventi.
- S2: m=494; positivi grezzi α=0.05: 51; attesi: 24.7
- survivors BH q=0.10: 0
- survivors BH q=0.05: 0

nessun segmento sopravvive alla correzione FDR

S2: 494 segmenti su 494 non sopravvivono a BH q=0.10.
Nessun segmento con edge positivo al netto dello spread sopravvive alla correzione FDR: risultato negativo.

Report completo: [report_s2.html](output/s2/report_s2.html)

## Cosa NON ha funzionato

Sweep sub-spread: gli eventi sotto la soglia di mezzo spread sono mantenuti ma trattati come rumore bid/mid nella popolazione primaria.
Ipotesi reversal: provata nella lettura S1 precedente; questa revisione testa continuazione come configurazione aggiuntiva.
| Fonte | Stato |
|---|---|
| DOLLARIDXUSD | available |
| USTBONDTRUSD | available |
| XAGUSD | available |
| tick days | 0 eventi senza tick |

## Esito anti-leakage

- cut count: 5
- compared events: 154633
- verified columns: 63
- failed columns: nessuno
