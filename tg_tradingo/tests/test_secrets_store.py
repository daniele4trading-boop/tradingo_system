"""Test per secrets_store — eseguibili su Linux (dove DPAPI non esiste)."""

from __future__ import annotations

import os
import sys
from unittest import mock


def _import_store():
    # Assicura che il modulo venga reimportato da zero
    for k in list(sys.modules):
        if "secrets_store" in k:
            del sys.modules[k]
    import importlib
    return importlib.import_module("secrets_store")


def test_is_available_non_windows():
    """Su Linux is_available() deve essere False."""
    with mock.patch("sys.platform", "linux"):
        mod = _import_store()
        assert mod.is_available() is False


def test_get_fallback_to_env(monkeypatch):
    """get() deve leggere os.environ se l'archivio non è disponibile."""
    monkeypatch.setenv("SOME_SECRET", "valore-test")
    mod = _import_store()
    # Forza cache vuota e is_available=False
    with mock.patch.object(mod, "is_available", return_value=False):
        store = mod.SecretsStore.__new__(mod.SecretsStore)
        store._path = mod._DEFAULT_PATH
        store._meta_path = mod._DEFAULT_PATH.with_suffix(".meta.json")
        store._cache = {}  # archivio vuoto
        result = store.get("SOME_SECRET")
    assert result == "valore-test"


def test_get_returns_default_when_missing(monkeypatch):
    """get() restituisce default se la chiave manca ovunque."""
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    mod = _import_store()
    store = mod.SecretsStore.__new__(mod.SecretsStore)
    store._path = mod._DEFAULT_PATH
    store._meta_path = mod._DEFAULT_PATH.with_suffix(".meta.json")
    store._cache = {}
    assert store.get("NO_SUCH_KEY", "default") == "default"
    assert store.get("NO_SUCH_KEY") is None


def test_require_raises_when_missing(monkeypatch):
    """require() solleva RuntimeError se la chiave non esiste da nessuna parte."""
    import pytest
    monkeypatch.delenv("NO_SUCH_KEY", raising=False)
    mod = _import_store()
    store = mod.SecretsStore.__new__(mod.SecretsStore)
    store._path = mod._DEFAULT_PATH
    store._meta_path = mod._DEFAULT_PATH.with_suffix(".meta.json")
    store._cache = {}
    with pytest.raises(RuntimeError, match="NO_SUCH_KEY"):
        store.require("NO_SUCH_KEY")


def test_names_returns_sorted():
    mod = _import_store()
    store = mod.SecretsStore.__new__(mod.SecretsStore)
    store._path = mod._DEFAULT_PATH
    store._meta_path = mod._DEFAULT_PATH.with_suffix(".meta.json")
    store._cache = {"ZZZ": "1", "AAA": "2", "MMM": "3"}
    assert store.names() == ["AAA", "MMM", "ZZZ"]


def test_set_raises_on_non_windows():
    """set() deve fallire con NotImplementedError su sistemi non-Windows."""
    import pytest
    mod = _import_store()
    with mock.patch.object(mod, "is_available", return_value=False):
        store = mod.SecretsStore.__new__(mod.SecretsStore)
        store._path = mod._DEFAULT_PATH
        store._meta_path = mod._DEFAULT_PATH.with_suffix(".meta.json")
        store._cache = {}
        with pytest.raises(NotImplementedError):
            store.set("KEY", "value")


def test_required_keys_list():
    """REQUIRED_KEYS deve contenere le chiavi del PRD."""
    mod = _import_store()
    required = set(mod.REQUIRED_KEYS)
    assert "TELEGRAM_API_ID" in required
    assert "TELEGRAM_API_HASH" in required
    assert "ANTHROPIC_API_KEY" in required
    assert "WEBAPP_SECRET_KEY" in required
    assert "DXTRADE_PASSWORD" in required


def test_cli_list_empty(monkeypatch, capsys):
    """CLI list su archivio vuoto stampa il messaggio corretto."""
    mod = _import_store()
    store = mod.SecretsStore.__new__(mod.SecretsStore)
    store._path = mod._DEFAULT_PATH
    store._meta_path = mod._DEFAULT_PATH.with_suffix(".meta.json")
    store._cache = {}
    with mock.patch.object(mod, "store", store):
        rc = mod.main(["list"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "vuoto" in out.lower() or out.strip() == ""


def test_cli_get_missing(monkeypatch, capsys):
    """CLI get NOME con chiave assente restituisce exit code 1."""
    monkeypatch.delenv("PHANTOM", raising=False)
    mod = _import_store()
    store = mod.SecretsStore.__new__(mod.SecretsStore)
    store._path = mod._DEFAULT_PATH
    store._meta_path = mod._DEFAULT_PATH.with_suffix(".meta.json")
    store._cache = {}
    with mock.patch.object(mod, "store", store):
        rc = mod.main(["get", "PHANTOM"])
    assert rc == 1
