# Indicatore livelli dalle analisi (TG_TradinGoLevels)

Le analisi dei canali (Hybrid Setup Gold/Forex, Matteo Sertorio) **non generano ordini**:
i livelli estratti (supporti, resistenze, liquidità) finiscono in un file CSV che l'indicatore
`mql5/TG_TradinGoLevels.mq5` disegna sul grafico. L'EA operativo non legge questo file.

## File

`MQL5\Files\tradingo\tradingo_levels.csv` (stessa cartella dei segnali), scritto in modo atomico
da `levels_store.write_levels_file()`:

```
symbol,source,kind,price,price_to,valid_until_utc,label
XAUUSD,HYBRID_GOLD,support,3990,,2026.10.13 20:00,minimo settimanale
XAUUSD,HYBRID_GOLD,liquidity,3980.5,3985,2026.10.13 20:00,equal lows
```

- `kind`: `support` (linea piena), `resistance` (tratteggio), `liquidity` (punti). Con `price_to` diventa una zona (rettangolo).
- `source`: colore per canale, input dell'indicatore (Hybrid Gold oro, Hybrid Forex azzurro, Sertorio viola, altri grigio). Legenda nell'angolo scelto.
- `symbol` senza suffisso: `XAUUSD` vale anche su `XAUUSD.ago`, `XAUUSD+`, ...
- Righe scadute (`valid_until_utc` in UTC) ignorate. Una nuova analisi di un canale per un simbolo sostituisce i livelli precedenti dello stesso canale e simbolo (`merge_levels`).

L'indicatore ricarica il file quando cambia (controllo ogni `InpRefreshSec`).

## Estrazione (levels_extract.py)

Il bridge chiama `update_levels_safe()` per ogni messaggio (anche modificato) dei canali con
sorgente livelli; un errore qui non blocca mai i segnali. Attivo con `"levels": {"enabled": true,
"output_files": [...]}` in `tradingo_config.json`; di default il file va in
`Terminal\Common\Files\tradingo\tradingo_levels.csv`, letto da tutti i terminali
(`InpCommonFolder=true`).

| Canale | Cosa si estrae | Validità |
|---|---|---|
| Matteo Sertorio (`CH_SERTORIO`, parser `placeholder`) | sezioni 🔴 Resistenze / 🟢 Supporti, `livello chiave` (`key`), scenari con SL → `watch_buy`/`watch_sell` con etichetta `Short 55% SL 4198 TP 4170/4160` | 36 h o prossima analisi |
| Hybrid Gold/Forex | `livello BUY 4.138`, `SHORT 4.187–4.191`, `GBPJPY — BUY 210.700`, `XAUUSD: 4.187–4.191` (`watch`) | fine sessione (21:00 UTC); spariscono all'arrivo del segnale sul simbolo o con "non abbiamo livelli attivi" |

Prezzi oro col punto delle migliaia (`4.138` = 4138); valori fuori range o lontani >8% dalla
mediana dell'analisi scartati (date, percentuali, pips).
