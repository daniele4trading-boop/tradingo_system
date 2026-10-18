from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS_FILE = PROJECT_ROOT / "accounts.json"
PARAMS_FILE = PROJECT_ROOT / "params.json"
STATE_DIR = PROJECT_ROOT / "state"
MAGIC_MIN = 30_260_000
MAGIC_MAX = 30_269_999


class ConfigError(ValueError):
    """Raised when accounts.json or params.json is missing required data."""


@dataclass(frozen=True)
class AccountProfile:
    name: str
    login: int
    password: str
    server: str
    path: str


@dataclass(frozen=True)
class CandidatePair:
    index: int
    leg_a: str
    leg_b: str
    category: str
    magic_a: int
    magic_b: int


CANDIDATE_PAIRS: tuple[CandidatePair, ...] = (
    CandidatePair(1, "XAUUSD", "XAGUSD", "metalli", 30_260_010, 30_260_011),
    CandidatePair(2, "XAUUSD", "XPTUSD", "metalli", 30_260_020, 30_260_021),
    CandidatePair(3, "XPTUSD", "XPDUSD", "metalli", 30_260_030, 30_260_031),
    CandidatePair(4, "EURUSD", "GBPUSD", "forex", 30_260_040, 30_260_041),
    CandidatePair(5, "AUDUSD", "NZDUSD", "forex", 30_260_050, 30_260_051),
    CandidatePair(6, "EURUSD", "USDCHF", "forex", 30_260_060, 30_260_061),
    CandidatePair(7, "EURUSD", "AUDUSD", "forex", 30_260_070, 30_260_071),
    CandidatePair(8, "US500", "US30", "indici", 30_260_080, 30_260_081),
    CandidatePair(9, "US500", "NAS100", "indici", 30_260_090, 30_260_091),
    CandidatePair(10, "GER40", "EU50", "indici", 30_260_100, 30_260_101),
)


REQUIRED_ROLES = ("data_source", "leg_A", "leg_B")
REQUIRED_PROFILE_FIELDS = ("login", "password", "server", "path")

PARAM_SCHEMA: dict[str, dict[str, type | tuple[type, ...]]] = {
    "data": {
        "structural_timeframe": str,
        "structural_lookback_days": int,
        "trigger_timeframe_default": str,
        "trigger_timeframe_indices": str,
        "trigger_lookback_days": int,
    },
    "selection": {
        "correlation_prefilter": (int, float),
        "adf_pvalue_max": (int, float),
        "require_two_window_validation": bool,
        "bonferroni_correction": bool,
        "halflife_min_hours": (int, float),
        "halflife_max_hours": (int, float),
    },
    "timeframe_rules": {
        "halflife_under_hours_to_m15": (int, float),
        "halflife_under_hours_to_h1": (int, float),
    },
    "signals": {
        "zscore_lookback_multiplier": (int, float),
        "entry_z": (int, float),
        "exit_z": (int, float),
        "stop_z": (int, float),
        "time_stop_halflife_mult": (int, float),
    },
    "risk": {
        "risk_per_trade_pct": (int, float),
        "max_concurrent_pairs": int,
        "max_pairs_sharing_leg": int,
        "per_account_daily_loss_pct": (int, float),
        "per_account_max_dd_pct": (int, float),
        "margin_floor_pct": (int, float),
    },
    "costs": {
        "model_spread": bool,
        "model_commission_per_lot": (int, float),
        "model_swap": bool,
    },
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"File mancante: {path}")
    try:
        with path.open("r", encoding="utf-8") as fh:
            data = json.load(fh)
    except json.JSONDecodeError as exc:
        raise ConfigError(f"JSON non valido in {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{path} deve contenere un oggetto JSON alla radice")
    return data


def _require_mapping(data: dict[str, Any], key: str, source: Path) -> dict[str, Any]:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ConfigError(f"{source}: campo '{key}' mancante o non oggetto")
    return value


def _validate_magic_numbers() -> None:
    for pair in CANDIDATE_PAIRS:
        for magic in (pair.magic_a, pair.magic_b):
            if not MAGIC_MIN <= magic <= MAGIC_MAX:
                raise ConfigError(
                    f"Magic number fuori range riservato {MAGIC_MIN}-{MAGIC_MAX}: {magic}"
                )


