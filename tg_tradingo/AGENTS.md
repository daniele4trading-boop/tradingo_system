# TG TradinGo — contesto per agenti Cursor / Cloud

Questo documento descrive il sistema **Telegram → JSON → MT4/MT5** nella repo `tradingo_system`.
Leggilo prima di modificare parser, config o EA.

**Branch canonico: `main`** (`git fetch origin main && git checkout main && git pull`).
Versioni correnti — bridge **2.25**, EA MT5 **2.25**, EA MT4 **1.12** — e mappa preset → conto in
[`docs/VERSIONS_AND_PRESETS.md`](docs/VERSIONS_AND_PRESETS.md).
Setup MT4 T4Trade / path iFunds: [`docs/MT4_T4TRADE_SETUP.md`](docs/MT4_T4TRADE_SETUP.md).

## Dove sono i file

| Percorso repo | Percorso VPS produzione |
|---------------|-------------------------|
| `tg_tradingo/` | `C:\TG_TradinGo\` |
| `tg_tradingo/tradingo_config.example.json` | `C:\TG_TradinGo\tradingo_config.json` (segreti, non in git) |
| `tg_tradingo/docs/fixtures/` | campioni messaggi Telegram reali |
| `statarb/` (root `C:\StatArb`) | StatArb pairs trading — progetto separato nella stessa repo |

**GitHub:** `https://github.com/daniele4trading-boop/tradingo_system.git`  
**VPS:** `144.91.76.28` · SSH porta `2222` · utente `Administrator`

---

## Architettura

```
Telegram (canali VIP)
        │
        ▼
tradingo_bridge.py  (Telethon, auto-restart)
        │  parser per canale (unica fonte di verità)
        ▼
signal_ch_*.json  (uno per canale)
        │
        ├─► TG_TradinGoEA.mq5  (MT5: Vantage, Ultima, iFunds, …)
        └─► TG_TradinGoEA.mq4  (MT4: T4Trade — stesso contratto JSON)
```

- Il bridge ascolta **NewMessage** e **MessageEdited** (importante per CH2 naked→completo).
- Scrive lo stesso payload in tutti i path di `mt5_instances[].signals_path` (scrittura atomica tmp + replace), inclusi path MT4 `MQL4\Files\...` se configurati.
- Deduplica messaggi Telegram su `state/processed_messages.json` (chiave chat_id + message_id + event_type).
- Stato CH2 naked→UPDATE su `state/bridge_state.json` (persistente al restart).
- Validazione payload prima della scrittura JSON (action, direction, SL/TP, splits).
- Sessione Telegram condivisa: `C:\TelegramBridge\telegram_bridge_session.session`.

---

## Canali operativi

| ID | Nome | Telegram ID | Parser | Magic base |
|----|------|-------------|--------|------------|
| CH1 | Zanni VIP Signals | -1003026686847 | `zanni_vip` | 11000 |
| CH2 | Sala Gold VIP | -1003302540529 | `sala_gold` | 12000 |
| CH3 | Sala VIP | -1002890661441 | `sala_vip` | 13000 |
| CH4 | Sala Stark | -1002073368935 | `sala_stark` | 14000 |

---

## File sorgente (inventario `C:\TG_TradinGo`)

### Codice (versionare in git)

- `tradingo_bridge.py` — bridge principale, 4 parser, auto-restart
- `dump_channels.py` — debug: dump canali Telegram
- `sample_channels.py` — ultimi 50 messaggi per canale → `signal_samples.txt`
- `sample_last_24h.py` — ultimi N ore + dry-run parser + canali sconosciuti attivi
- `fetch_apr21.py` — estrazione messaggi per data
- `start_tradingo.bat` — avvio bridge
- `webapp/` — dashboard monitor mobile (FastAPI, porta 8600 via Tailscale) — vedi `docs/WEBAPP.md`
- `start_webapp.bat` — avvio webapp monitor
- `README.md` — guida operativa

### Config (NON versionare segreti)

