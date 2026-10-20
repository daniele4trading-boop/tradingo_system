# TG TradinGo Monitor Webapp

Dashboard mobile per monitorare bridge, MT5, link Gamehosting, canali e **PnL**.
**Fase 1:** semafori + eventi bridge. **Fase 2:** PnL per canale, posizioni aperte,
equity curve, stats esecuzione EA. Fase 3 (ordini) predisposta ma disabilitata
(`orders_enabled: false`).

## Architettura

- Processo Python unico (FastAPI + uvicorn) su **Gamehosting** (`WIN-72D7M1PCJC6`),
  `0.0.0.0:8610`. Sulla stessa macchina la porta 8600 è dell'Agent Hub.
- Avvio: task `TG_TradinGo_WebappAtLogon` → `run_webapp_task.cmd` (venv
  `C:\TG_TradinGo\.venv`, salta l'avvio se la webapp è già attiva).
- Collector in thread background (refresh 15s), tutte le letture con timeout.
- Accesso **solo via Tailscale**. Telefono/PC: `http://100.74.9.8:8610`.

## Sicurezza

- Login utente/password (hash PBKDF2-SHA256, 200k iterazioni).
- Sessione cookie HMAC firmato, scadenza 12h.
- Rate limit: 5 tentativi falliti → blocco 15 min + honeypot antibot.
- `webapp_config.json` solo sul VPS (non in git).

## Setup Contabo (una tantum)

```powershell
cd C:\TG_TradinGo
pip install -r requirements.txt
Copy-Item webapp_config.example.json webapp_config.json
python -m webapp.hash_password
C:\TG_TradinGo\start_webapp.bat
```

## Fase 2 — fonti dati

Ogni conto è una voce di `accounts` e produce un riquadro nella sezione **Conti**
(equity, oggi/7g/30g, floating, posizioni aperte). Senza `accounts` la webapp usa
`checks.ea_journal_dir` come conto unico, quindi una config vecchia resta valida.

```json
"accounts": [
  { "id": "vantage", "label": "Vantage demo (Contabo)",
    "ea_journal_dir": "C:\\Users\\...\\AE2CC2E0...\\MQL5\\Files\\journal",
    "signal_stats": "C:\\Users\\...\\AE2CC2E0...\\MQL5\\Files\\tradingo_signal_stats.csv" },
  { "id": "ultima", "label": "Ultima demo iFunds (Gamehosting)",
    "ea_journal_dir": "\\\\100.74.9.8\\tradingo_journal",
    "start_date": "2026-08-05" }
]
```

- `start_date` scarta la storia precedente: accetta un giorno (`2026-08-05`) o un
  istante (`2026-08-05T10:05:00Z`, utile dopo un reset a metà giornata). Serve dopo un reset saldo,
  altrimenti equity e PnL includono la storia del conto vecchio.
- I conti remoti arrivano via share SMB in sola lettura sulla cartella journal
  dell'EA (sul Gamehosting: share `tradingo_journal`). Il mount va fatto nella
  stessa sessione di logon della webapp — lo fa `run_webapp_task.cmd`.
- Il primo conto della lista alimenta anche il PnL per canale nella sezione Canali.

Da `accounts[].ea_journal_dir` (MQL5\\Files\\journal):

| Path | Uso |
|------|-----|
| `trades\trades_YYYYMMDD.csv` | PnL chiuso oggi/7g/30g, posizioni aperte |
| `equity\equity_YYYYMMDD.csv` | equity, floating, sparkline |
| `..\tradingo_signal_stats.csv` | eseguiti vs annullati |

### Canale di un trade

Il canale si ricava prima dal `magic` del CSV, poi dalla colonna `channel`:

- `build_magic_map` legge i `magic_base` da `tradingo_config.json` (ricaricato a ogni
  ciclo se cambia la data del file) → `{magic_base: channel_id}`.
- `prefix = (int(float(magic)) // 1000) * 1000`, quindi un nuovo canale deve avere un
  `magic_base` multiplo di 1000 non ancora usato (i TP sono `magic_base + 1..3`).
- Se il magic non è in mappa si usa il tag EA (`GOLD` / `IT` / `AS` / `CH_*`).

## Admin

`/admin` (solo utenti in `admin_users`, default `["daniele"]`): stato terminali MT5,
alert, costi e prelievi. I dati stanno in `C:\TG_TradinGo\webapp_admin_data.json`
(non in git), gestiti da `admin_mgr.py`.

- Auto-discovery: scansiona `%APPDATA%\MetaQuotes\Terminal\*`, legge `origin.txt`,
  cerca CSV in `MQL5\Files\journal\trades\` e controlla `terminal64.exe`.
  Alert se un terminale ha dati ma non è in esecuzione.
- `POST /api/admin/account` imposta label, tipo (`vetrina` / `personale` / `prop`),
  visibilità e `show_equity_total`. **Vantage: `show_equity_total` sempre `false`**
  (si mostra solo il PnL per canale).

## Aggiornamento (senza rifare password)

```powershell
powershell -ExecutionPolicy Bypass -File C:\StatArb\scripts\pull_and_deploy_tg_tradingo.ps1 -Branch cursor/journal-and-exit-hardening-8e22
# poi riavvio dal Task Scheduler:
schtasks /End /TN TG_TradinGo_WebappAtLogon
schtasks /Run /TN TG_TradinGo_WebappAtLogon
```

Opzionale in `webapp_config.json` (se manca):

```json
"pnl_lookback_days": 30,
"equity_days": 3,
"checks": {
  "signal_stats": "C:\\Users\\Administrator\\AppData\\Roaming\\MetaQuotes\\Terminal\\AE2CC2E013FDE1E3CDF010AA51C60400\\MQL5\\Files\\tradingo_signal_stats.csv"
}
```

## Test

```bash
cd tg_tradingo && python -m pytest tests/test_webapp.py tests/test_webapp_pnl.py -v
```