def validate_accounts(accounts: dict[str, Any]) -> None:
    profiles = _require_mapping(accounts, "profiles", ACCOUNTS_FILE)
    roles = _require_mapping(accounts, "roles", ACCOUNTS_FILE)
    symbol_map = _require_mapping(accounts, "symbol_map", ACCOUNTS_FILE)

    for role in REQUIRED_ROLES:
        profile_name = roles.get(role)
        if not isinstance(profile_name, str) or not profile_name:
            raise ConfigError(f"accounts.json: roles.{role} deve puntare a un profilo")
        if profile_name not in profiles:
            raise ConfigError(
                f"accounts.json: roles.{role}='{profile_name}' non esiste in profiles"
            )

    for profile_name, profile in profiles.items():
        if not isinstance(profile, dict):
            raise ConfigError(f"accounts.json: profiles.{profile_name} deve essere un oggetto")
        for field in REQUIRED_PROFILE_FIELDS:
            if field not in profile:
                raise ConfigError(f"accounts.json: profiles.{profile_name}.{field} mancante")
        if not isinstance(profile["login"], int):
            raise ConfigError(f"accounts.json: profiles.{profile_name}.login deve essere int")
        for field in ("password", "server", "path"):
            if not isinstance(profile[field], str):
                raise ConfigError(
                    f"accounts.json: profiles.{profile_name}.{field} deve essere stringa"
                )

    for profile_name, mapping in symbol_map.items():
        if profile_name not in profiles:
            raise ConfigError(
                f"accounts.json: symbol_map contiene profilo sconosciuto '{profile_name}'"
            )
        if not isinstance(mapping, dict):
            raise ConfigError(f"accounts.json: symbol_map.{profile_name} deve essere oggetto")
        for canonical, broker_symbol in mapping.items():
            if not isinstance(canonical, str) or not isinstance(broker_symbol, str):
                raise ConfigError(
                    f"accounts.json: symbol_map.{profile_name} deve mappare stringa->stringa"
                )

    _validate_magic_numbers()


def validate_params(params: dict[str, Any]) -> None:
    for section, fields in PARAM_SCHEMA.items():
        section_value = params.get(section)
        if not isinstance(section_value, dict):
            raise ConfigError(f"params.json: sezione '{section}' mancante o non oggetto")
        for field, expected_type in fields.items():
            if field not in section_value:
                raise ConfigError(f"params.json: campo '{section}.{field}' mancante")
            value = section_value[field]
            if not isinstance(value, expected_type):
                raise ConfigError(
                    f"params.json: '{section}.{field}' tipo non valido "
                    f"(valore={value!r}, atteso={expected_type})"
                )

    selection = params["selection"]
    if selection["halflife_min_hours"] >= selection["halflife_max_hours"]:
        raise ConfigError("params.json: halflife_min_hours deve essere < halflife_max_hours")
    if not 0 < selection["correlation_prefilter"] <= 1:
        raise ConfigError("params.json: correlation_prefilter deve essere in (0, 1]")
    if not 0 < selection["adf_pvalue_max"] < 1:
        raise ConfigError("params.json: adf_pvalue_max deve essere in (0, 1)")


def load_accounts(path: Path = ACCOUNTS_FILE) -> dict[str, Any]:
    accounts = load_json(path)
    validate_accounts(accounts)
    return accounts


def load_params(path: Path = PARAMS_FILE) -> dict[str, Any]:
    params = load_json(path)
    validate_params(params)
    return params


def load_all() -> tuple[dict[str, Any], dict[str, Any]]:
    return load_accounts(), load_params()


def get_profile(role: str, accounts: dict[str, Any] | None = None) -> AccountProfile:
    if role not in REQUIRED_ROLES:
        raise ConfigError(f"Ruolo non valido '{role}'. Ruoli validi: {', '.join(REQUIRED_ROLES)}")
    accounts = accounts if accounts is not None else load_accounts()
    profile_name = accounts["roles"][role]
    profile = accounts["profiles"][profile_name]
    return AccountProfile(
        name=profile_name,
        login=profile["login"],
        password=profile["password"],
        server=profile["server"],
        path=profile["path"],
    )
