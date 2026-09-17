from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ColumnSpec:
    name: str
    definition: str
    unit: str
    source: str
    proxy_or_missing: str
    horizon: str
    is_outcome: bool = False


def _spec(
    name: str, definition: str, unit: str, source: str,
    proxy: str = "", horizon: str = "event",
) -> ColumnSpec:
    return ColumnSpec(name, definition, unit, source, proxy, horizon, False)


_CAUSAL = [
    _spec("event_id", "progressivo evento", "int", "events"),
    _spec("symbol", "simbolo", "text", "config"),
    _spec("tf", "timeframe evento", "text", "events"),
    _spec("bar_ts_utc", "inizio barra", "UTC naive", "bars"),
    _spec("event_ts_utc", "fine barra evento", "UTC naive", "bars"),
    _spec("event_ts_ny", "evento convertito a New York", "ISO", "calendar"),
    _spec("ny_date", "trading day NY", "date", "calendar"),
    _spec("dir", "direzione sweep", "text", "events"),
    _spec("sign", "segno direzionale", "int", "events"),
    _spec(
        "trade_sign", "segno dell'ipotesi dichiarata per il rendimento", "int", "config"
    ),
    _spec("level_type", "tipo livello", "text", "levels"),
    _spec("level_price", "prezzo livello", "price", "levels"),
    _spec("extreme_price", "estremo barra", "price", "bars"),
    _spec("penetration_pts", "distanza estremo-livello", "price", "bars"),
    _spec(
        "penetration_half_spreads",
        "penetrazione divisa per mezzo spread mediano della barra",
        "ratio", "bars",
    ),
    _spec(
        "subspread_sweep",
        "penetrazione inferiore alla soglia configurata in mezzi spread",
        "bool", "bars",
    ),
    _spec("bar_open", "apertura barra evento", "price", "bars"),
    _spec("bar_high", "massimo barra evento", "price", "bars"),
    _spec("bar_low", "minimo barra evento", "price", "bars"),
    _spec("bar_close", "chiusura barra evento", "price", "bars"),
    _spec("bar_volume", "volume barra evento", "volume", "bars"),
    _spec("bar_n_ticks", "tick count barra evento", "count", "bars"),
    _spec("level_form_ts", "timestamp formazione livello", "UTC naive", "bars"),
    _spec("level_age_bars", "eta livello alla sweep", "bars", "events"),
    _spec("level_touch_count", "touch precedenti non-break", "count", "bars"),
    _spec("level_points", "punti nel cluster", "count", "levels"),
    _spec("also_n", "swing N coincidenti", "text", "levels"),
    _spec("atr_tf", "ATR del timeframe configurato", "price", "bars"),
    _spec("wick_ratio", "penetrazione su range della barra", "ratio", "bars"),
    _spec("dist_from_level_atr", "penetrazione normalizzata ATR", "ATR", "bars"),
    _spec("liquidity_density", "swing confermati nella fascia prezzo", "count", "bars"),
    _spec("time_beyond_level_sec", "secondi oltre il livello tra tick", "seconds", "ticks"),
    _spec("t_extreme", "timestamp estremo raw tick", "UTC naive", "ticks"),
    _spec(
        "displacement_60s", "displacement normalizzato a 60 secondi", "ATR/s", "ticks",
        "NaN se nessun midpoint tick supera il livello",
    ),
    _spec(
        "displacement_180s", "displacement normalizzato a 180 secondi", "ATR/s", "ticks",
        "NaN se nessun midpoint tick supera il livello",
    ),
    _spec("displacement_60s_truncated", "finestra 60s troncata", "bool", "ticks"),
    _spec("displacement_180s_truncated", "finestra 180s troncata", "bool", "ticks"),
    _spec(
        "ticks_missing", "giorno raw tick assente", "bool", "ticks",
        "MISSING: file tick non presente",
    ),
    _spec(
        "delta_est", "delta buy-sell nella barra evento", "volume", "bars M1",
        "PROXY: quote-based Lee-Ready",
    ),
    _spec(
        "vpin", "imbalance assoluto su volume mobile", "ratio", "bars M1",
        "APPROX: VPIN a bucket temporali",
    ),
    _spec(
        "cvd_session", "cumulative delta dalla sessione corrente", "volume", "bars M1",
        "PROXY: quote-based Lee-Ready",
    ),
    _spec(
        "delta_divergence_flag",
        "nuovo estremo prezzo con delta divergente; delta a 3 barre include la barra evento",
        "bool", "bars TF",
    ),
    _spec(
        "delta_divergence_mag",
        "z-score della delta a 3 barre inclusiva della barra evento, con deviazione standard mobile causale",
        "z-score", "bars TF",
    ),
    _spec("spread_at_event", "spread mediano alla barra", "price", "bars TF"),
    _spec(
        "vol_vs_hourly_median",
        "valore barra TF corrente / mediana mobile sui precedenti N giorni "
        "dei mediani giornalieri della stessa ora NY; giorno corrente escluso",
        "ratio", "bars TF",
    ),
    _spec(
        "spread_expansion",
        "valore barra TF corrente / mediana mobile sui precedenti N giorni "
        "dei mediani giornalieri della stessa ora NY; giorno corrente escluso",
        "ratio", "bars TF",
    ),
    _spec("minutes_from_london_open", "minuti da apertura Londra", "minutes", "calendar"),
    _spec("minutes_from_ny_open", "minuti da apertura New York", "minutes", "calendar"),
    _spec("session", "sessione Asia/Londra/NY", "text", "calendar"),
    _spec("killzone_flag", "killzone corrente", "text", "calendar"),
    _spec("day_of_week", "giorno settimana NY", "int", "calendar"),
    _spec("is_month_end", "ultimo weekday del mese", "bool", "calendar"),
    _spec(
        "news_high_impact_within_30min", "news high impact entro 30 minuti",
        "bool", "calendar", "MISSING: nessuna fonte news",
    ),
    _spec("vs_session_vwap", "distanza da VWAP sessione", "ATR", "bars M1"),
    _spec("vs_poc", "distanza da POC del giorno precedente", "ATR", "bars M1"),
    _spec("vs_vah", "distanza da VAH del giorno precedente", "ATR", "bars M1"),
    _spec("vs_val", "distanza da VAL del giorno precedente", "ATR", "bars M1"),
    _spec("efficiency_ratio", "efficiency ratio del prezzo", "ratio", "bars TF"),
    _spec("vol_burst_flag", "burst ATR veloce/lento", "bool", "bars TF"),
    _spec("volatility_regime", "regime burst/trend/chop", "text", "bars TF"),
    _spec(
        "overnight_range",
        "range high-low della sessione Asia corrente normalizzato ATR",
        "ATR", "bars M1",
        "NaN se l'ora NY dell'evento è <03:00 o >=18:00, oppure mancano dati",
    ),
    _spec(
        "asia_gap",
        "open Asia corrente meno close pre-17:00 dello stesso giorno, normalizzato ATR",
        "ATR", "bars M1",
        "NaN solo se manca uno dei riferimenti M1",
    ),
    _spec(
        "dxy_intraday_trend", "rendimento DXY lookback", "bp", "external symbol",
        "MISSING: external symbol assente",
    ),
    _spec(
        "ust_proxy_change", "variazione proxy T-Bond", "bp", "external symbol",
        "PROXY: T-Bond CFD per tassi",
    ),
    _spec(
        "xagusd_divergence", "divergenza XAU-XAG", "bp", "external symbol",
        "MISSING: external symbol assente",
    ),
]


