import pytest

from sweep_research.src.schema import schema_registry


def test_schema_registry_matches_events():
    columns = ["event_id", "symbol", "fwd_ret_5_bp", "mfe_120_pts"]
    specs = schema_registry(columns)
    assert {x.name for x in specs} == set(columns)
    assert next(x for x in specs if x.name == "fwd_ret_5_bp").is_outcome
    assert next(x for x in specs if x.name == "mfe_120_pts").is_outcome


def test_schema_rejects_unregistered_columns():
    with pytest.raises(ValueError, match="colonne non registrate"):
        schema_registry(["custom"])
