# SMC Final ETH H1/M5 v1.18 optimized

Research EA derived from `TradinGOld_SMC_Final.mq5` and specialized for Ethereum with high spread costs.

## Intended model

- Setup / structure / BOS timeframe: H1
- Trigger timeframe: M5
- FVG timeframe: H4
- OB timeframe: H1
- Sizing unit: bps

## Default ETH parameters

- `InpPresetMode = PRESET_ETH`
- `InpUseBpsSizing = true`
- `InpMinSLPts = 200.0` bps
- `InpMaxSLPts = 500.0` bps
- `InpSLBufferPts = 1.0` bps
- `InpBE_Trigger_R = 0.80`
- `InpTP1_R = 0.80`, `InpTP1_ClosePct = 50.0`
- `InpTP2_R = 1.20`, `InpTP2_ClosePct = 30.0`
- `InpTP3_R = 2.00`

## Backtest context

Indicative Python porting on ETHUSD Dukascopy M1, 2026-01-01 14:00 UTC to 2026-06-25 23:59 UTC, with 15.75 bps spread/cost stress and 50k starting balance:

- Conservative H1/M5 preset: +12.90%, PF 1.53, max DD 3.16%, max daily DD 2.15%.
- More profitable H1/M5 preset with BE/TP1 1.0R: +13.47%, PF 1.48, max DD 3.16%.

This EA uses the conservative preset by default.

## Verification

Compile check on a temporary copy with XM MetaEditor completed with 0 errors and 0 warnings.

## Safety

This is a versioned research copy. It has not been attached to charts and no Windows tasks were modified.
Run MT5 Strategy Tester / demo forward test on the exact broker ETH symbol before any live use.
