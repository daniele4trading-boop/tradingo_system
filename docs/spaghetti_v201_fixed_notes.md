# SpaghettiForex REVERSE PURE v2.01 fixed

Research copy derived from `SpaghettiForex_EA_REVERSE_PURE.mq5`.

## Fixes / defaults

- Default `InpStrategyMask` changed from `27` to `11`:
  - enabled by default: SCALP1 + SCALP2 + MTF1;
  - MTF2 disabled by default for lower drawdown until validated separately.
- Added `InpMTF2UseFixedPipUnits = true`.
- Fixed MTF2 TP/SL unit conversion:
  - old: `InpTP_DefaultPips / _Point * 10.0`;
  - fixed: `InpTP_DefaultPips * 10.0`.

## Why

The old MTF2 formula generated extremely large XAUUSD distances (about 200 USD TP and 120 USD SL with default settings). The MTF2 red-zone manager then closed positions almost immediately when price moved slightly against the entry.

Observed Vantage 2026-06-29 examples:

- `MTF2-DivOro-B` SELL opened around 4059/4058/4064 with SL around 4178/4184 and TP around 3858/3864, then closed immediately at market for small losses.

## Indicative Python backtest on XAUUSD M1 2026

Starting balance: 10,000 USD.

| Variant | Final | Profit | PF | Max DD | Daily DD | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Current bug MTF2 | 8,642.09 | -13.58% | 0.95 | 31.30% | 10.60% | MTF2 P/L -4,501.64 |
| Fixed MTF2 units | 13,466.93 | +34.67% | 1.11 | 17.99% | 7.13% | MTF2 P/L -118.21 |
| MTF2 disabled | 12,776.86 | +27.77% | 1.10 | 13.13% | 3.15% | Lower DD default |

## Verification

Compile check completed with Vantage MetaEditor on a temporary copy: 0 errors, 0 warnings.

## Safety

This is a versioned research copy. It does not replace the EA currently attached to charts and no Windows tasks were modified.
