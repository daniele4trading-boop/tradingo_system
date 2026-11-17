# Canale Sertorio: analisi → zone → setup condizionali (shadow)

Nome dedicato (non è Ivan): canale `CH_SERTORIO`, parser `sertorio_zones`, tag `SZ`, magic base proprio.

```
analisi Telegram (canale pubblico)
  → bridge: update_levels_safe (livelli MT5 desktop, invariato)
  → bridge: sertorio_analyze → sertorio_zones.handle_analysis
       interprete LLM (claude, max_tokens 1500) + fallback parser regex
       → Analysis {zones, ideas, bias, key_level, current_price}
       → build_setups → state/sertorio_zones.json   (nessun signal_*.json: nessun ordine)
  → tools/sertorio_shadow.py (processo separato, feed MT5 sola lettura)
       tocco zona → fill virtuale, una gamba per TP, SL, BE delle altre dopo TP1, scadenza 21 UTC
       → state/sertorio_shadow.json + journal/sertorio_shadow.csv
  → webapp: sezione "Zone Sertorio (shadow)" (zone, bias, livello chiave, setup, esiti 7g/30g)
```

- `setups.use_ideas` (default on): le idee LONG/SHORT esplicite dell'analista (entry/SL/TP).
- `setups.derive_from_zones` (default off): BUY su supporto / SELL su resistenza con SL = zona ± `sl_buffer`, TP = zone successive.
- Una nuova analisi annulla i setup pendenti precedenti; le gambe virtuali aperte restano.
- `setups.shadow=false` + `setups.live_instances` (es. `["Vantage"]`): `tools/sertorio_shadow.py` al tocco
  di un setup scrive un `OPEN` (`entry_range` = zona, SL, `tp_levels`, `magic_base`, `signal_id` `SZ-<setup>`)
  **solo** nei `signals_path` delle `mt5_instances` elencate; gli altri terminali non ricevono nulla.
  L'EA (2.36) apre con `InpLotSertorio` per gamba, commenti `SZ-Tn-...`; il preset deve avere `sertorio` in
  `InpChannels`. Il libro virtuale continua in parallelo (confronto reale vs virtuale in webapp/journal).
  Il bridge non emette mai ordini Sertorio: l'unico scrittore è il monitor.

Replay storico: `python tools/sertorio_replay.py --analyses <json interpretate> --m1 <xau_m1.csv>`.
Avvio monitor su GH: `python tools/sertorio_shadow.py --config C:\TG_TradinGo\tradingo_config.json --terminal "<terminal64.exe Vantage>"`.
