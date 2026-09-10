# Versioni correnti e mappa preset → conto

Documento di riferimento rapido per chi riprende il lavoro (Cursor Cloud, agenti, deploy manuale).

## Branch canonico

Il branch di lavoro è **`main`**: contiene bridge, EA, preset, webapp, docs, test e script di deploy.

```bash
git fetch origin main && git checkout main && git pull
```

I branch `cursor/*` e `devin/*` sono di lavoro: allinearli a `main` prima di ripartire.

## Versioni

| Componente | Versione | Dove è dichiarata |
|---|---|---|
| Bridge Python | **2.25** | `BRIDGE_VERSION` in `tg_tradingo/tradingo_bridge.py` (banner di avvio e `start_tradingo.bat`) |
| EA MT5 | **2.25** | `#property version` + `#define EA_VERSION` in `tg_tradingo/mql5/TG_TradinGoEA.mq5` |
| EA MT4 | **1.12** | `#property version` + `#define EA_VERSION` in `tg_tradingo/mql4/TG_TradinGoEA.mq4` (stesso contratto JSON del bridge 2.24) |

Bridge ed EA MT5 si muovono insieme sul contratto JSON ([`EA_SPEC.md`](EA_SPEC.md)).
L’EA MT4 è un consumer aggiuntivo dello stesso JSON (nessun secondo parser).
Setup Contabo T4Trade + predisposizione path iFunds: [`MT4_T4TRADE_SETUP.md`](MT4_T4TRADE_SETUP.md).

**MT5 2.25 / MT4 1.12 (solo EA):** casi IVAN del 10/09. (1) Tolleranza off-market
`InpRangeTolerancePoints` da 150 a 200 punti (default e tutti i preset): il setup
`BUY 4344-4340` con prezzo a 4345,73 (173 punti oltre la zona) veniva annullato.
(2) Break-even: quando SL=entry non è ancora legale (prezzo entro lo stops level
dell'apertura reale del ticket) il comando non viene più scartato
(`BE_SKIPPED_WORSE_THAN_ENTRY`): il ticket resta in coda (`BE_PENDING`) e lo SL viene
portato al prezzo di apertura appena il mercato lo consente (`BE_PENDING_APPLIED`),
senza mai clampare peggio dell'entry. `InpBePendingRetry=false` ripristina lo scarto.

**2.25 (solo bridge):** IVAN, casi del 09/09. (1) "Chiduamo questa" due minuti dopo
"Rientriamo con piccola size" chiudeva tutto il simbolo (setup base incluso, che poi ha
preso TP3): ora, con un rientro emesso da meno di 30 minuti e senza "tutto", il
dimostrativo singolare produce `CLOSE_SELECTIVE keep=ALL_BUT_NEWEST` (solo il rientro).
(2) Zona con typo "BUY 4401-3399" (per 4401-4399) veniva scartata come non plausibile e il
setup non apriva: se un estremo è dentro la forchetta SL→TP e l'altro no, quello fuori
viene riallineato copiando le cifre iniziali dell'estremo buono (`RANGE_TYPO_FIXED`).

**2.24 / MT5 2.24 / MT4 1.11:** un campo nullo nel payload non viene più letto come
l'array successivo. Il lettore JSON dell'EA cercava la chiave e poi il primo `[` del file:
con `"entry_range": null` prendeva `tp_levels`, quindi su ogni setup a prezzo singolo
(IVAN) TP1/TP2 diventavano la zona d'ingresso e il segnale veniva annullato per distanza
(31/08: `SIGNAL_CANCELLED CH_IVAN ... range=[4422.00,4425.00]` su entry 4429). Corretto su
due fronti: il bridge non scrive più i campi nulli (`payload_for_ea`, effetto immediato
senza ricompilare) e `JsonGetNumberArray` (MT5/MT4) pretende che il valore della chiave sia
esso stesso un array.

**2.23 / MT5 2.23 / MT4 1.10:** lo SL che allontana lo stop si esegue, con un tetto.
Allargare lo stop è una scelta legittima del canale (dare respiro al prezzo), ma la size
è stata calcolata sul rischio precedente: il nuovo livello viene applicato fino a
`GOLD_MAX_SL_WIDEN_FACTOR` / `InpMaxSlWidenFactor` volte quel rischio (default **2.0**),
oltre viene **fissato** al tetto invece di essere scartato. Parser GOLD: riferimento
l'entry o il centro della zona (`SL 4700` su un SELL con zona 4639-4645 e SL 4656 →
`UPDATE_SL 4670`). EA MT5/MT4: riferimento il prezzo di apertura reale della posizione e
lo SL corrente, quindi vale per tutti i canali (log `UPDATE_SL_WIDEN_CAPPED`).
`InpMaxSlWidenFactor = 0` segue il canale senza limite.

