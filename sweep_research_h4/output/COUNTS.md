# H4 S0 counts

## GATE

**PASS** — `post_threshold=6582` (need >=3000); `symbols_ge_400=5` (need >=3).

## Decisioni dichiarate

- Equal levels: tolleranza `0.15 ATR`, fissata a priori.
- Segmenti S2 per simbolo: minimo `200` eventi (`s2_min_segment_events=200`).
  I segmenti equal per-simbolo sotto questa soglia sono esclusi da S2; è
  ammesso soltanto il segmento equal pooled (circa 409 eventi).
- `XAGUSD` è escluso dai segmenti S2 pooled (`s2_segments_exclude: [XAGUSD]`):
  la storia disponibile parte da 2022-08 e non copre un ciclo completo.
- La stratificazione annuale XAUUSD è dichiarata **piatta** a priori: non va
  interpretata come evidenza di un effetto annuale né usata per introdurre una
  segmentazione annuale post hoc.

Final population is `dedup ∧ ¬below_min_penetration`, with equal-level tolerance 0.15 ATR.

`COUNTS_step1_tol005.md` is a historical pre-cleaning report. The tol 0.05 comparison below is recomputed on the cleaned M1 data.

XAGUSD uses `symbol_period_start=2022-08-01`; 2015-02→2022-07 M1 history is unavailable in the warehouse although remote tick sync completed.

## Pulizia M1

Configured `drop_flat_zero_volume_m1=true`; rows are dropped only when `volume == 0 AND high == low`.

| Symbol | Rows before | Rows dropped | Dropped % | H4 bars with n_m1 < 30 |
|---|---:|---:|---:|---:|
| XAUUSD | 4094950 | 0 | 0.000% | 11 |
| XAGUSD | 1396099 | 0 | 0.000% | 0 |
| EURUSD | 8710560 | 2531590 | 29.063% | 0 |
| GBPUSD | 8710560 | 2529043 | 29.034% | 1 |
| USDJPY | 8710560 | 2531374 | 29.061% | 0 |
| SPX500 | 6081120 | 2646638 | 43.522% | 326 |

Weekday distribution after filtering (`0=Monday`, ..., `6=Sunday`):

| Symbol | Mon | Tue | Wed | Thu | Fri | Sat | Sun |
|---|---:|---:|---:|---:|---:|---:|---:|
| XAUUSD | 809929 | 827968 | 825576 | 824129 | 748296 | 0 | 59052 |
| XAGUSD | 275304 | 282579 | 281098 | 279966 | 257385 | 0 | 19767 |
| EURUSD | 1235031 | 1236688 | 1234056 | 1235999 | 1102675 | 0 | 134521 |
| GBPUSD | 1236054 | 1237304 | 1234536 | 1236743 | 1102931 | 0 | 133949 |
| USDJPY | 1234788 | 1237059 | 1234738 | 1236475 | 1102189 | 0 | 133937 |
| SPX500 | 673615 | 693519 | 692424 | 693306 | 641017 | 0 | 40601 |

| Symbol | Source | M1 files | H4 bars | Raw | Dedup | Post-threshold | Dropped no next bar | Mean exec cost bp | Range |
|---|---|---:|---:|---:|---:|---:|---:|---:|---|
| XAUUSD | dukascopy | 139 | 18085 | 2814 | 1423 | 1028 | 0 | 0.270776 | 2015-01-01T21:00:00 → 2026-07-24T17:00:00 |
| XAGUSD | dukascopy | 48 | 6234 | 974 | 486 | 327 | 0 | -0.578233 | 2022-08-01T01:00:00 → 2026-07-24T17:00:00 |
| EURUSD | dukascopy_candles | 199 | 26136 | 4256 | 2115 | 1457 | 0 | 0.108114 | 2009-12-31T21:00:00 → 2026-07-24T17:00:00 |
| GBPUSD | dukascopy_candles | 199 | 26135 | 4539 | 2240 | 1542 | 0 | 0.067412 | 2009-12-31T21:00:00 → 2026-07-24T17:00:00 |
| USDJPY | dukascopy_candles | 199 | 26130 | 4106 | 1978 | 1387 | 0 | 0.066320 | 2009-12-31T21:00:00 → 2026-07-24T17:00:00 |
| SPX500 | dukascopy_candles | 139 | 16625 | 2509 | 1249 | 841 | 0 | 0.030297 | 2015-01-01T21:00:00 → 2026-07-24T17:00:00 |

## Equal tolerance comparison

| Symbol | Post-threshold tol 0.05 | Equal 0.05 | Post-threshold tol 0.15 | Equal 0.15 |
|---|---:|---:|---:|---:|
| XAUUSD | 1031 | 19 | 1028 | 61 |
| XAGUSD | 325 | 6 | 327 | 23 |
| EURUSD | 1463 | 34 | 1457 | 83 |
| GBPUSD | 1549 | 39 | 1542 | 90 |
| USDJPY | 1376 | 28 | 1387 | 97 |
| SPX500 | 842 | 19 | 841 | 55 |

