# SCHEMA

| Nome | Definizione | Unità | Fonte | Proxy/missing | Orizzonte | Outcome |
|---|---|---|---|---|---|---|
| event_id | progressivo evento | int | events |  | event | no |
| symbol | simbolo | text | config |  | event | no |
| tf | timeframe evento | text | events |  | event | no |
| bar_ts_utc | inizio barra | UTC naive | bars |  | event | no |
| event_ts_utc | fine barra evento | UTC naive | bars |  | event | no |
| event_ts_ny | evento convertito a New York | ISO | calendar |  | event | no |
| ny_date | trading day NY | date | calendar |  | event | no |
| dir | direzione sweep | text | events |  | event | no |
| sign | segno direzionale | int | events |  | event | no |
| level_type | tipo livello | text | levels |  | event | no |
| level_price | prezzo livello | price | levels |  | event | no |
| extreme_price | estremo barra | price | bars |  | event | no |
| penetration_pts | distanza estremo-livello | price | bars |  | event | no |
| bar_open | apertura barra evento | price | bars |  | event | no |
| bar_high | massimo barra evento | price | bars |  | event | no |
| bar_low | minimo barra evento | price | bars |  | event | no |
| bar_close | chiusura barra evento | price | bars |  | event | no |
| bar_volume | volume barra evento | volume | bars |  | event | no |
| bar_n_ticks | tick count barra evento | count | bars |  | event | no |
| level_form_ts | timestamp formazione livello | UTC naive | bars |  | event | no |
| level_age_bars | eta livello alla sweep | bars | events |  | event | no |
| level_touch_count | touch precedenti non-break | count | bars |  | event | no |
| level_points | punti nel cluster | count | levels |  | event | no |
| also_n | swing N coincidenti | text | levels |  | event | no |
| wick_ratio | penetrazione su range della barra | ratio | bars |  | event | no |
| atr_tf | ATR del timeframe configurato | price | bars |  | event | no |
| dist_from_level_atr | penetrazione normalizzata ATR | ATR | bars |  | event | no |
| liquidity_density | swing confermati nella fascia prezzo | count | bars |  | event | no |
| time_beyond_level_sec | secondi oltre il livello tra tick | seconds | ticks |  | event | no |
| displacement_60s | displacement normalizzato a 60 secondi | ATR/s | ticks |  | event | no |
| displacement_180s | displacement normalizzato a 180 secondi | ATR/s | ticks |  | event | no |
| t_extreme | timestamp estremo raw tick | UTC naive | ticks |  | event | no |
| displacement_60s_truncated | finestra 60s troncata | bool | ticks |  | event | no |
| displacement_180s_truncated | finestra 180s troncata | bool | ticks |  | event | no |
| ticks_missing | giorno raw tick assente | bool | ticks | MISSING: file tick non presente | event | no |
| delta_est | delta buy-sell nella barra evento | volume | bars M1 | PROXY: quote-based Lee-Ready | event | no |
| vpin | imbalance assoluto su volume mobile | ratio | bars M1 | APPROX: VPIN a bucket temporali | event | no |
| cvd_session | cumulative delta dalla sessione corrente | volume | bars M1 | PROXY: quote-based Lee-Ready | event | no |
| delta_divergence_flag | nuovo estremo prezzo con delta divergente; delta a 3 barre include la barra evento | bool | bars TF |  | event | no |
| delta_divergence_mag | z-score della delta a 3 barre inclusiva della barra evento, con deviazione standard mobile causale | z-score | bars TF |  | event | no |
| spread_at_event | spread mediano alla barra | price | bars TF |  | event | no |
| vol_vs_hourly_median | valore barra TF corrente / mediana mobile sui precedenti N giorni dei mediani giornalieri della stessa ora NY; giorno corrente escluso | ratio | bars TF |  | event | no |
| spread_expansion | valore barra TF corrente / mediana mobile sui precedenti N giorni dei mediani giornalieri della stessa ora NY; giorno corrente escluso | ratio | bars TF |  | event | no |
| minutes_from_london_open | minuti da apertura Londra | minutes | calendar |  | event | no |
| minutes_from_ny_open | minuti da apertura New York | minutes | calendar |  | event | no |
| session | sessione Asia/Londra/NY | text | calendar |  | event | no |
| killzone_flag | killzone corrente | text | calendar |  | event | no |
| day_of_week | giorno settimana NY | int | calendar |  | event | no |
| is_month_end | ultimo weekday del mese | bool | calendar |  | event | no |
| news_high_impact_within_30min | news high impact entro 30 minuti | bool | calendar | MISSING: nessuna fonte news | event | no |
| vs_session_vwap | distanza da VWAP sessione | ATR | bars M1 |  | event | no |
| vs_poc | distanza da POC del giorno precedente | ATR | bars M1 |  | event | no |
| vs_vah | distanza da VAH del giorno precedente | ATR | bars M1 |  | event | no |
| vs_val | distanza da VAL del giorno precedente | ATR | bars M1 |  | event | no |
| efficiency_ratio | efficiency ratio del prezzo | ratio | bars TF |  | event | no |
| vol_burst_flag | burst ATR veloce/lento | bool | bars TF |  | event | no |
| volatility_regime | regime burst/trend/chop | text | bars TF |  | event | no |
| overnight_range | range Asia normalizzato ATR | ATR | bars M1 |  | event | no |
| asia_gap | gap apertura Asia rispetto close precedente | ATR | bars M1 |  | event | no |
| dxy_intraday_trend | rendimento DXY lookback | bp | external symbol | MISSING: external symbol assente | event | no |
| ust_proxy_change | variazione proxy T-Bond | bp | external symbol | PROXY: T-Bond CFD per tassi | event | no |
| xagusd_divergence | divergenza XAU-XAG | bp | external symbol | MISSING: external symbol assente | event | no |
| fwd_ret_5_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_5_dir_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_15_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_15_dir_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_30_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_30_dir_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_60_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_60_dir_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_120_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| fwd_ret_120_dir_bp | risultato forward ex-post | bp | outcomes |  | future | sì |
| mfe_120_pts | risultato forward ex-post | price | outcomes |  | future | sì |
| mae_120_pts | risultato forward ex-post | price | outcomes |  | future | sì |
| mfe_120_atr | risultato forward ex-post | ATR | outcomes |  | future | sì |
| mae_120_atr | risultato forward ex-post | ATR | outcomes |  | future | sì |