**2.22 / MT5 2.22 / MT4 1.09:** quattro difetti visti sulla giornata del 24/08.

- GOLD: `Canceled` / `Annullato` / `Anulado` / `Annulé` entro 30 minuti dal setup emette
  `CLOSE_ALL_SYMBOL`. Il 24/08 il canale ha annullato 21 secondi dopo l'apertura, il
  messaggio è rimasto `UNPARSED` e il trade è morto sullo stop (-350 EUR). Se il testo
  nomina un livello (`Annulliamo il TP3`) non è un annullamento del trade; senza setup
  recente non fa nulla.
- GOLD: un `SL` standalone che **allontana** lo stop veniva rifiutato (`SL 4660` su un
  SELL con SL 4656), nel parser e nell'EA su `UPDATE_SL`. Sostituito nella 2.23 dal
  tetto: lo spostamento si esegue, limitato al doppio del rischio precedente.
- GOLD: i messaggi compositi non vengono più ignorati per intero — `SL hit | | Sell gold
  now` esegue l'ordine che contiene.
- EA MT5: le chiusure usano il magic della posizione (`ClosePositionKeepMagic` /
  `ClosePartialKeepMagic`). `CTrade` mantiene il magic dell'ultimo ordine inviato, quindi
  i deal di uscita ereditavano il magic dell'ultima apertura: il 24/08 le quattro uscite
  IVAN sono finite su magic 14101 (ORO) e ogni report per canale era falsato di 218 EUR.
  Le posizioni chiuse erano quelle giuste (`IsOurPosition` era corretto): è un difetto
  contabile, non operativo.

**2.21 / MT5 2.21 / MT4 1.08:** su FOREX (CH3) l'apertura la dichiara il messaggio
`NUOVO ORDINE / NEW ORDER`, anche quando dice `No SL / No TP`: il parser emette subito
`OPEN` naked (`sl=None`, `tp_levels=[]`, lotto singolo) e registra il trade in stato.
Prima il nuovo ordine restava solo `pending` e l'apertura veniva emessa dal `Modified`
successivo: il 20/08 AUDUSD, EURJPY e USDCAD sono nati da una modifica del TP e quindi
strutturalmente senza stop. Ora un `Modified` su un trade già noto muove solo i livelli
(`UPDATE_TP` / `UPDATE_SL` / `UPDATE_OPEN` per TP+SL insieme) e non può più aprire.
Se il nuovo ordine porta già i livelli (`SL: 1.15400`, `TP: 1.14000`) l'`OPEN` li include;
`SL: 0.00000` / `TP: 0.00000` valgono come assenti. Nessuna modifica a EA, JSON o architettura.

**2.20 / MT5 2.21 / MT4 1.08:** spostamento di un singolo take profit su IVAN, dal
messaggio del 17/08 `Spostiamo TP 4 a 4376` (era `UNPARSED`, il TP4 restava a 4375).
Bridge: il parser IVAN emette `UPDATE_TP` con `tp_index` per le forme con verbo
("spostiamo / portiamo / modifichiamo il TP n a X", `Sposto TP4 a 4376`, `Move TP 3 to
4383`) e senza indice quando il messaggio parla dei TP al plurale; il prezzo abbreviato
("TP 4 a 76") viene espanso sull'ultimo entry e un prezzo dal lato sbagliato rispetto
all'entry non viene emesso. EA MT5/MT4: `UPDATE_TP` con `tp_index` modifica **solo** la
posizione di quello split (`magic_base + tp_index`) invece di riscrivere il TP su tutte,
e passa dal filtro `InpProtectExistingLevels` (un TP avverso non chiude più la posizione
a mercato). Senza `tp_index` il comportamento resta quello di prima (ORO, FOREX).