## Post-threshold by year and level_type

### XAUUSD

| Year | Events | Median ATR pts | Median ATR / close bp |
|---:|---:|---:|---:|
| 2015 | 88 | 5.356360 | 46.912583 |
| 2016 | 65 | 6.163550 | 49.054025 |
| 2017 | 114 | 4.811177 | 38.146428 |
| 2018 | 113 | 4.467310 | 35.344506 |
| 2019 | 67 | 5.408457 | 37.731354 |
| 2020 | 81 | 10.560371 | 56.446913 |
| 2021 | 117 | 8.645752 | 47.705996 |
| 2022 | 84 | 9.223463 | 50.734662 |
| 2023 | 79 | 7.852341 | 41.460960 |
| 2024 | 88 | 11.737271 | 48.167946 |
| 2025 | 80 | 19.820781 | 57.006885 |
| 2026 | 52 | 40.923570 | 90.208992 |

| Level type | Count |
|---|---:|
| equal_high | 22 |
| equal_low | 39 |
| swing_high_10 | 146 |
| swing_high_20 | 83 |
| swing_high_5 | 259 |
| swing_low_10 | 121 |
| swing_low_20 | 100 |
| swing_low_5 | 258 |

### XAGUSD

| Year | Events | Median ATR pts | Median ATR / close bp |
|---:|---:|---:|---:|
| 2022 | 28 | 0.250996 | 125.949771 |
| 2023 | 72 | 0.218654 | 94.617488 |
| 2024 | 102 | 0.305111 | 106.030164 |
| 2025 | 71 | 0.331973 | 96.553320 |
| 2026 | 54 | 1.424455 | 200.768681 |

| Level type | Count |
|---|---:|
| equal_high | 8 |
| equal_low | 15 |
| swing_high_10 | 47 |
| swing_high_20 | 22 |
| swing_high_5 | 81 |
| swing_low_10 | 39 |
| swing_low_20 | 38 |
| swing_low_5 | 77 |

### EURUSD

| Year | Events | Median ATR pts | Median ATR / close bp |
|---:|---:|---:|---:|
| 2010 | 88 | 0.005491 | 42.725408 |
| 2011 | 102 | 0.005969 | 42.874071 |
| 2012 | 88 | 0.003846 | 29.804106 |
| 2013 | 109 | 0.003591 | 27.336186 |
| 2014 | 88 | 0.002561 | 18.612282 |
| 2015 | 77 | 0.004255 | 37.693160 |
| 2016 | 100 | 0.003395 | 30.298331 |
| 2017 | 91 | 0.003148 | 27.379146 |
| 2018 | 84 | 0.003149 | 26.701292 |
| 2019 | 112 | 0.002030 | 18.250899 |
| 2020 | 97 | 0.002975 | 26.409707 |
| 2021 | 75 | 0.002376 | 19.991296 |
| 2022 | 66 | 0.003996 | 38.037597 |
| 2023 | 65 | 0.002856 | 26.323883 |
| 2024 | 84 | 0.002292 | 21.278922 |
| 2025 | 77 | 0.003006 | 27.194627 |
| 2026 | 54 | 0.002560 | 21.862285 |

| Level type | Count |
|---|---:|
| equal_high | 45 |
| equal_low | 38 |
| swing_high_10 | 199 |
| swing_high_20 | 147 |
| swing_high_5 | 374 |
| swing_low_10 | 166 |
| swing_low_20 | 148 |
| swing_low_5 | 340 |

### GBPUSD

| Year | Events | Median ATR pts | Median ATR / close bp |
|---:|---:|---:|---:|
| 2010 | 108 | 0.006073 | 38.553593 |
| 2011 | 101 | 0.005488 | 34.140262 |
| 2012 | 109 | 0.003977 | 25.132654 |
| 2013 | 100 | 0.003986 | 25.323086 |
| 2014 | 84 | 0.003611 | 22.194860 |
| 2015 | 90 | 0.004363 | 28.543936 |
| 2016 | 104 | 0.005175 | 37.803380 |
| 2017 | 78 | 0.003616 | 28.743899 |
| 2018 | 87 | 0.003834 | 28.797869 |
| 2019 | 96 | 0.003735 | 29.711983 |
| 2020 | 74 | 0.004634 | 36.154700 |
| 2021 | 85 | 0.003394 | 24.548734 |
| 2022 | 83 | 0.004544 | 37.584742 |
| 2023 | 61 | 0.003748 | 29.831107 |
| 2024 | 104 | 0.002924 | 22.924306 |
| 2025 | 121 | 0.003286 | 24.970368 |
| 2026 | 57 | 0.003182 | 23.506305 |