def schema_registry(columns: list[str] | None = None) -> list[ColumnSpec]:
    outcomes = [
        ColumnSpec(name, "risultato forward ex-post", unit, "outcomes", "", "future", True)
        for name, unit in [
            ("fwd_ret_5_bp", "bp"), ("fwd_ret_15_bp", "bp"), ("fwd_ret_30_bp", "bp"),
            ("fwd_ret_60_bp", "bp"), ("fwd_ret_120_bp", "bp"),
            ("fwd_ret_5_dir_bp", "bp"), ("fwd_ret_15_dir_bp", "bp"),
            ("fwd_ret_30_dir_bp", "bp"), ("fwd_ret_60_dir_bp", "bp"),
            ("fwd_ret_120_dir_bp", "bp"), ("mfe_120_pts", "price"), ("mae_120_pts", "price"),
            ("mfe_120_atr", "ATR"), ("mae_120_atr", "ATR"),
            ("entry_c1", "price"), ("entry_c1_ts", "UTC naive"),
            ("exec_slip_bp", "bp"), ("cost_rt_bp", "bp"),
        ]
    ]
    known = {spec.name: spec for spec in _CAUSAL + outcomes}
    if columns is None:
        return _CAUSAL + outcomes
    for column in columns:
        if column in known:
            continue
        if re.fullmatch(
            r"fwd_ret_\d+(?:_c1)?(?:_ex)?(?:_dir)?_bp|"
            r"(?:mfe|mae)_\d+(?:_dir)?_(?:bp|pts|atr)",
            column,
        ):
            unit = "bp" if ("fwd_ret" in column or column.endswith("_bp")) else (
                "ATR" if column.endswith("_atr") else "price"
            )
            known[column] = ColumnSpec(
                column, "risultato forward ex-post", unit, "outcomes", "", "future", True
            )
    unknown = sorted(set(columns) - set(known))
    if unknown:
        raise ValueError(f"colonne non registrate: {unknown}")
    return [known[column] for column in columns]


def schema_for_columns(columns: list[str]) -> list[ColumnSpec]:
    return schema_registry(columns)