- `tradingo_config.json` — `api_id`, `api_hash`, path, canali, `mt5_instances`
- `webapp_config.json` — utenti (hash), secret, check semafori (solo VPS)
- Template in repo: `tradingo_config.example.json`, `webapp_config.example.json`

### Runtime (NON versionare)

- `logs/tradingo_*.log` — log giornalieri bridge
- `signals/signal_ch*.json` — ultimo segnale scritto (stato live)

### Fixture in repo (per sviluppo parser)

- `docs/fixtures/signal_samples.txt` — ~50 msg/canale (apr 2026)
- `docs/fixtures/signals_apr21.txt` — messaggi 20-21 apr
- `docs/fixtures/channel_dump.txt` — dump canali completo

### EA MT5

- `mql5/TG_TradinGoEA.mq5` — EA in repo (v2.0); installare in `MQL5\Experts\` del terminale MT5.
- `docs/EA_SPEC.md` — contratto JSON bridge → EA.
- `docs/FRIEND_SETUP.md` — distribuzione EA su VPS amici.

---

## Azioni JSON per canale

### CH1 (`zanni_vip`)

- `OPEN` — BUY/SELL + TP1-3 + SL
- `CHECK_AND_BE` — TP1 hit / sposta SL a BE
- `CHECK_AND_CLOSE_TP` — chiudi TP N se non già chiuso
- `CLOSE_ALL_SYMBOL` — chiusura generica

### CH2 (`sala_gold`)

- `OPEN_NOW` — "Gold sell now" senza numeri (mercato)
- `UPDATE_OPEN` — messaggio successivo con SL/TP
- `OPEN` — segnale completo standalone
- `BREAK_EVEN_PRICE`, `CLOSE_ALL_SYMBOL`, `CLOSE_HALF_BE`
- Stato interno: `_ch2_pending_open` per naked→update

### CH3 (`sala_vip`)

- `OPEN` — "NUOVO ORDINE - XAUUSDpm Buy", lotto fisso
- `UPDATE_TP`, `UPDATE_SL` — "Modificato"
- `CHECK_AND_CLOSE` — "CHIUSO - SIMBOLO Buy"

### CH4 (`sala_stark`)

- `OPEN` — formato markdown ("Apro una nuova operazione") o forex piatto
- `is_add_signal` — "Aggiungo un'altra operazione" (lotto dimezzato)
- `inherit_from_first` — secondo trade senza SL/TP copia dal primo

---

## Regole per modifiche

1. **Non committare** `tradingo_config.json` con `api_hash` reale.
2. Dopo modifica parser, testare con messaggi in `docs/fixtures/signal_samples.txt`.
3. CH2 richiede test su **messaggi editati** (Telegram modifica il naked aggiungendo SL/TP).
4. Magic numbers: `magic_base + 1..3` per split trade.
5. Il bridge è indipendente da StatArb; non mescolare `params.json` StatArb con TradinGo.

---

## Comandi rapidi VPS

```bat
cd C:\TG_TradinGo
start_tradingo.bat
python sample_channels.py
python dump_channels.py
```

Deploy repo → produzione (backup automatico):

```powershell
powershell -ExecutionPolicy Bypass -File C:\StatArb\scripts\deploy_tg_tradingo_to_vps.ps1
```

---

## Numeri di produzione: usa il report, non il codice

I dati di una giornata (messaggi ricevuti, segnali emessi, trade eseguiti) **non**
si deducono dal codice né dai `signals/signal_ch*.json`, che contengono solo
l'ultimo segnale scritto. Sulla VPS del bridge:

```powershell
python C:\TG_TradinGo\tools\report_today.py --days 2 --out C:\agent_out\report.md
```

Legge i log del bridge e lo storico deal del terminale MT5 collegato, e mappa
`magic → canale` da `tradingo_config.json` (`magic_base + indice split`). Ogni
segnale compare una volta sola anche se scritto su più terminali.

## Relazione con StatArb

Nella stessa repo GitHub convivono:

- **`tg_tradingo/`** — copy trading da segnali Telegram
- **root `C:\StatArb`** — pairs trading stat-arb MT5 (moduli 0–8)

Sono sistemi separati; condividono solo VPS e repository git.
