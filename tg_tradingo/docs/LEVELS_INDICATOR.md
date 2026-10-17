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