**2.19 / MT5 2.20 / MT4 1.07:** flusso naked GOLD e break-even, dalla sequenza del 14/08
(naked alle 07:42, zona col typo `4342 - 4450` alle 07:45 rifiutata, edit corretto alle 10:06:
2 posizioni senza SL per 24 minuti, poi chiuse a -234 da un comando di break-even).
EA: `InpNakedFallbackSlPoints` (default 1200 pt = 12 $ sull'oro) mette subito uno SL
provvisorio sull'apertura `OPEN_NOW`, sostituito dai livelli veri all'`UPDATE_OPEN`
(log `NAKED_FALLBACK_SL`); `InpBeNeverWorseThanEntry` (default true) fa saltare il
break-even quando SL=entry non è applicabile, invece di "clampare" lo stop oltre l'entry
e liquidare la posizione (log `BE_SKIPPED_WORSE_THAN_ENTRY`). Bridge: un `UPDATE_OPEN`
con zona inutilizzabile ma SL/TP coerenti fra loro non viene più scartato in blocco —
la zona viene rimossa e il payload marcato `levels_only: true`, così l'EA applica SL/TP
alle posizioni aperte e non apre nulla se non ce ne sono. Parser GOLD: riconosciute le
forme con `NOW` prima dell'asset ("Go sell now gold !") e le locuzioni francesi
(`en vente`, `a vendre`, `en achat`); l'edit che aggiunge al naked solo il prezzo
("Go sell now gold ! 4357", "4259 sell gold now") non emette più un `UPDATE_OPEN` senza
livelli — il naked resta in attesa del setup completo.

**2.18 / MT5 2.19 / MT4 1.06:** parser GOLD — il canale a volte ripubblica lo stesso
setup tradotto ("Gold on sale now", "Oro a la venta ahora", "Precio actual del oro",
"COMPRAR/VENTA XAUUSD") o scrive `Typ:` per `Tp:`, e decora i livelli con emoji
("ZONA ➡️ 4425 - 4422"): tutte queste forme ora emettono il setup, con `entry_range`
popolato anche dietro le label `ZONE/ZONA/PRECIO ACTUAL`. Quando il testo tradotto perde
la direzione, questa si ricava dalla posizione di SL e TP rispetto alla zona. Parser IVAN —
un'entry incoerente ma con SL e TP coerenti fra loro (`XAUUSD SELL 4326` con SL 4436 e TP
4420→4408, entry vera ~4427) non fa più scartare il segnale: si apre a mercato
(`entry=null`, log `ENTRY_TYPO_MARKET`). Lato EA nuovo `InpProtectExistingLevels`
(default true): quando un setup nuovo arriva con posizioni aperte e `allow_stack=false`,
l'EA ne modifica SL/TP ma non applica più un TP dal lato in perdita rispetto al prezzo di
apertura della posizione (`MODIFY_SKIPPED_ADVERSE_TP`) né uno SL già superato dal mercato
(`MODIFY_SKIPPED_CROSSED_SL`), che il 12/08 avevano chiuso un BUY GOLD a -86,77.

**2.17 / MT5 2.18 / MT4 1.05:** parser IVAN — i desiderativi ("vorrei rientrare",
"volevo rientrare", "mi piacerebbe rientrare") non aprono più il rientro, e i consuntivi
con verbo di chiusura ("E anche oggi chiudiamo in Profitto", "chiudiamo la settimana")
non emettono più `CLOSE_ALL_SYMBOL` — i comandi espliciti (`CHIUDIAMO ORA`, `USCIAMO`,
`chiudiamo tutto`) restano validi. Lato EA nuovo `InpReentryMaxDriftPctOfSl` (default 40):
un rientro eredita lo SL del setup originale, quindi se il mercato si è già mangiato più
di quella quota della distanza entry→SL il rientro viene scartato
(`REENTRY_CANCELLED`, stat `CANCELLED_REENTRY_DRIFT`). 0 = guard disattivato.

