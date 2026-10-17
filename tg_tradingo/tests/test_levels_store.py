from __future__ import annotations

from datetime import UTC, datetime, timedelta

from levels_store import (
    Level,
    from_csv,
    merge_levels,
    normalize_level,
    to_csv,
    write_levels_file,
)

NOW = datetime(2026, 10, 12, 8, 0, tzinfo=UTC)


def test_normalize_rejects_bad_rows():
    assert normalize_level({"symbol": "XAUUSD", "kind": "pivot", "price": 1}, "HYBRID_GOLD", now=NOW) is None
    assert normalize_level({"symbol": "XAUUSD", "kind": "support"}, "HYBRID_GOLD", now=NOW) is None
    assert normalize_level({"symbol": "", "kind": "support", "price": 4000}, "H", now=NOW) is None
    assert normalize_level({"symbol": "XAUUSD", "kind": "support", "price": "x"}, "H", now=NOW) is None


def test_normalize_zone_sorted_and_label_cleaned():
    lv = normalize_level({"symbol": "eur/usd", "kind": "Liquidity", "price": 1.0950,
                          "price_to": 1.0930, "label": "minimi, asia"}, "hybrid_forex", now=NOW)
    assert lv == Level("EURUSD", "HYBRID_FOREX", "liquidity", 1.093, 1.095,
                       NOW + timedelta(hours=36), "minimi asia")


def test_merge_replaces_same_source_symbol_and_drops_expired():
    old = [
        Level("XAUUSD", "HYBRID_GOLD", "support", 4000.0, None, NOW + timedelta(hours=1)),
        Level("XAUUSD", "SERTORIO", "resistance", 4100.0, None, NOW + timedelta(hours=1)),
        Level("EURUSD", "HYBRID_FOREX", "support", 1.09, None, NOW - timedelta(minutes=1)),
    ]
    new = [Level("XAUUSD", "HYBRID_GOLD", "support", 3990.0, None, NOW + timedelta(hours=36))]
    out = merge_levels(old, new, "HYBRID_GOLD", now=NOW)
    assert [(lv.source, lv.price) for lv in out] == [("HYBRID_GOLD", 3990.0), ("SERTORIO", 4100.0)]


def test_csv_roundtrip(tmp_path):
    levels = [Level("XAUUSD", "HYBRID_GOLD", "liquidity", 3980.5, 3985.0,
                    datetime(2026, 10, 13, 20, 0, tzinfo=UTC), "equal lows")]
    text = to_csv(levels)
    assert text.splitlines()[1] == "XAUUSD,HYBRID_GOLD,liquidity,3980.5,3985,2026.10.13 20:00,equal lows"
    assert from_csv(text) == levels
    p = tmp_path / "tradingo_levels.csv"
    write_levels_file(p, levels)
    assert from_csv(p.read_text(encoding="utf-8")) == levels
