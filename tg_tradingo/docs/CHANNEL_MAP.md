# Mappa canali operativi — luglio 2026

## Regola lotti universale

- **1 TP** → 1 trade × **0.20** lotto
- **2+ TP** → N trade × **0.10** lotto ciascuno
- Ignorare i lotti indicati nei messaggi Telegram (`[Lots: 0.02]`)

## Sale attive

### Sala GOLD VIP (`CH_GOLD`)

| Campo | Valore |
|-------|--------|
| telegram_id | -1003302540529 |
| Parser | `sala_gold` |
| TP standard | **2** (8/10 segnali completi; raro 3) |
| Trade demo | 2 × 0.10 (o 3 × 0.10) |
| Magic | 12001, 12002 |
| Flusso | OPEN_NOW → UPDATE_OPEN |

### Sala FOREX VIP (`CH_FOREX`)

| Campo | Valore |
|-------|--------|
| telegram_id | -1002890661441 |
| Parser | `sala_vip` (IT + EN) |
| TP standard | **1** (sempre via Modified) |
| Trade demo | 1 × 0.20 |
| Magic | 13001 |
| Flusso | NEW ORDER → Modified Set TP → CLOSED |

### Sala ORO VIP (`CH_ORO`)

| Campo | Valore |
|-------|--------|
| Parser | `sala_oro` |
| Simbolo | sempre **XAUUSD** (anche senza simbolo nel messaggio) |
| Range entry | `entry` null, solo `entry_range` |
| Edit TP | `UPDATE_TP` (non secondo OPEN) |
| `60 PIPS CLOSE OR BREKIVEN` | `CLOSE_HALF_BE` (chiudi 50% + BE) |

| Campo | Valore |
|-------|--------|
| telegram_id | -1003950995427 |
| Parser | `sala_oro` (nuovo) |
| TP standard | **2** predominante (33 msg 1TP, 14 msg 2TP) |
| Trade demo | 1×0.20 o 2×0.10 in base al messaggio |
| Magic | 14101, 14102 |

### Sala Stark (`CH_STARK`)

| Campo | Valore |
|-------|--------|
| telegram_id | -1002073368935 |
| Parser | `sala_stark` |
| TP standard | **1** (100% segnali 13-17 lug) |
| Trade demo | 1 × 0.20 |
| Magic | 14001 |

### IvanTrades - VIP (`CH_IVAN`)

| Campo | Valore |
|-------|--------|
| telegram_id | **-1004475607491** (canale "IvanTrades - VIP 2.0", sostituisce "IvanTrades - VIP" -1002112242007 dal 5 nov 2026) |
| Parser | `ivan_vip` |
| TP standard | **4** (4 × 0.10 lotto; `Meta size` / `METÀ SIZE` / `MEZZA SIZE` → 4 × 0.05) |
| Trade demo | 4 × 0.10 (o 4 × 0.05 con Meta/METÀ size) |
| Magic | 17001–17004 |
| Flusso | OPEN → CHECK_AND_BE → CLOSE_ALL_SYMBOL |
| Stato | `enabled: false` in config finché non attivato in produzione |

## Accantonata

- **In momo Veritas VIP** — non in config (solo analisi, no copy auto)

## Rimossa

- Zanni VIP (CH1) — non più seguita
