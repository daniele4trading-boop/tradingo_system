# TG TradinGo EA — specifica bridge → MT5

Documento di riferimento per `TG_TradinGoEA.mq5`. Il bridge Python **non** parla con MT5: scrive solo file JSON. L'EA è l'unico componente che esegue ordini.

## Architettura multi-VPS (tu + amici)

```
[La tua VPS]  tradingo_bridge.py  →  signal_ch_*.json
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
            C:\TG_TradinGo\signals    Amico A MQL5\Files    Amico B (share SMB)
                    │                         │                         │
                    ▼                         ▼                         ▼
              TG_TradinGoEA              TG_TradinGoEA              TG_TradinGoEA
              (tuo MT5 demo)             (suo MT5)                  (suo MT5)
```

**Due modi per gli amici:**

1. **Bridge centrale (consigliato):** nel tuo `tradingo_config.json` aggiungi il path `signals_path` della cartella `MQL5\Files` di ogni amico (share di rete o sync). Il bridge scrive gli stessi JSON ovunque.
2. **Solo EA:** l'amico copia/sincronizza i file `signal_ch_*.json` nella propria cartella segnali e avvia l'EA con lo stesso `SignalsBasePath`.

L'EA non richiede Telegram né Python: legge solo i JSON.

---

## File segnale (uno per canale)

| File | Canale | magic_base |
|------|--------|------------|
| `signal_ch_gold.json` | Sala GOLD VIP | 12000 |
| `signal_ch_forex.json` | Sala FOREX VIP | 13000 |
| `signal_ch_oro.json` | Sala ORO VIP | 14100 |
| `signal_ch_stark.json` | Sala Stark | 14000 |
| `signal_ch_ivan.json` | IvanTrades VIP | 17000 |
| `signal_ch_ivanbtc.json` | IvanTrades - BTC (BTCUSD/XAGUSD) | 18000 |

Stato iniziale / idle: `{"action": "NONE"}`.

Il bridge sovrascrive l'intero file ad ogni evento (scrittura atomica tmp + replace). L'EA deduplica con `timestamp` in memoria e, se `InpClearSignalAfterProcess=true` (default), riscrive il file a `{"action":"NONE"}` dopo ogni segnale gestito (esecuzione, scarto range o open fallito) per evitare replay al riavvio.

**v2.07:** con `InpIgnoreExistingOnInit=true` (default), all'attach l'EA **non esegue** i JSON già presenti: marca i `timestamp` come visti e li azzera a `NONE`. Solo segnali nuovi (timestamp diverso) dopo l'init vengono eseguiti.

---

## Campi JSON comuni

| Campo | Tipo | Note |
|-------|------|------|
| `action` | string | Vedi tabella azioni |
| `direction` | `"BUY"` \| `"SELL"` | Obbligatorio per OPEN* |
| `symbol` | string | Es. `XAUUSD`, `EURUSD` |
| `entry` | number \| null | Prezzo singolo; **null** se c'è `entry_range` |
| `entry_range` | `[lo, hi]` \| null | EA valuta prezzo nel range (non usa media) |
| `sl` | number \| null | Stop loss |
| `tp_levels` | number[] | Un TP per trade split |
| `trades` | int | Numero posizioni da aprire |
| `fixed_lot` | number | Lotto per trade (se `use_fixed_lot`) |
| `use_fixed_lot` | bool | Sempre `true` con bridge attuale |
| `splits` | number[] | Legacy; bridge usa `fixed_lot` |
| `magic_base` | int | Magic trade i = `magic_base + i` |
| `channel_id` | string | Es. `CH_GOLD` |
| `timestamp` | string ISO UTC | Dedup EA |
| `message_id` | int | Opzionale, da Telegram |
| `event_type` | `"NEW"` \| `"EDIT"` | Opzionale |
| `is_add_signal` | bool | STARK: lotto dimezzato |
| `inherit_from_first` | bool | STARK: copia SL/TP dal primo trade |
| `new_tp` / `new_sl` | number | UPDATE_TP / UPDATE_SL |
| `is_be` | bool | UPDATE_SL verso break-even |
| `be_price` | number | BREAK_EVEN_PRICE |
| `tp_index` | int | CHECK_AND_CLOSE_TP; UPDATE_TP (solo lo split `magic_base + tp_index`) |
| `levels_only` | bool | UPDATE_OPEN con zona inutilizzabile: applica SL/TP alle posizioni aperte, non aprire |

