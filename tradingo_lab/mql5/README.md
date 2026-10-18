# TradinGo MQL5 Expert Advisors

## EA

`Experts/TradinGo/TradinGo_ICT_QualityFailed_XAUUSD.mq5`

Research/test EA for XAUUSD M1 based on the combined ICT setup found in `tradingo_lab`:

- quality ICT reversal in NY Open;
- failed-reversal continuation in high-volatility/overextended conditions;
- configurable spread filter, default `InpMaxSpreadPoints=25`;
- optional relative tick-volume filter, disabled by default, to test whether extra failed-continuation trades can be filtered by participation;
- demo/tester protection enabled by default via `InpRequireDemoAccount=true`;
- risk-percent sizing enabled by default at `InpRiskPercent=1.0`;
- time exit after the configured horizon bars if neither SL nor TP is hit.

## Default strategy mode

`InpStrategyMode=TG_QUALITY_PLUS_FAILED`

This trades:

1. quality reversal when `ATR <= 279` and directional previous-10-minute move is not too extended;
2. failed-reversal continuation when `ATR >= 450` and directional previous-10-minute move is overextended.

Other strategy modes are available in the EA inputs:

- `TG_CORE_WITH_FAILED_REPLACEMENT`
- `TG_QUALITY_ONLY`
- `TG_FAILED_ONLY`
- `TG_CORE_ONLY`

## Installation

Copy the EA to the target terminal data folder, for example:

```powershell
copy mql5\Experts\TradinGo\TradinGo_ICT_QualityFailed_XAUUSD.mq5 "$env:APPDATA\MetaQuotes\Terminal\<TERMINAL_ID>\MQL5\Experts\TradinGo\"
copy mql5\Presets\TradinGo_ICT_QualityFailed_XAUUSD_spread25.set "$env:APPDATA\MetaQuotes\Terminal\<TERMINAL_ID>\MQL5\Profiles\Tester\"
```

Then compile in MetaEditor and run Strategy Tester on:

- symbol: `XAUUSD`
- timeframe: `M1`
- initial deposit: `50000`
- model: real ticks if available, otherwise every tick based on real ticks.

## Research presets

Use these V2 presets to compare the current research direction:

1. `TradinGo_ICT_QualityFailed_XAUUSD_V2_test_a_robust.set`
   - robust baseline from the earlier optimizer cluster;
   - keeps `InpQualityAtrMaxPoints=50`, `InpQualityStopPoints=50`, `InpFailedTargetPoints=500`, `InpFailedStopPoints=210`;
   - volume filter disabled.
2. `TradinGo_ICT_QualityFailed_XAUUSD_V2_test_b_more_trades.set`
   - more-trades candidate from the later Vantage optimizer;
   - uses `InpQualityStopPoints=130` with the same failed-continuation target/stop;
   - volume filter disabled.
3. `TradinGo_ICT_QualityFailed_XAUUSD_V2_test_c_volume_rel_1p2.set`
   - same as test B, but enables the failed-continuation relative volume filter with `InpMinRelativeVolume=1.20`.
4. `TradinGo_ICT_QualityFailed_XAUUSD_V2_test_d_volume_rel_1p4.set`
   - same as test B, but requires stronger relative volume with `InpMinRelativeVolume=1.40`.

The volume filter uses:

```text
relative_volume = signal_bar_tick_volume / average_tick_volume_previous_N_closed_bars
```

Keep the candidate only if it stays between `InpMinRelativeVolume` and `InpMaxRelativeVolume`. The research goal is not to maximize trades; prefer a candidate that keeps drawdown materially below the unfiltered more-trades result while preserving enough trades to be statistically useful.

## Safety

The EA is intended for backtest/demo validation. With default settings it blocks trading on real accounts.
