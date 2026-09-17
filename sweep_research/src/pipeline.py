from __future__ import annotations

import time

import pandas as pd

from .events import detect_events
from .features_context import add_context_features
from .features_external import add_external_features
from .features_price import add_price_features
from .features_volume import add_volume_features
from .ticks import tick_event_features


def compute_s0(bundle: dict, cfg) -> pd.DataFrame:
    """Calcola tutte le colonne causali di S0, senza outcome."""
    started = time.perf_counter()
    frames: list[pd.DataFrame] = []
    tick_loader = bundle.get("ticks_by_day")
    for tf, bars in bundle["bars"].items():
        bars.attrs["tf_minutes"] = {"M1": 1, "M5": 5, "M15": 15, "H1": 60}[tf]
        events = detect_events(bars, cfg, tf)
        events = events[events["event_ts_utc"] >= pd.Timestamp(cfg.start)].copy()
        if events.empty:
            continue
        events = add_price_features(events, bars, cfg)
        atr_map = pd.Series(events["atr_tf"].to_numpy(), index=events.index)
        ticks = tick_event_features(cfg, events, tick_loader, atr_map)
        events = pd.concat([events, ticks], axis=1)
        events = add_volume_features(events, bars, bundle["tick_m1_panel"], cfg)
        events = add_context_features(events, bars, bundle["m1"], cfg)
        frames.append(events)
        print(
            f"pipeline_{tf}: {time.perf_counter() - started:.2f}s ({len(events)} events)",
            flush=True,
        )
    if not frames:
        return pd.DataFrame()
    events = pd.concat(frames, ignore_index=True)
    external, _ = add_external_features(events, bundle["m1"], bundle.get("external", {}), cfg)
    return external