| Level type | Count |
|---|---:|
| equal_high | 50 |
| equal_low | 40 |
| swing_high_10 | 186 |
| swing_high_20 | 149 |
| swing_high_5 | 379 |
| swing_low_10 | 214 |
| swing_low_20 | 165 |
| swing_low_5 | 359 |

### USDJPY

| Year | Events | Median ATR pts | Median ATR / close bp |
|---:|---:|---:|---:|
| 2010 | 98 | 0.347203 | 38.843084 |
| 2011 | 128 | 0.236507 | 30.201186 |
| 2012 | 65 | 0.196602 | 24.537179 |
| 2013 | 65 | 0.381016 | 39.255796 |
| 2014 | 80 | 0.284875 | 27.636163 |
| 2015 | 82 | 0.355375 | 29.531621 |
| 2016 | 72 | 0.422889 | 40.197114 |
| 2017 | 88 | 0.358195 | 32.048107 |
| 2018 | 80 | 0.277495 | 25.474087 |
| 2019 | 78 | 0.220659 | 20.310540 |
| 2020 | 68 | 0.226979 | 21.313436 |
| 2021 | 95 | 0.220960 | 21.023319 |
| 2022 | 67 | 0.521361 | 38.880700 |
| 2023 | 93 | 0.465418 | 33.411087 |
| 2024 | 81 | 0.579798 | 39.067466 |
| 2025 | 85 | 0.521576 | 35.229198 |
| 2026 | 62 | 0.383479 | 24.034535 |

| Level type | Count |
|---|---:|
| equal_high | 33 |
| equal_low | 64 |
| swing_high_10 | 149 |
| swing_high_20 | 141 |
| swing_high_5 | 313 |
| swing_low_10 | 182 |
| swing_low_20 | 142 |
| swing_low_5 | 363 |

### SPX500

| Year | Events | Median ATR pts | Median ATR / close bp |
|---:|---:|---:|---:|
| 2015 | 61 | 9.834302 | 46.900136 |
| 2016 | 60 | 10.610909 | 50.592410 |
| 2017 | 51 | 6.471476 | 27.927574 |
| 2018 | 60 | 14.895013 | 55.151629 |
| 2019 | 73 | 11.208016 | 38.055200 |
| 2020 | 104 | 21.641558 | 63.451234 |
| 2021 | 75 | 17.580230 | 40.351738 |
| 2022 | 47 | 31.362773 | 76.843940 |
| 2023 | 93 | 17.573327 | 42.474457 |
| 2024 | 77 | 23.947640 | 42.094861 |
| 2025 | 91 | 27.105271 | 43.902130 |
| 2026 | 49 | 34.828710 | 50.542158 |

| Level type | Count |
|---|---:|
| equal_high | 24 |
| equal_low | 31 |
| swing_high_10 | 81 |
| swing_high_20 | 81 |
| swing_high_5 | 177 |
| swing_low_10 | 135 |
| swing_low_20 | 79 |
| swing_low_5 | 233 |

GATE: **PASS** — post_threshold=6582 (need >=3000); symbols_ge_400=5 (need >=3)

## Soglia vs volatilità

Spearman tra il numero di eventi post-threshold per anno e ATR mediano/close
in basis point per anno. Sono esclusi il 2026 e l'anno iniziale se copre meno
di 12 mesi; `n` è il numero di anni utilizzati. L'anno iniziale XAGUSD
(2022-08→2022-12) è quindi escluso. Il controllo è descrittivo e l'ipotesi
dichiarata è rho circa zero.

| Symbol | n anni | Spearman rho |
|---|---:|---:|
| XAUUSD | 11 | -0.264 |
| XAGUSD | 3 | 0.500 |
| EURUSD | 16 | 0.050 |
| GBPUSD | 16 | -0.116 |
| USDJPY | 16 | -0.050 |
| SPX500 | 11 | -0.087 |

## Pooled per level_type

| Level type | Count |
|---|---:|
| equal_high | 182 |
| equal_low | 227 |
| **equal pooled** | **409** |
| swing_high_10 | 808 |
| swing_high_20 | 623 |
| swing_high_5 | 1,583 |
| swing_low_10 | 857 |
| swing_low_20 | 672 |
| swing_low_5 | 1,630 |

## Symbol × level_type

| Symbol | equal_high | equal_low | swing_high_10 | swing_high_20 | swing_high_5 | swing_low_10 | swing_low_20 | swing_low_5 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| XAUUSD | 22 | 39 | 146 | 83 | 259 | 121 | 100 | 258 |
| XAGUSD | 8 | 15 | 47 | 22 | 81 | 39 | 38 | 77 |
| EURUSD | 45 | 38 | 199 | 147 | 374 | 166 | 148 | 340 |
| GBPUSD | 50 | 40 | 186 | 149 | 379 | 214 | 165 | 359 |
| USDJPY | 33 | 64 | 149 | 141 | 313 | 182 | 142 | 363 |
| SPX500 | 24 | 31 | 81 | 81 | 177 | 135 | 79 | 233 |
