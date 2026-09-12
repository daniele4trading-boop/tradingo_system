# Piano di riassorbimento sistemi legacy

## Principio

`tradingo_lab` deve diventare il progetto unico per ricerca, backtest e sviluppo strategie. Gli script legacy nella root della repo non devono essere importati, modificati o richiesti per far funzionare il laboratorio.

## Confini da mantenere separati

- Dati storici: `tradingo_lab/data/`
- Config ricerca: `tradingo_lab/config/research_config.json`
- Codice nuovo: `tradingo_lab/src/tradingo_lab/`
- Script nuovi: `tradingo_lab/scripts/`
- Report nuovi: `tradingo_lab/data/reports/`

Non usare nel nuovo progetto:

- `C:/tradingo_math/tradingo.db`
- `tradingo_state.json`
- `ftmo_state.json`
- `prop.log`
- `hedge.log`
- magic number legacy
- credenziali hardcoded nei file root
- logiche live prop/hedge non backtestate

## Fasi

### Fase 1 - Dati

1. Collegare Python al terminale MT5 XM gia' aperto.
2. Esportare metadata simbolo `XAUUSD`.
3. Scaricare tick storici.
4. Scaricare M1 come controllo.
5. Salvare Parquet.

### Fase 2 - Dataset e labeling

1. Costruire barre M1/M5 dai tick.
2. Calcolare indicatori e feature price action.
3. Labeling target 300 punti prima dello stop.
4. Report qualita' dati: gap, spread, sessioni, numero tick.

### Fase 3 - Strategie

Riassorbire una logica alla volta dai sistemi vecchi:

- RSI cross-back M5/M15;
- EMA50 M15;
- ATR SL/TP;
- VWAP/CVD;
- momentum 10 barre;
- kill zones London/NY.

Ogni logica deve diventare una funzione pura testabile sul dataset storico, non un loop live.

### Fase 4 - Ottimizzazione

1. Grid search / Optuna.
2. Walk-forward.
3. Out-of-sample.
4. Stress test spread/slippage.
5. Report ranking setup.

### Fase 5 - Spegnimento legacy

Solo dopo avere validato exporter, backtest e report:

1. fermare i processi legacy sulla VPS;
2. archiviare log e database legacy;
3. rimuovere credenziali hardcoded;
4. eliminare script legacy o spostarli in archivio;
5. mantenere `tradingo_lab` come sorgente unica.

## Criterio minimo prima di cancellare i vecchi sistemi

Non eliminare nulla finche' `tradingo_lab` non produce almeno:

- export tick XM funzionante;
- barre M1/M5 corrette;
- report primo backtest;
- metadata MT5 del simbolo salvato;
- conferma che nessun file nuovo importa moduli legacy.