### Regole lotti (bridge `apply_lot_rules`)

| TP nel segnale | `trades` | `fixed_lot` |
|----------------|----------|-------------|
| 0 (OPEN_NOW) | 1 | 0.20 |
| 1 TP | 1 | 0.20 |
| 2+ TP | N | 0.10 ciascuno |

`OPEN_NOW` (GOLD naked): mercato subito, senza SL/TP. Il messaggio successivo è `UPDATE_OPEN` con livelli.

---

## Azioni EA

| action | Comportamento EA |
|--------|------------------|
| `NONE` | Ignora |
| `OPEN` | Apri `trades` posizioni (magic_base+1..N), SL/TP da JSON |
| `OPEN_NOW` | Apri `trades` ordini a mercato **senza** SL/TP. Da bridge 2.15: per canali con `tp_levels_expected>=2` (GOLD) `trades`/`fixed_lot` sono già quelli multi-TP (`N × fixed_lot_per_tp`), così all’UPDATE_OPEN successivo non serve chiudere/riaprire |
| `UPDATE_OPEN` | Per ogni slot magic `base+1..N`: se la posizione esiste → modifica SL/TP; se manca → apre solo quella (`EXECUTED_UPDATE_FILL`). Posizioni extra oltre N (rientri) ricevono solo il nuovo SL, TP invariato. **Non** chiude e riapre più il set |
| `UPDATE_TP` | Modifica TP posizioni del canale/simbolo; con `tp_index` solo la posizione di quello split (IVAN: "spostiamo TP 4 a 4376"). Un TP dal lato sbagliato rispetto all'entry viene ignorato se `InpProtectExistingLevels` |
| `UPDATE_SL` | Modifica SL (FOREX, ORO, GOLD, IVAN: "spostiamo lo stop a 4255") |
| `CHECK_AND_CLOSE` | Chiudi se esiste posizione symbol+direction |
| `CLOSE_ALL_SYMBOL` | Chiudi tutte le posizioni del simbolo (magic del canale) |
| `CLOSE_SELECTIVE` | Chiusura **parziale selettiva**: campo `keep` = `BEST` / `HIGHEST` / `LOWEST` / `ALL_BUT_NEWEST`. Restano aperte solo le posizioni con quel prezzo di apertura (per direzione, tolleranza 10 punti), le altre si chiudono. `BEST` = migliori per la direzione (SELL: prezzo più alto, BUY: più basso). `ALL_BUT_NEWEST` ("chiudo la rientry") = chiude solo l'ultimo blocco aperto — le posizioni aperte entro `InpBatchWindowSec` dalla più recente — e lascia il setup principale; se tutte le posizioni sono nello stesso blocco non fa nulla |
| `BREAK_EVEN_PRICE` | Sposta SL a `be_price` |
| `CLOSE_HALF_BE` | Chiudi metà volume + BE sul resto (ORO: `60 PIPS CLOSE OR BREKIVEN`) |
| `CHECK_AND_BE` | Se TP1 non chiuso, sposta SL a entry. Opzionale `be_price` (IVAN: entry pubblicato dal setup): l'EA lo usa se legale ed entro `InpBeSignalEntryMaxGapPoints` dal fill, altrimenti SL al fill |
| `CHECK_AND_CLOSE_TP` | Chiudi trade con `tp_index` se ancora aperto |

### Entry range

Se `entry_range` è `[lo, hi]` il parser **non** calcola la media in `entry` (resta `null`).

L'EA valuta **subito** al ricevimento del segnale:

| Condizione | Azione |
|------------|--------|
| Prezzo ∈ [lo, hi] | Esegue a mercato (`ENTRY_IN_RANGE`) |
| Prezzo fuori range ma distanza ≤ `InpRangeTolerancePoints` (default **150** ≈ 15 pip su XAUUSD) | Esegue comunque (`ENTRY_TOLERANCE`) |
| Prezzo troppo lontano | **Non esegue** — log `SIGNAL_CANCELLED` + riga in `MQL5\Files\tradingo_signal_stats.csv` |

