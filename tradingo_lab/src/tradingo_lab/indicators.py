from __future__ import annotations

import numpy as np
import pandas as pd

from .config import FeatureSettings


def add_indicators(df: pd.DataFrame, settings: FeatureSettings) -> pd.DataFrame:
    if df.empty:
        return df
    result = df.copy()
    result["ema_fast"] = ema(result["close"], settings.ema_fast)
    result["ema_mid"] = ema(result["close"], settings.ema_mid)
    result["ema_slow"] = ema(result["close"], settings.ema_slow)
    result["rsi"] = rsi(result["close"], settings.rsi_period)
    result["atr"] = atr(result, settings.atr_period)
    result["adx"] = adx(result, settings.adx_period)
    result["momentum"] = result["close"] - result["close"].shift(settings.momentum_period)
    result["vwap"] = rolling_vwap(result, settings.vwap_period)
    return add_price_action_features(result)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]
    close = df["close"]
    true_range = pd.concat(
        [
            high - low,
            (high - close.shift()).abs(),
            (low - close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = df["high"]
    low = df["low"]

    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=df.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=df.index)

    tr = pd.concat(
        [
            high - low,
            (high - df["close"].shift()).abs(),
            (low - df["close"].shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr_smoothed = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_smoothed
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_smoothed
    dx = ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)) * 100
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def rolling_vwap(df: pd.DataFrame, period: int = 20) -> pd.Series:
    volume_col = "volume"
    if volume_col not in df.columns:
        volume_col = "tick_count" if "tick_count" in df.columns else ""
    typical = (df["high"] + df["low"] + df["close"]) / 3.0
    if not volume_col:
        return typical.rolling(period, min_periods=period).mean()
    volume = df[volume_col].replace(0, np.nan)
    return (typical * volume).rolling(period, min_periods=period).sum() / volume.rolling(
        period, min_periods=period
    ).sum()


def add_price_action_features(df: pd.DataFrame, liquidity_lookback: int = 20) -> pd.DataFrame:
    result = df.copy()
    previous_high = result["high"].rolling(liquidity_lookback, min_periods=liquidity_lookback).max().shift(1)
    previous_low = result["low"].rolling(liquidity_lookback, min_periods=liquidity_lookback).min().shift(1)

    result["sweep_high"] = (result["high"] > previous_high) & (result["close"] < previous_high)
    result["sweep_low"] = (result["low"] < previous_low) & (result["close"] > previous_low)
    result["displacement_up"] = (result["close"] > result["open"]) & (
        (result["close"] - result["open"]) > result["atr"] * 1.2
    )
    result["displacement_down"] = (result["open"] > result["close"]) & (
        (result["open"] - result["close"]) > result["atr"] * 1.2
    )
    result["fvg_bull"] = result["low"] > result["high"].shift(2)
    result["fvg_bear"] = result["high"] < result["low"].shift(2)
    result["trend_up"] = (result["ema_fast"] > result["ema_mid"]) & (result["ema_mid"] > result["ema_slow"])
    result["trend_down"] = (result["ema_fast"] < result["ema_mid"]) & (result["ema_mid"] < result["ema_slow"])
    return result
