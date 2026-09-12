# M1 Scalper optimized EA v1.16

Generated from `Tradingold M1 SCALPER.mq5` after the June 2026 trade-management analysis.

## Default parameters

- `InpMinSLPts = 550.0`
- `InpTP1_R = 0.40`
- `InpTP1_ClosePct = 60.0`
- `InpTPFinal_R = 1.00`
- `InpBE_Trigger_R = 0.40`
- `InpUsePartialClose = true`
- `InpUseBreakEven = true`

## Behaviour

1. The structural SL is still calculated from the protected HH/LL plus buffer.
2. If the structural SL distance is below `InpMinSLPts`, the EA forces the SL farther away to the minimum distance before sizing.
3. Position size is then calculated from the effective SL distance and `InpRiskPct`.
4. The order TP is placed at `InpTPFinal_R`.
5. When price reaches `InpTP1_R`, the EA closes `InpTP1_ClosePct` of the current position.
6. When price reaches `InpBE_Trigger_R`, the EA moves SL to entry for the remaining position.

## Safety notes

- This file is a versioned research copy. It was not attached to charts and no Windows tasks were modified.
- Compile check completed on 2026-06-26 using XM MetaEditor on a temporary copy: 0 errors, 0 warnings.
- Before live use, still run Strategy Tester / demo forward test on the target broker symbol and account settings.
- The analysis that motivated these defaults favored `60/0/40` among the last four tested schemes on the January-June 2026 Python porting backtest.
