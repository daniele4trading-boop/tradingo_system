# `sweep_research_h4`

Pipeline isolata per uno studio causale H4 multi-strumento. Il pacchetto non
modifica né importa il modulo legacy `sweep_research/`.

## Scopo e ipotesi

S0 costruisce un dataset riproducibile di eventi di sweep, outcome e feature
causali per:

```text
XAUUSD, XAGUSD, EURUSD, GBPUSD, USDJPY, SPX500
```

L'ipotesi dichiarata in YAML è:

```yaml
hypothesis: continuation
```

La direzione è quindi **one-sided**: dopo lo sweep si misura la continuazione
nella direzione del trade (`trade_sign`). Non viene assunta né testata in S0
un'ipotesi di reversal. Un'eventuale ipotesi reversal dovrà essere dichiarata
a priori in una successiva specifica, non scelta dopo aver visto i risultati.

S0 è un event study e non una strategia esecutiva completa. Non contiene
equity curve, sizing, ordini reali o una simulazione di costi di mercato
realistici.

## Universo, fonti e range effettivi

| Strumento | Fonte | Range effettivo |
|---|---|---|
| XAUUSD | tick → M1 locale | 2015-01 → 2026-07 |
| XAGUSD | tick → M1 locale | 2022-08 → 2026-07 |
| EURUSD | candele M1 BID+ASK Dukascopy | 2010-01 → 2026-07 |
| GBPUSD | candele M1 BID+ASK Dukascopy | 2010-01 → 2026-07 |
| USDJPY | candele M1 BID+ASK Dukascopy | 2010-01 → 2026-07 |
| SPX500 | candele M1 BID+ASK Dukascopy | 2015-01 → 2026-07 |

Per le candele Dukascopy vengono mantenuti solo i minuti con informazione
effettiva: il loader elimina le righe per cui **entrambe** le condizioni sono
vere:

```text
volume == 0 AND high == low
```

Le righe flat con volume positivo restano. Le barre H4 parziali non vengono
scartate; il loro numero di minuti è esposto in `n_m1_bar`.

XAGUSD è configurato con:

```yaml
symbol_period_start:
  XAGUSD: "2022-08-01"
```

Nel warehouse esiste gennaio 2015, ma non esiste M1 per febbraio 2015–luglio
2022. Il sync remoto ha scritto tick per quel tratto, ma non ha prodotto M1
utilizzabile. Il mese isolato 2015-01 viene quindi escluso per evitare che
ATR, livelli e rolling inizino senza contesto e poi attraversino un buco di
sette anni.

## Aggregazione H4

I dati M1 vengono aggregati su una griglia UTC fissa con:

```yaml
h4:
  anchor_utc_hour: 21
```

Ogni barra copre quattro ore a partire dall'ancora `21:00 UTC`. La base
prezzo è `mid`, costruita come:

```text
mid = bid + spread_med / 2
```

Le barre H4 vuote non vengono fabbricate. Per questo la distanza tra due barre
può essere maggiore di quattro ore: `next_bar_gap_h` conserva il gap osservato,
inclusi i gap di fine settimana e le chiusure del mercato.

## Evento, deduplica e soglia

Un evento è un sweep causale di un livello già formato e confermato entro il
close della barra evento. Sono considerati livelli equal e swing con
`n = 5, 10, 20`. La popolazione finale è:

```text
dedup ∧ ¬below_min_penetration
```

La soglia minima è dichiarata in YAML:

```yaml
min_penetration_atr: 0.10
```

La deduplica usa la chiave:

```text
(symbol, event_ts_utc, dir)
```

La priorità, dalla più alta alla più bassa, è:

```text
equal > swing20 > swing10 > swing5
```

Tutte le informazioni usate per formare il livello, confermarlo e rilevare lo
sweep rispettano il cutoff causale dell'evento.

## Entry e outcome

`entry_next_open` è l'entry primaria: l'esecuzione avviene all'open della
barra successiva disponibile. Sono conservati anche:

- `entry_bar_close`;
- `exec_cost_bp`, con `spread_bp_entry` e `cost_rt_bp`;
- forward return a 1, 2, 4 e 12 barre, lordo, direzionale e dopo costo;
- MFE/MAE a 12 barre, in punti e ATR;
- `label_start_ts`, `label_end_ts` e `label_truncated`;
- `next_bar_gap_h` e `n_m1_bar`.

Il drift è calcolato per `(symbol, year)` con detrending dichiarato in YAML.
Non esiste una regola o una feature `cluster-day`: eventi dello stesso giorno
non vengono accorpati per questa ragione.

## Feature panel

Il pannello causale include:

- geometria dello sweep: `wick_ratio`, `dist_from_level_atr`,
  `bar_range_atr`, `body_ratio`;
- contesto del livello: `liquidity_density`, `level_age_bars`,
  `level_touch_count`;