`InpRangeTolerancePoints` è in **punti MT5** del simbolo (`_Point`). Per XAUUSD 2 decimali: 100 punti ≈ 1.0$, 200 punti ≈ 2.0$.

Non c'è più attesa passiva del prezzo nel range.

### STARK `is_add_signal` / `inherit_from_first`

- `is_add_signal=true` → lotto × 0.5 (`InpAddLotFactor`, default 0.5)
- `inherit_from_first=true` → copia SL e `tp_levels` dalla prima posizione aperta dello stesso canale/simbolo

### Break-even automatico

Se `InpAutoBreakEvenOnTp1=true`, quando una posizione con magic `magic_base+1` chiude in profitto (TP1), sposta SL delle altre posizioni dello stesso segnale a prezzo di apertura.

---

## Parametri EA consigliati

| Parametro | Default | Uso |
|-----------|---------|-----|
| `InpSignalsPath` | `C:\TG_TradinGo\signals\` | Cartella JSON |
| `InpUseAbsolutePath` | true | false = sotto `MQL5\Files\` |
| `InpSymbolSuffix` | `""` | Suffisso broker (`m`, `.pro`) |
| `InpLotMultiplier` | 1.0 | Scala lotti per amici |
| `InpMaxSlippagePoints` | 50 | Slippage |
| `InpPollMs` | 500 | Intervallo lettura file |
| `InpRangeTolerancePoints` | 150 | Max distanza (punti) dal range per entrare comunque |
| `InpOroRangeTolerancePoints` | 250 | Tolleranza dedicata CH_ORO (`0` = usa il default globale) |
| `InpLogCancelledSignals` | true | Scrive `tradingo_signal_stats.csv` (esecuzioni + cancellazioni) |
| `InpClearSignalAfterProcess` | true | Dopo ogni segnale gestito, riscrive il JSON a `NONE` |
| `InpIgnoreExistingOnInit` | true | All'attach salta+azzera JSON già presenti (anti-replay) |
| `InpStackOpensIfFlatBusy` | true | Se true, onora `allow_stack` nel JSON; senza flag → modifica SL/TP |
| `InpStopBufferPoints` | 20 | Buffer extra oltre stops/freeze level (anti 10016 su BE) |
| `InpNakedFallbackSlPoints` | 1200 | SL provvisorio sull'`OPEN_NOW` naked (12 $ sull'oro); `0`=apri senza SL |
| `InpBeNeverWorseThanEntry` | true | Salta il BE se SL=entry non è applicabile, invece di clampare oltre l'entry |
| `InpMaxFloatingLossUSD` | 150 | Kill-switch floating (valuta **conto**); `0`=off |
| `InpKillSwitchCooldownMin` | 60 | Cooldown OPEN dopo kill-switch |
| `InpMaxHoldingMinutes` | 0 | Max holding; `0`=off (suggerito 90) |
| `InpHeartbeatMaxAgeSec` | 180 | Blocca OPEN se heartbeat stale; `0`=off |
| `InpHeartbeatFile` | `tradingo_heartbeat.json` | Heartbeat bridge |
| `InpEquitySampleSec` | 60 | Campione equity; `0`=off |
| `InpJournalPrefix` | `journal\` | Sotto `MQL5\Files` |
| `InpSingleInstanceLock` | true | Una sola istanza EA |
| `InpLockStaleSec` | 120 | Scadenza lock post-crash |
| `InpChannels` | `gold,forex,oro,stark,ivan` | File da monitorare |

**v2.06+:** prima di `OrderSend`, normalizza SL/TP a `SYMBOL_TRADE_STOPS_LEVEL`; se SL/TP restano dal lato sbagliato del prezzo (segnale stale), salta l'open.  
**v2.07:** `InpIgnoreExistingOnInit` evita di rieseguire OPEN_NOW/OPEN vecchi al riattach EA.  
**v2.08:** BE clamp + `InpStackOpensIfFlatBusy` (stackava anche MSG+EDIT — troppo aggressivo).  
**v2.09:** stack solo se JSON `allow_stack:true`; `InpStopBufferPoints` + retry BE su 10016.  
**v2.10:** kill-switch floating/time, single-instance lock, heartbeat gate, trade journal + MAE/MFE, equity, market context, `signal_id` in commento.  
**v2.11:** lotti/tag per canale (`InpLotIvan`/`InpLotStark`, `InpTagIvan=IT`, `InpTagStark=AS`); preset Moneta `mql5/presets/TG_TradinGo_Moneta_10k.set` — vedi [`MONETA_FUNDED_SETUP.md`](MONETA_FUNDED_SETUP.md).

**v2.20 / MT4 1.07:** `InpNakedFallbackSlPoints` protegge subito il naked open (`NAKED_FALLBACK_SL`), `InpBeNeverWorseThanEntry` evita che un break-even non applicabile diventi uno stop avverso (`BE_SKIPPED_WORSE_THAN_ENTRY`), `levels_only` fa modificare senza aprire.

`close_reason`: `TP` · `SL` · `BE_SL` · `CLOSE_ALL_SIGNAL` · `CLOSE_HALF` · `KILLSWITCH_FLOATING` · `KILLSWITCH_TIME` · `MANUAL` · `UNKNOWN` — vedi [`JOURNAL_SPEC.md`](JOURNAL_SPEC.md).

### Commento ordini MT5

Ogni trade aperto dall'EA ha commento:

```
TG-GOLD-T1              (canale GOLD, TP 1)
TG-IVAN-T3-a1b2c3d4e5   (Ivan TP3 + signal_id, max 31 char)
TG-STARK-T1
```

Il tag deriva da `channel_id` nel JSON (`CH_GOLD` → `GOLD`). Visibile in tab Trade / History.

### Statistiche esecuzione (`tradingo_signal_stats.csv`)

In `MQL5\Files\`, una riga per ogni trade aperto o segnale scartato:

| status | Significato |
|--------|-------------|
| `EXECUTED_IN_RANGE` | Prezzo dentro `entry_range` |
| `EXECUTED_TOLERANCE` | Fuori range ma dentro tolleranza |
| `EXECUTED_DIRECT` | OPEN senza range (entry singolo) |
| `EXECUTED_OPEN_NOW` | GOLD naked |
| `CANCELLED_RANGE` | Prezzo troppo lontano, trade non aperto |

Colonne: `signal_entry`, `fill_price`, `slippage_points`, `distance_points`, `channel_id`, `tp_index`, `magic`.

Analisi da VPS:

```bat
cd C:\StatArb\tg_tradingo
python analyze_signal_stats.py
```

---

## Recupero EA vecchio sulla VPS

```powershell
powershell -ExecutionPolicy Bypass -File C:\StatArb\scripts\find_tradingo_ea.ps1
```

Confronta l'output con `tg_tradingo/mql5/TG_TradinGoEA.mq5` in repo. **Non** distribuire l'EA vecchio se non documentato: usare la versione in repo.

---

## Prompt per Claude (se vuoi rigenerare)

Usa questo blocco come system/task prompt; allega `EA_SPEC.md` e i fixture in `docs/fixtures/signal_ch*.example.json`.

```
Scrivi TG_TradinGoEA.mq5 per MetaTrader 5 che:
1. Legge periodicamente signal_ch_{gold,forex,oro,stark}.json da InpSignalsPath
2. Deduplica con campo timestamp
3. Implementa tutte le action in EA_SPEC.md
4. Usa magic_base+i per trade split, fixed_lot × InpLotMultiplier
5. OPEN_NOW = mercato; UPDATE_OPEN aggiorna SL/TP e splitta se trades>1
6. entry_range = attendi prezzo nel range prima di OPEN
7. Un solo file .mq5, include Trade.mqh, niente DLL esterne
8. Log su Experts con prefisso [TradinGo]
```

La versione in `tg_tradingo/mql5/TG_TradinGoEA.mq5` è già allineata a questa specifica.
