# TradinGo Lab

Nuovo progetto isolato per ricerca, download dati storici da MT5/XM, backtest e ottimizzazione strategie su XAUUSD.

Questo progetto e' separato dagli script legacy presenti nella root della repo. L'obiettivo e' riassorbire progressivamente le idee valide dei sistemi esistenti senza dipendere dai loro file, stati, database o processi live.

## Risposta operativa VPS / XM

### Download dati: automatico o CSV?

La via principale deve essere automatica:

1. Python si collega al terminale MT5 XM gia' aperto sulla VPS Windows.
2. Scarica tick storici con `copy_ticks_range`.
3. Scarica anche M1 con `copy_rates_range` come backup/controllo qualita'.
4. Salva tutto in Parquet sotto `data/`.

Il CSV M1 puo' restare un fallback se XM non restituisce abbastanza tick storici o se il terminale MT5 non espone tutta la profondita' storica richiesta. Non lo userei come flusso principale.

### Cosa posso fare in autonomia

Posso preparare e mantenere nel progetto:

- struttura separata del laboratorio;
- script di connessione MT5;
- exporter automatico tick e M1;
- configurazione per XM/XAUUSD;
- salvataggio dati in Parquet;
- costruzione barre da tick;
- indicatori e feature engineering;
- labeling dei movimenti da almeno 300 punti;
- backtest iniziale con spread reale bid/ask;
- report e metriche;
- ottimizzazione parametri;
- istruzioni per avvio su VPS Windows;
- successiva migrazione delle logiche buone dai sistemi legacy.

### Cosa non posso fare direttamente da questa macchina cloud

Da qui non posso collegarmi fisicamente al tuo MT5 aperto sulla VPS Windows. Quindi non posso:

- verificare live che il terminale XM sia accessibile;
- leggere lo storico reale XM dalla tua VPS;
- installare pacchetti sulla VPS se non mi dai accesso a quell'ambiente;
- ruotare credenziali broker gia' finite nei file legacy;
- spegnere o cancellare sistemi attualmente in esecuzione sulla VPS.

Posso pero' preparare gli script in modo che, copiati o pullati sulla VPS Windows, facciano questi controlli automaticamente.

## Setup su VPS Windows

Da PowerShell, dentro questa cartella:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Poi modifica `.env` solo se vuoi forzare path/login/server. Se il terminale XM e' gia' aperto e loggato, puoi lasciare login/password vuoti.

Esempio:

```powershell
$env:TRADINGO_MT5_PATH="C:\Program Files\XM Global MT5\terminal64.exe"
```

## Export automatico dati

```powershell
python scripts/export_mt5_history.py --config config/research_config.json --days 30 --ticks --rates
```

Per un intervallo esplicito:

```powershell
python scripts/export_mt5_history.py --config config/research_config.json --start 2026-01-01 --end 2026-01-31 --ticks --rates
```

## Build barre da tick

```powershell
python scripts/build_dataset.py --config config/research_config.json --timeframe 1min
python scripts/build_dataset.py --config config/research_config.json --timeframe 5min
```

## Primo backtest target 300 punti

```powershell
python scripts/run_first_backtest.py --config config/research_config.json --bars data/bars/XAUUSD/1min.parquet
```


## Ottimizzazione primi setup

Dopo avere creato `data/bars/XAUUSD/1min.parquet`:

```powershell
python scripts/optimize_first_setups.py --config config/research_config.json --bars data/bars/XAUUSD/1min.parquet --min-trades 15 --top 30
```

Genera:

```text
data/reports/XAUUSD/optimization_candidates.csv
data/reports/XAUUSD/optimization_top.csv
```

L'optimizer testa famiglie di setup oggettive:

- ICT reversal: sweep high/low con conferma displacement/FVG;
- RSI reversion: soglie RSI con/senza sweep;
- trend continuation: EMA trend + VWAP + ADX, con/senza displacement;
- target/stop/horizon multipli;
- filtri London/NY/all day;
- filtro spread.

I risultati vanno letti cercando profit factor, expectancy positiva, numero trade sufficiente e stabilita' tra sessioni.

## Report diagnostico

Dopo il primo backtest:

```powershell
python scripts/report_dataset.py --config config/research_config.json --bars data/bars/XAUUSD/1min.parquet
```

Genera:

```text
data/reports/XAUUSD/diagnostic_report.json
data/reports/XAUUSD/diagnostic_trades_by_hour.csv
```

Serve a controllare copertura dati, gap, spread, distribuzione oraria e win rate dei setup.

## Se MT5/XM esporta solo M1 e non tick

Lo script `build_dataset.py` ora usa `--source auto` di default: prova prima i tick e, se non trova file tick, usa i rates M1 esportati in `data/rates/XAUUSD/M1/`.

Dopo una riga simile a:

```text
Rates file written: ...\data\rates\XAUUSD\M1\YYYYMMDD_YYYYMMDD.parquet
```

puoi continuare con:

```powershell
python scripts/build_dataset.py --config config/research_config.json --timeframe 1min
python scripts/run_first_backtest.py --config config/research_config.json --bars data/bars/XAUUSD/1min.parquet
```

Se vuoi forzare l'uso delle candele M1 esportate da MT5:

```powershell
python scripts/build_dataset.py --config config/research_config.json --timeframe 1min --source rates
```

## Convenzioni importanti

- `point` viene letto da MT5. Su XAUUSD a 2 decimali normalmente `point = 0.01`.
- Target 300 punti = `300 * point`, quindi circa `3.00` dollari se `point = 0.01`.
- I dati raw non vanno committati in git.
- Prima di eliminare sistemi legacy, questo progetto deve avere exporter, backtest e report funzionanti sulla VPS.