**MT5 2.17:** `InpDdRecoveryLot` — sotto la soglia `InpDdBlockNewAtPct` l'EA non blocca
più tutte le aperture: se l'input è > 0 continua a eseguire i segnali a quel lotto
(0,01 sui preset iFunds) finché l'equity non risale sopra la soglia. Bridge ed EA MT4
invariati (il guard DD esiste solo su MT5).

**2.16 / MT4 1.04:** parser IVAN — gli annunci condizionali di rientro ("se ritraccia
rientriamo", "zona reentry 56-53") non aprono più, "chiudo la rientry" emette
`CLOSE_SELECTIVE keep=ALL_BUT_NEWEST` (chiude solo l'ultimo blocco), "spostiamo lo stop a X"
emette `UPDATE_SL`; lato EA nuovo guard `InpMaxLevelDeviationPct` (scarta i segnali con
livelli fuori scala rispetto al prezzo) e `InpBatchWindowSec`.

**2.15 / MT4 1.03:** `OPEN_NOW` apre subito `tp_levels_expected` ticket (GOLD=2×`fixed_lot_per_tp`); `UPDATE_OPEN` fa fill per-magic senza close/reopen.

Contenuto della 2.14 rispetto alla 2.10 del bridge: parser post-audit (PR #21-#28) —
forme italiane GOLD/FOREX, dedup MSG+EDIT, chiusure selettive `CLOSE_SELECTIVE`,
rientri IVAN con filtro anti-frase-di-attesa — e lato EA guard DD statico su equity,
kill switch manuale, sizing dal serbatoio, cap sui lotti concorrenti, cooldown killswitch a 0.

## Preset → conto

Tutti in `tg_tradingo/mql5/presets/`, si caricano da `Inputs → Load` sul chart dell'EA.

| Preset | Conto | Guard DD | Sizing serbatoio | Canali | Note |
|---|---|---|---|---|---|
| `TG_TradinGo_Vantage_Demo.set` | Vantage demo (Contabo) | off | off | tutti e 5 | conto "misura i canali": nessun guard altera i risultati |
| `TG_TradinGo_Ultima_iFunds_Demo.set` | Ultima demo (Gamehosting) | 6%, start 10.000 → floor 9.400 | on, cap 0,05 | ivan, stark | prova delle regole iFunds; `InpMagicOffset=0` per non perdere le posizioni già aperte |
| `TG_TradinGo_iFunds_10k_dd6.set` | iFunds 10k reale | 6%, start 10.000 → floor 9.400 | on, cap 0,05 | ivan, stark | `InpMagicOffset=500000` |
| `TG_TradinGo_iFunds_50k_dd6.set` | iFunds 50k reale | 6%, start 50.000 → floor 47.000 | on, cap 0,25 | ivan, stark | scalata dopo il 10k |
| `TG_TradinGo_Reale_Personale.set` | conti reali personali | 10%, equity catturata al primo attach | off | ivan, stark (da scegliere) | template: lotti, suffisso simbolo e % vanno adattati al broker |
| `TG_TradinGo_Moneta_10k.set` | Moneta Funded 10k | off | off | ivan, stark | storico: piano abbandonato, lotti fissi 0,02/0,01 |
| `TG_TradinGo_Gamehosting_Demo.set` | demo su VPS amico | off | off | tutti e 5 | usa la junction `MQL5\Files\tradingo` |

Preset MT4 in `tg_tradingo/mql4/presets/`:

| Preset | Conto | Canali | Note |
|---|---|---|---|
| `TG_TradinGo_T4Trade_Reale.set` | T4Trade reale (Contabo) | tutti e 5 (editabili) | lotti 0.01; path `MQL4\Files\tradingo\` |

Dettaglio delle regole iFunds e procedure di test dei guard: [`IFUNDS_SETUP.md`](IFUNDS_SETUP.md).

## Cosa gira in produzione

| Macchina | Componente | Preset |
|---|---|---|
| Contabo `144.91.76.28` | bridge Python + terminale Vantage demo | `TG_TradinGo_Vantage_Demo.set` |
| Gamehosting `100.74.9.8` | terminale Ultima demo | `TG_TradinGo_Ultima_iFunds_Demo.set` |

Il deploy non è automatico: `C:\StatArb\scripts\deploy_tg_tradingo_to_vps.ps1` per il bridge,
copia manuale del `.mq5` + compilazione in MetaEditor per l'EA.
