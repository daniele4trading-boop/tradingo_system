from __future__ import annotations

from dataclasses import dataclass
from types import ModuleType
from typing import Any

from core.config import AccountProfile, CANDIDATE_PAIRS, ConfigError
from core.logging_setup import setup_logging


class SymbolResolutionError(RuntimeError):
    """Raised when a canonical symbol cannot be verified on a broker feed."""


@dataclass(frozen=True)
class ContractMetadata:
    canonical: str
    broker_symbol: str
    digits: int
    point: float
    trade_tick_size: float
    trade_tick_value: float
    trade_contract_size: float
    volume_min: float
    volume_max: float
    volume_step: float
    currency_base: str
    currency_profit: str
    currency_margin: str


@dataclass(frozen=True)
class ResolvedPair:
    canonical_a: str
    canonical_b: str
    broker_a: str
    broker_b: str
    meta_a: ContractMetadata
    meta_b: ContractMetadata


LOGGER = setup_logging("statarb.symbols")


def _all_broker_symbols(mt5: ModuleType) -> list[str]:
    symbols = mt5.symbols_get()
    if symbols is None:
        raise SymbolResolutionError(f"mt5.symbols_get() fallito: {mt5.last_error()}")
    return [symbol.name for symbol in symbols]


def _candidate_matches(canonical: str, broker_symbols: list[str]) -> list[str]:
    canonical_upper = canonical.upper()
    matches: list[str] = []
    for broker_symbol in broker_symbols:
        normalized = "".join(ch for ch in broker_symbol.upper() if ch.isalnum())
        if normalized == canonical_upper or normalized.startswith(canonical_upper):
            matches.append(broker_symbol)
    return sorted(matches, key=lambda item: (len(item), item))


def resolve_broker_symbol(
    canonical: str,
    profile: AccountProfile,
    accounts: dict[str, Any],
    mt5: ModuleType,
) -> str:
    """Resolve and verify one canonical symbol for a concrete broker profile.

    The configured symbol_map is treated as an untrusted hint. A symbol is returned only
    after symbol_select()/symbol_info() confirms that it exists on the connected terminal.
    If the configured hint is missing or invalid, the function searches the broker symbol
    list for exact/prefix matches; ambiguous matches fail closed.
    """
    profile_map = accounts.get("symbol_map", {}).get(profile.name, {})
    configured = profile_map.get(canonical)
    broker_symbols = _all_broker_symbols(mt5)

    candidates: list[str] = []
    if configured:
        candidates.append(configured)
    candidates.extend(candidate for candidate in _candidate_matches(canonical, broker_symbols) if candidate not in candidates)

    if not candidates:
        raise SymbolResolutionError(
            f"Nessun candidato broker per simbolo canonico={canonical} profilo={profile.name}"
        )

    verified: list[str] = []
    failures: list[str] = []
    for candidate in candidates:
        selected = mt5.symbol_select(candidate, True)
        info = mt5.symbol_info(candidate) if selected else None
        if selected and info is not None:
            verified.append(candidate)
        else:
            failures.append(f"{candidate}: {mt5.last_error()}")

    if configured and configured in verified:
        return configured

    auto_verified = [symbol for symbol in verified if symbol != configured]
    if len(auto_verified) == 1:
        LOGGER.warning(
            "Auto-correzione simbolo profilo=%s canonical=%s configured=%s resolved=%s",
            profile.name,
            canonical,
            configured,
            auto_verified[0],
        )
        return auto_verified[0]
    if len(auto_verified) > 1:
        raise SymbolResolutionError(
            f"Simbolo canonico={canonical} profilo={profile.name} ambiguo: {auto_verified}"
        )

    raise SymbolResolutionError(
        f"Simbolo canonico={canonical} profilo={profile.name} non verificato. "
        f"Tentativi falliti: {failures}"
    )


def get_contract_metadata(mt5: ModuleType, canonical: str, broker_symbol: str) -> ContractMetadata:
    info = mt5.symbol_info(broker_symbol)
    if info is None:
        raise SymbolResolutionError(
            f"symbol_info() assente per broker_symbol={broker_symbol}: {mt5.last_error()}"
        )

    required_numeric = {
        "point": getattr(info, "point", None),
        "trade_tick_size": getattr(info, "trade_tick_size", None),
        "trade_tick_value": getattr(info, "trade_tick_value", None),
        "trade_contract_size": getattr(info, "trade_contract_size", None),
        "volume_min": getattr(info, "volume_min", None),
        "volume_max": getattr(info, "volume_max", None),
        "volume_step": getattr(info, "volume_step", None),
    }
    missing = [name for name, value in required_numeric.items() if value is None]
    if missing:
        raise SymbolResolutionError(
            f"Metadati contratto incompleti per {broker_symbol}: mancanti {missing}"
        )

    return ContractMetadata(
        canonical=canonical,
        broker_symbol=broker_symbol,
        digits=int(getattr(info, "digits", 0)),
        point=float(required_numeric["point"]),
        trade_tick_size=float(required_numeric["trade_tick_size"]),
        trade_tick_value=float(required_numeric["trade_tick_value"]),
        trade_contract_size=float(required_numeric["trade_contract_size"]),
        volume_min=float(required_numeric["volume_min"]),
        volume_max=float(required_numeric["volume_max"]),
        volume_step=float(required_numeric["volume_step"]),
        currency_base=str(getattr(info, "currency_base", "") or ""),
        currency_profit=str(getattr(info, "currency_profit", "") or ""),
        currency_margin=str(getattr(info, "currency_margin", "") or ""),
    )


def resolve_pair(
    canonical_a: str,
    canonical_b: str,
    profile: AccountProfile,
    accounts: dict[str, Any],
    mt5: ModuleType,
) -> ResolvedPair:
    broker_a = resolve_broker_symbol(canonical_a, profile, accounts, mt5)
    broker_b = resolve_broker_symbol(canonical_b, profile, accounts, mt5)
    return ResolvedPair(
        canonical_a=canonical_a,
        canonical_b=canonical_b,
        broker_a=broker_a,
        broker_b=broker_b,
        meta_a=get_contract_metadata(mt5, canonical_a, broker_a),
        meta_b=get_contract_metadata(mt5, canonical_b, broker_b),
    )


def resolve_candidate_pairs(
    profile: AccountProfile,
    accounts: dict[str, Any],
    mt5: ModuleType,
) -> list[ResolvedPair]:
    resolved: list[ResolvedPair] = []
    for candidate_pair in CANDIDATE_PAIRS:
        try:
            resolved.append(
                resolve_pair(candidate_pair.leg_a, candidate_pair.leg_b, profile, accounts, mt5)
            )
        except SymbolResolutionError as exc:
            LOGGER.warning(
                "Coppia scartata in risoluzione simboli profilo=%s pair=%s/%s motivo=%s",
                profile.name,
                candidate_pair.leg_a,
                candidate_pair.leg_b,
                exc,
            )
    return resolved


def get_pair_magic(canonical_a: str, canonical_b: str) -> tuple[int, int]:
    for pair in CANDIDATE_PAIRS:
        if pair.leg_a == canonical_a and pair.leg_b == canonical_b:
            return pair.magic_a, pair.magic_b
    raise ConfigError(f"Coppia non presente nell'universo canonico: {canonical_a}/{canonical_b}")
