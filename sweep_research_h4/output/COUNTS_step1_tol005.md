# H4 Step 1 counts

Dedup key: `(symbol, event_ts_utc, dir)`; priority: equal > swing20 > swing10 > swing5.
Post-threshold means `dedup ∧ ¬below_min_penetration` with penetration measured in ATR14.

| Symbol | Source | M1 files | H4 bars | Raw | Dedup | Post-threshold | Range |
|---|---|---:|---:|---:|---:|---:|---|
| XAUUSD | dukascopy | 139 | 18085 | 2755 | 1423 | 1031 | 2015-01-01T21:00:00 → 2026-07-24T17:00:00 |
| XAGUSD | dukascopy | 48 | 6234 | 948 | 486 | 325 | 2022-08-01T01:00:00 → 2026-07-24T17:00:00 |
| EURUSD | dukascopy_candles | 199 | 36295 | 5899 | 2697 | 2035 | 2009-12-31T21:00:00 → 2026-07-24T21:00:00 |
| GBPUSD | dukascopy_candles | 199 | 36295 | 6364 | 2853 | 2180 | 2009-12-31T21:00:00 → 2026-07-24T21:00:00 |
| USDJPY | dukascopy_candles | 199 | 36295 | 5695 | 2566 | 1968 | 2009-12-31T21:00:00 → 2026-07-24T21:00:00 |
| SPX500 | dukascopy_candles | 139 | 25339 | 3789 | 1686 | 1310 | 2014-12-31T21:00:00 → 2026-07-24T21:00:00 |
| **TOTAL** | — | — | 158543 | 25450 | 11711 | 8849 | — |

GATE: **PASS** — post_threshold=8849 (need >=3000); symbols_ge_400=5 (need >=3)

## XAUUSD

### Post-threshold per anno

| Anno | Count |
|---:|---:|
| 2015 | 89 |
| 2016 | 65 |
| 2017 | 115 |
| 2018 | 114 |
| 2019 | 67 |
| 2020 | 81 |
| 2021 | 117 |
| 2022 | 84 |
| 2023 | 79 |
| 2024 | 88 |
| 2025 | 80 |
| 2026 | 52 |

### Post-threshold per level_type

| Level type | Count |
|---|---:|
| equal_high | 7 |
| equal_low | 12 |
| swing_high_10 | 156 |
| swing_high_20 | 85 |
| swing_high_5 | 263 |
| swing_low_10 | 136 |
| swing_low_20 | 105 |
| swing_low_5 | 267 |

## XAGUSD

### Post-threshold per anno

| Anno | Count |
|---:|---:|
| 2022 | 28 |
| 2023 | 73 |
| 2024 | 102 |
| 2025 | 69 |
| 2026 | 53 |

### Post-threshold per level_type

| Level type | Count |
|---|---:|
| equal_low | 6 |
| swing_high_10 | 53 |
| swing_high_20 | 23 |
| swing_high_5 | 82 |
| swing_low_10 | 46 |
| swing_low_20 | 38 |
| swing_low_5 | 77 |

## EURUSD

### Post-threshold per anno

| Anno | Count |
|---:|---:|
| 2010 | 133 |
| 2011 | 132 |
| 2012 | 117 |
| 2013 | 136 |
| 2014 | 124 |
| 2015 | 105 |
| 2016 | 132 |
| 2017 | 140 |
| 2018 | 139 |
| 2019 | 150 |
| 2020 | 135 |
| 2021 | 100 |
| 2022 | 99 |
| 2023 | 102 |
| 2024 | 112 |
| 2025 | 110 |
| 2026 | 69 |

### Post-threshold per level_type

| Level type | Count |
|---|---:|
| equal_high | 18 |
| equal_low | 16 |
| swing_high_10 | 366 |
| swing_high_20 | 287 |
| swing_high_5 | 398 |
| swing_low_10 | 338 |
| swing_low_20 | 241 |
| swing_low_5 | 371 |

## GBPUSD

### Post-threshold per anno

| Anno | Count |
|---:|---:|
| 2010 | 145 |
| 2011 | 142 |
| 2012 | 158 |
| 2013 | 119 |
| 2014 | 122 |
| 2015 | 122 |
| 2016 | 141 |
| 2017 | 116 |
| 2018 | 124 |
| 2019 | 131 |
| 2020 | 112 |
| 2021 | 125 |
| 2022 | 116 |
| 2023 | 105 |
| 2024 | 150 |
| 2025 | 171 |
| 2026 | 81 |

### Post-threshold per level_type

| Level type | Count |
|---|---:|
| equal_high | 36 |
| equal_low | 11 |
| swing_high_10 | 345 |
| swing_high_20 | 288 |
| swing_high_5 | 424 |
| swing_low_10 | 374 |
| swing_low_20 | 297 |
| swing_low_5 | 405 |

## USDJPY

### Post-threshold per anno

| Anno | Count |
|---:|---:|
| 2010 | 170 |
| 2011 | 186 |
| 2012 | 121 |
| 2013 | 91 |
| 2014 | 106 |
| 2015 | 118 |
| 2016 | 94 |
| 2017 | 120 |
| 2018 | 115 |
| 2019 | 114 |
| 2020 | 101 |
| 2021 | 114 |
| 2022 | 93 |
| 2023 | 126 |
| 2024 | 110 |
| 2025 | 114 |
| 2026 | 75 |

### Post-threshold per level_type

| Level type | Count |
|---|---:|
| equal_high | 12 |
| equal_low | 18 |
| swing_high_10 | 298 |
| swing_high_20 | 247 |
| swing_high_5 | 340 |
| swing_low_10 | 328 |
| swing_low_20 | 257 |
| swing_low_5 | 468 |

## SPX500

### Post-threshold per anno

| Anno | Count |
|---:|---:|
| 2015 | 105 |
| 2016 | 141 |
| 2017 | 137 |
| 2018 | 97 |
| 2019 | 94 |
| 2020 | 125 |
| 2021 | 105 |
| 2022 | 80 |
| 2023 | 129 |
| 2024 | 105 |
| 2025 | 119 |
| 2026 | 73 |

### Post-threshold per level_type

| Level type | Count |
|---|---:|
| equal_high | 10 |
| equal_low | 18 |
| swing_high_10 | 227 |
| swing_high_20 | 162 |
| swing_high_5 | 201 |
| swing_low_10 | 227 |
| swing_low_20 | 175 |
| swing_low_5 | 290 |
