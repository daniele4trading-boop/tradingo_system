# H4 S0 schema

`static` identifica l'evento e il livello; `t_close` usa esclusivamente
informazioni disponibili al close della barra evento; `ex_post` usa dati della
barra successiva o dell'orizzonte forward. Le percentuali NaN sono quelle
osservate nel dataset finale di 6.582 eventi.

Il calendario contiene soltanto NFP verificati. FOMC è omesso perché il
parsing storico non era sufficientemente affidabile; CPI è omesso perché BLS
ha risposto HTTP 403.

Range semantics: le chiavi di raggruppamento settimanali e mensili usano
`ts + 3h`, così la barra domenicale 21:00 UTC appartiene alla settimana/mese
successivo. `day_of_month`, `weekday` e `week_of_month` usano `ts` non
traslato.

Le feature `dxy_chg_k` e `ust_chg_k` usano k barre della griglia H4 del
simbolo dopo allineamento as-of backward. `corr20_*` include il rendimento
della barra `t`.

## Colonne

| Colonna | Disponibilità | Unità | NaN % | Definizione e regola NaN |
|---|---|---|---:|---|
| `symbol` | static | stringa | 0.000 | Strumento dell'evento; mai NaN. |
| `bar_ts_utc` | static | timestamp UTC | 0.000 | Inizio della barra H4 evento; mai NaN. |
| `event_ts_utc` | static | timestamp UTC | 0.000 | Timestamp causale dell'evento; mai NaN. |
| `dir` | static | `high`/`low` | 0.000 | Direzione dello sweep; mai NaN. |
| `level_type` | static | categoria | 0.000 | `equal_*` o `swing_*`; mai NaN. |
| `level_price` | static | prezzo strumento | 0.000 | Prezzo del livello swept; mai NaN. |
| `extreme_price` | static | prezzo strumento | 0.000 | Estremo della barra evento usato per la penetrazione; mai NaN. |
| `penetration_atr` | t_close | ATR | 0.000 | Penetrazione normalizzata per ATR14; mai NaN negli eventi. |
| `below_min_penetration` | t_close | booleano | 0.000 | True se penetrazione < 0.10 ATR; gli eventi finali sono filtrati. |
| `swing_n` | static | barre | 0.000 | Ampiezza dello swing, 5/10/20; mai NaN. |
| `event_id` | static | stringa | 0.000 | Identificatore deterministico dell'evento; mai NaN. |
| `bar_idx` | static | indice | 0.000 | Indice della barra H4 nel simbolo; mai NaN. |
| `level_form_idx` | static | indice | 0.000 | Indice di formazione del livello; mai NaN. |
| `level_confirm_idx` | static | indice | 0.000 | Indice di conferma del livello; mai NaN. |
| `trade_sign` | static | `+1`/`-1` | 0.000 | Segno della continuazione, usato per rendimenti direzionali. |
| `wick_ratio` | t_close | rapporto | 0.000 | Wick nella direzione dello sweep diviso range H4; mai NaN. |
| `dist_from_level_atr` | t_close | ATR | 0.000 | Distanza del close dal livello normalizzata per ATR14; mai NaN. |
| `liquidity_density` | t_close | livelli/barra | 0.000 | Livelli già confermati entro la distanza ATR; nessun livello futuro. |
| `level_age_bars` | t_close | barre | 0.000 | Età del livello al momento dell'evento; mai NaN. |
| `level_touch_count` | t_close | conteggio | 0.000 | Touch del livello disponibili causalmente; mai NaN. |
| `bar_range_atr` | t_close | ATR | 0.000 | Range high-low della barra / ATR14; mai NaN. |
| `body_ratio` | t_close | rapporto | 0.000 | Corpo assoluto / range H4; mai NaN. |
| `atr_regime` | t_close | rapporto | 0.000 | ATR14 rapportato alla scala ATR precedente configurata; mai NaN. |
| `volatility_regime` | t_close | percentile [0,1] | 0.258 | Frazione degli ATR precedenti inferiori all'ATR corrente in una finestra massima di 250 barre; NaN con meno di 100 ATR precedenti. |
| `vol_vs_median` | t_close | rapporto | 0.000 | Volume barra / mediana rolling causale; mai NaN dopo pulizia M1. |
| `efficiency_ratio` | t_close | rapporto [0,1] | 0.000 | Movimento netto / somma dei movimenti assoluti nella finestra causale. |
| `ret_5_bp` | t_close | basis point | 0.000 | Rendimento close su 5 barre H4; NaN solo se manca storia. |
| `ret_20_bp` | t_close | basis point | 0.000 | Rendimento close su 20 barre H4; NaN solo se manca storia. |
| `pos_in_week_range` | t_close | rapporto [0,1] | 0.000 | Posizione del close nel range su chiavi `ts+3h`; mai NaN negli eventi. |
| `pos_in_month_range` | t_close | rapporto [0,1] | 0.000 | Posizione del close nel range mensile su chiavi `ts+3h`; mai NaN. |
| `dist_52w_high_atr` | t_close | ATR | 3.859 | Distanza dal massimo rolling di 52 settimane / ATR; NaN con storia insufficiente. |
| `dist_52w_low_atr` | t_close | ATR | 3.859 | Distanza dal minimo rolling di 52 settimane / ATR; NaN con storia insufficiente. |
| `day_of_month` | t_close | intero | 0.000 | Giorno del mese di `ts` non traslato; mai NaN. |
| `week_of_month` | t_close | intero | 0.000 | Settimana del mese di `ts` non traslato; mai NaN. |
| `weekday` | t_close | 0–6 | 0.000 | Giorno settimana di `ts` non traslato; mai NaN. |
| `bar_slot` | t_close | slot H4 | 0.000 | Slot della barra nella giornata UTC; mai NaN. |
| `year` | t_close | anno | 0.000 | Anno UTC di `ts`; mai NaN. |
| `month` | t_close | mese | 0.000 | Mese UTC di `ts`; mai NaN. |
| `dxy_level` | t_close | livello indice | 69.553 | Ultimo livello DXY disponibile as-of backward; NaN prima dell'inizio DXY locale. |
| `dxy_chg_5_bp` | t_close | basis point | 70.100 | Variazione DXY su 5 barre della griglia del simbolo; NaN senza lag disponibile. |
| `dxy_chg_20_bp` | t_close | basis point | 70.100 | Variazione DXY su 20 barre della griglia del simbolo; NaN senza lag disponibile. |
| `ust_level` | t_close | livello proxy | 69.204 | Ultimo livello UST disponibile as-of backward; NaN prima dell'inizio UST locale. |
| `ust_chg_5_bp` | t_close | basis point | 69.386 | Variazione UST su 5 barre della griglia del simbolo; NaN senza lag disponibile. |
| `ust_chg_20_bp` | t_close | basis point | 69.386 | Variazione UST su 20 barre della griglia del simbolo; NaN senza lag disponibile. |
| `corr20_XAGUSD` | t_close | correlazione [-1,1] | 74.248 | Correlazione rolling a 20 rendimenti, incluso `t`; NaN senza 20 osservazioni comuni. |
| `corr20_EURUSD` | t_close | correlazione [-1,1] | 22.136 | Correlazione rolling a 20 rendimenti verso EURUSD; NaN senza finestra comune. |
| `corr20_GBPUSD` | t_close | correlazione [-1,1] | 23.428 | Correlazione rolling a 20 rendimenti verso GBPUSD; NaN senza finestra comune. |
| `corr20_USDJPY` | t_close | correlazione [-1,1] | 21.088 | Correlazione rolling a 20 rendimenti verso USDJPY; NaN senza finestra comune. |
| `corr20_SPX500` | t_close | correlazione [-1,1] | 36.858 | Correlazione rolling a 20 rendimenti verso SPX500; NaN senza finestra comune. |
| `corr20_XAUUSD` | t_close | correlazione [-1,1] | 37.238 | Correlazione rolling a 20 rendimenti verso XAUUSD; NaN senza finestra comune. |
| `macro_high_impact_in_bar` | t_close | booleano | 0.000 | True se un NFP verificato cade nell'intervallo half-open della barra H4; mai NaN. |
| `entry_bar_close` | t_close | prezzo strumento | 0.000 | Close della barra evento usato come riferimento; mai NaN. |
| `entry_next_open` | ex_post | prezzo strumento | 0.000 | Open della barra successiva disponibile, entry primaria; mai NaN. |
| `exec_cost_bp` | ex_post | basis point | 0.000 | Costo stimato dell'esecuzione entry; mai NaN. |
| `next_bar_gap_h` | ex_post | ore | 0.000 | Ore tra barra successiva e continuazione H4 attesa; i gap restano visibili. |
| `n_m1_bar` | t_close | conteggio | 0.000 | Numero di minuti M1 nella barra H4; barre con meno di 30 minuti restano. |
| `spread_bp_entry` | ex_post | basis point | 0.000 | Spread usato all'entry; mai NaN negli eventi finali. |
| `cost_rt_bp` | ex_post | basis point | 0.000 | Costo round-trip stimato; mai NaN. |
| `fwd_ret_1_bp` | ex_post | basis point | 0.000 | Rendimento forward a 1 barra; NaN solo per orizzonte non disponibile. |
| `fwd_ret_1_dir_bp` | ex_post | basis point | 0.000 | Forward a 1 barra moltiplicato per `trade_sign`; NaN con outcome mancante. |
| `fwd_ret_1_ex_bp` | ex_post | basis point | 0.000 | Forward a 1 barra dopo costo; NaN con outcome mancante. |
| `fwd_ret_2_bp` | ex_post | basis point | 0.000 | Rendimento forward a 2 barre; NaN solo per orizzonte non disponibile. |
| `fwd_ret_2_dir_bp` | ex_post | basis point | 0.000 | Forward a 2 barre direzionale; NaN con outcome mancante. |
| `fwd_ret_2_ex_bp` | ex_post | basis point | 0.000 | Forward a 2 barre dopo costo; NaN con outcome mancante. |
| `fwd_ret_4_bp` | ex_post | basis point | 0.000 | Rendimento forward a 4 barre; NaN solo per orizzonte non disponibile. |
| `fwd_ret_4_dir_bp` | ex_post | basis point | 0.000 | Forward a 4 barre direzionale; NaN con outcome mancante. |
| `fwd_ret_4_ex_bp` | ex_post | basis point | 0.000 | Forward a 4 barre dopo costo; NaN con outcome mancante. |
| `fwd_ret_12_bp` | ex_post | basis point | 0.061 | Rendimento forward a 12 barre; NaN se l'orizzonte termina nel dataset. |
| `fwd_ret_12_dir_bp` | ex_post | basis point | 0.061 | Forward a 12 barre direzionale; NaN se l'orizzonte termina. |
| `fwd_ret_12_ex_bp` | ex_post | basis point | 0.061 | Forward a 12 barre dopo costo; NaN se l'orizzonte termina. |
| `label_start_ts` | ex_post | timestamp UTC | 0.000 | Inizio della finestra label forward; mai NaN. |
| `label_end_ts` | ex_post | timestamp UTC | 0.000 | Fine osservata o attesa della finestra label; mai NaN. |
| `label_truncated` | ex_post | booleano | 0.000 | True quando la finestra termina per fine campione; mai NaN. |
| `mfe_12_pts` | ex_post | punti prezzo | 0.061 | Massimo excursion favorevole a 12 barre; NaN con orizzonte incompleto. |
| `mae_12_pts` | ex_post | punti prezzo | 0.061 | Massimo adverse excursion a 12 barre; NaN con orizzonte incompleto. |
| `mfe_12_atr` | ex_post | ATR | 0.061 | MFE a 12 barre normalizzato per ATR; NaN con orizzonte incompleto. |
| `mae_12_atr` | ex_post | ATR | 0.061 | MAE a 12 barre normalizzato per ATR; NaN con orizzonte incompleto. |

## Embargo e trade sign

`trade_sign` è una codifica dell'ipotesi one-sided di continuazione e deve
essere applicato ai forward return solo nella direzione dichiarata. Non
invertire il segno per costruire a posteriori una strategia reversal.

Per qualsiasi split temporale, walk-forward o aggregazione successiva, usare
`label_start_ts` e `label_end_ts` per applicare un embargo: un evento di test
non deve entrare nel train se la sua finestra label si sovrappone alla finestra
label del train. L'embargo deve essere basato sui timestamp di calendario,
non sul solo indice di riga o sul simbolo.