- volatilità e dinamica: `atr_regime`, `volatility_regime`,
  `vol_vs_median`, `efficiency_ratio`, `ret_5_bp`, `ret_20_bp`;
- posizione temporale e range: `pos_in_week_range`, `pos_in_month_range`,
  `dist_52w_high_atr`, `dist_52w_low_atr`, `day_of_month`,
  `week_of_month`, `weekday`, `bar_slot`, `year`, `month`;
- contesto esterno allineato backward: `dxy_level`, `dxy_chg_5_bp`,
  `dxy_chg_20_bp`, `ust_level`, `ust_chg_5_bp`, `ust_chg_20_bp`;
- correlazioni rolling `corr20_*`;
- `macro_high_impact_in_bar`.

Le feature microstrutturali che richiederebbero tick/flow affidabile
omogeneo nell'intero universo sono state rimosse dal pannello H4. In
particolare `delta_est` e `cvd_session` sono omesse: non sono confrontabili
tra il dataset tick→M1 e le candele BID+ASK e introdurrebbero una proxy
microstrutturale non uniforme.

Il calendario macro contiene solo NFP verificati. FOMC è omesso perché il
parsing storico non era sufficientemente affidabile; CPI è omesso perché la
fonte BLS ha risposto HTTP 403. Non sono state inserite date inventate.

Le chiavi settimanali e mensili usano `ts + 3h`, così la barra domenicale
21:00 UTC ricade nella settimana/mese successivo. `day_of_month`, `weekday` e
`week_of_month` usano invece `ts` non traslato. Le variazioni `dxy_chg_k` e
`ust_chg_k` usano k barre della griglia H4 del simbolo dopo allineamento
backward; `corr20_*` include il rendimento della barra `t`.

## Regole vincolanti per gli stadi successivi

Queste regole sono dichiarate a priori e vincolano S1/S2:

1. `symbol` è un effetto fisso. Ogni aggregato pooled in S1/S2 deve essere
   riportato anche per simbolo e per `level_type`; il pooled da solo non è
   interpretabile.
2. `s2_segments_exclude: [XAGUSD]`: XAGUSD è fuori dai segmenti pooled di S2.
   La storia parte dal 2022-08 e non contiene un ciclo completo.
3. La tolleranza equal-level è fissata a priori a `0.15 ATR`. Con meno di
   `s2_min_segment_events: 200` eventi per simbolo, il segmento equal
   per-simbolo è escluso da S2. Questo esclude XAUUSD (61), FX (83–97) e
   SPX500 (55); è ammesso solo il segmento equal pooled (circa 409 eventi).
4. Il walk-forward futuro è allineato per data di calendario tra simboli, non
   separatamente per simbolo.
5. L'asimmetria `sweep_high`/`sweep_low` è un'ipotesi della griglia a priori,
   non una conclusione già nota dai dati.

La causalità è controllata con un harness di truncation: vengono ricalcolate
le feature, comprese external e correlation, su frame troncati al cutoff.
`leakage.json` elenca le colonne verificate e i confronti per simbolo; il test
negativo inserisce una colonna futura e deve fallire solo per quella colonna.

## Come eseguire

Attivare `/home/ubuntu/venv-sweep` e dalla root del repository:

```bash
PYTHONPATH=. /home/ubuntu/venv-sweep/bin/python \
  -m sweep_research_h4 run \
  --stage count \
  --config sweep_research_h4/config/h4_basket.yaml
```

Run S0 completo:

```bash
PYTHONPATH=. /home/ubuntu/venv-sweep/bin/python \
  -m sweep_research_h4 run \
  --stage s0 \
  --config sweep_research_h4/config/h4_basket.yaml
```

Gli override sono configurabili senza hard-code, per esempio:

```bash
... -m sweep_research_h4 run \
  --stage count \
  --config sweep_research_h4/config/h4_basket.yaml \
  --set equal_level_tol_atr=0.05
```

Test e lint:

```bash
PYTHONPATH=. /home/ubuntu/venv-sweep/bin/pytest sweep_research_h4/tests -q
/home/ubuntu/venv-sweep/bin/ruff check sweep_research_h4 \
  --config sweep_research_h4/ruff.toml
```

## Artefatti e limiti

S0 produce:

```text
output/events_h4.parquet
output/events_h4_raw.parquet
output/drift_table.parquet
output/counts.json
output/COUNTS.md
output/COUNTS_step1_tol005.md
output/SCHEMA.md
output/leakage.json
output/manifest.json
```

Il parquet principale contiene eventi, outcome e pannello feature; il manifest
registra configurazione, input hashati e metadati di pulizia M1. Gli output
parquet sono esclusi dal versionamento tramite `.gitignore`.

Questo pacchetto **non contiene** risultati S1+, equity curve, una strategia
tradabile completa, sizing, execution simulator o costi realistici calibrati
su liquidità/market impact. I risultati S1 e S2 non sono stati ancora
avviati.

## Stato

**S0 completato. S1 non avviato.**
