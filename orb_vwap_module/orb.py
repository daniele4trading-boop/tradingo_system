"""Opening Range Breakout: High/Low della finestra di apertura di sessione."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .session import Session, SessionSpec


@dataclass(frozen=True)
class ORB:
    high: float
    low: float
    start_utc: pd.Timestamp
    end_utc: pd.Timestamp
    n_bars: int


def compute_orb(orb_df: pd.DataFrame, s: Session, spec: SessionSpec) -> ORB | None:
    """ORB dalle barre del timeframe `spec.orb_tf` che coprono [open, open+orb_tf).

    Con orb_tf = M15 e barre M15 è la singola candela 9:30-9:45 NY. Se il
    timeframe dei dati è più fine dell'ORB (es. M5 per un ORB M15) vengono
    aggregate tutte le barre della finestra. Ritorna None se la finestra non è
    coperta (festivo, buco dati)."""
    win = orb_df[(orb_df.index >= s.open_utc) & (orb_df.index < s.orb_end_utc)]
    if win.empty:
        return None
    return ORB(float(win["high"].max()), float(win["low"].min()),
               win.index[0], s.orb_end_utc, len(win))
