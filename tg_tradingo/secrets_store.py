"""Archivio segreti cifrato con Windows DPAPI (scope utente).

Formato su disco
----------------
  <dir>/store.dpapi      DPAPI( base64( JSON({nome: valore, ...}) ) )
  <dir>/store.meta.json  {created, updated, keys: [...]}   (in chiaro, solo nomi)

Percorso default: C:\\TG_TradinGo\\secrets\\store.dpapi
Override: variabile d'ambiente TRADINGO_SECRETS_FILE

Uso da codice
-------------
    from tg_tradingo.secrets_store import store
    api_id  = store.require("TELEGRAM_API_ID")
    api_key = store.get("ANTHROPIC_API_KEY")          # None se assente
    store.set("WEBAPP_SECRET_KEY", "nuovo-valore")

Uso da CLI
----------
    python -m tg_tradingo.secrets_store list
    python -m tg_tradingo.secrets_store get  NOME [--reveal]
    python -m tg_tradingo.secrets_store set  NOME          # legge da stdin/getpass
    python -m tg_tradingo.secrets_store delete NOME
    python -m tg_tradingo.secrets_store import-env NOME1 NOME2 ...
    python -m tg_tradingo.secrets_store export-check

Fallback
--------
Se una chiave manca nell'archivio viene letto os.environ[NOME] e viene
emesso un WARNING. Questo mantiene la compatibilità durante la migrazione e
permette di eseguire i test su Linux (dove DPAPI non è disponibile).
"""

from __future__ import annotations

import base64
import ctypes
import ctypes.wintypes
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger("tradingo.secrets")

# Chiavi richieste — usate da export-check
REQUIRED_KEYS: list[str] = [
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_API_KEY_BACKUP",
    "GEMINI_API_KEY",
    "TELEGRAM_AGENT_BOT_TOKEN",
    "WEBAPP_SECRET_KEY",
    "DXTRADE_PASSWORD",
]

# Entropia opzionale fissa: aggiunge un secondo fattore (non segreto in sé,
# ma impedisce che un dump dell'archivio sia decifrabile con solo l'account utente
# su un'altra installazione con gli stessi file di profilo copiati).
_ENTROPY_LABEL = b"TGTradinGo-SecretsStore-v1"

_DEFAULT_PATH = Path(r"C:\TG_TradinGo\secrets\store.dpapi")

# ─────────────────────────────────────────────────────────────────────────────
# DPAPI — disponibile solo su Windows
# ─────────────────────────────────────────────────────────────────────────────

def is_available() -> bool:
    """True se DPAPI è disponibile (solo Windows)."""
    return sys.platform == "win32"


if is_available():
    class _Blob(ctypes.Structure):
        _fields_ = [
            ("cbData", ctypes.wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_char)),
        ]

    _crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
    _kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]

    def _encrypt(data: bytes) -> bytes:
        blob_in = _Blob(len(data), ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_char)))
        entropy = _Blob(len(_ENTROPY_LABEL), ctypes.cast(ctypes.c_char_p(_ENTROPY_LABEL), ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        ok = _crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            "TGTradinGo secrets",
            ctypes.byref(entropy),
            None,
            None,
            0,            # scope utente (NON CRYPTPROTECT_LOCAL_MACHINE)
            ctypes.byref(blob_out),
        )
        if not ok:
            raise OSError(f"CryptProtectData fallita: errore {_kernel32.GetLastError()}")
        result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return result

    def _decrypt(data: bytes) -> bytes:
        blob_in = _Blob(len(data), ctypes.cast(ctypes.c_char_p(data), ctypes.POINTER(ctypes.c_char)))
        entropy = _Blob(len(_ENTROPY_LABEL), ctypes.cast(ctypes.c_char_p(_ENTROPY_LABEL), ctypes.POINTER(ctypes.c_char)))
        blob_out = _Blob()
        ok = _crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            ctypes.byref(entropy),
            None,
            None,
            0,
            ctypes.byref(blob_out),
        )
        if not ok:
            raise OSError(f"CryptUnprotectData fallita: errore {_kernel32.GetLastError()}")
        result = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        _kernel32.LocalFree(blob_out.pbData)
        return result

else:
    def _encrypt(data: bytes) -> bytes:  # type: ignore[misc]
        raise NotImplementedError("DPAPI disponibile solo su Windows")

    def _decrypt(data: bytes) -> bytes:  # type: ignore[misc]
        raise NotImplementedError("DPAPI disponibile solo su Windows")


# ─────────────────────────────────────────────────────────────────────────────
# SecretsStore
# ─────────────────────────────────────────────────────────────────────────────

class SecretsStore:
    """Archivio segreti in-process con persistenza DPAPI.

    Singleton accessibile tramite il modulo: ``from secrets_store import store``.
    """

    def __init__(self, path: Path | None = None) -> None:
        env_override = os.environ.get("TRADINGO_SECRETS_FILE")
        self._path: Path = Path(env_override) if env_override else (path or _DEFAULT_PATH)
        self._meta_path: Path = self._path.with_suffix(".meta.json")
        self._cache: dict[str, str] | None = None   # None = non ancora caricato

    # ── I/O ──────────────────────────────────────────────────────────────────

    def _load(self) -> dict[str, str]:
        if not self._path.exists():
            return {}
        try:
            encrypted = base64.b64decode(self._path.read_bytes())
            plaintext = _decrypt(encrypted)
            return json.loads(plaintext.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001
            log.error("Impossibile leggere l'archivio segreti: %s", exc)
            return {}

    def _save(self, data: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        plaintext = json.dumps(data, ensure_ascii=False).encode("utf-8")
        encrypted = _encrypt(plaintext)
        self._path.write_bytes(base64.b64encode(encrypted))
        # Aggiorna metadati (nomi chiave, mai valori)
        now = datetime.now(UTC).isoformat()
        meta: dict[str, Any] = {}
        if self._meta_path.exists():
            try:
                meta = json.loads(self._meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001, S110
                pass
        if "created" not in meta:
            meta["created"] = now
        meta["updated"] = now
        meta["keys"] = sorted(data.keys())
        self._meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    def _ensure(self) -> dict[str, str]:
        if self._cache is None:
            if is_available():
                self._cache = self._load()
            else:
                self._cache = {}
        return self._cache

    # ── API pubblica ─────────────────────────────────────────────────────────

    def get(self, name: str, default: str | None = None) -> str | None:
        """Restituisce il valore del segreto o *default* se assente.

        Se il segreto manca nell'archivio, prova os.environ[name] e logga WARNING.
        """
        data = self._ensure()
        if name in data:
            return data[name]
        env_val = os.environ.get(name)
        if env_val is not None:
            log.warning("secret %s letto da ambiente: migrare nell'archivio", name)
            return env_val
        return default

    def require(self, name: str) -> str:
        """Come get(), ma solleva RuntimeError se il segreto non esiste da nessuna parte."""
        value = self.get(name)
        if not value:
            raise RuntimeError(
                f"Secret '{name}' non trovato nell'archivio né come variabile d'ambiente. "
                f"Usare: python -m tg_tradingo.secrets_store set {name}"
            )
        return value

    def set(self, name: str, value: str) -> None:
        """Aggiunge o aggiorna un segreto nell'archivio e svuota la cache."""
        if not is_available():
            raise NotImplementedError("set() richiede Windows DPAPI")
        data = self._ensure().copy()
        data[name] = value
        self._save(data)
        self._cache = data
        log.info("Secret '%s' salvato nell'archivio.", name)

    def delete(self, name: str) -> bool:
        """Rimuove un segreto. Restituisce True se esisteva."""
        if not is_available():
            raise NotImplementedError("delete() richiede Windows DPAPI")
        data = self._ensure().copy()
        existed = name in data
        if existed:
            del data[name]
            self._save(data)
            self._cache = data
        return existed

    def names(self) -> list[str]:
        """Elenco nomi delle chiavi presenti nell'archivio."""
        return sorted(self._ensure().keys())

    def invalidate_cache(self) -> None:
        """Forza la rilettura dal disco alla prossima operazione."""
        self._cache = None


# Singleton di modulo
store = SecretsStore()


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def _cmd_list(_args: list[str]) -> int:
    names = store.names()
    if not names:
        print("Archivio vuoto (o non ancora inizializzato).")
    else:
        for n in names:
            print(n)
    return 0


def _cmd_get(args: list[str]) -> int:
    if not args:
        print("Uso: get NOME [--reveal]", file=sys.stderr)
        return 1
    name = args[0]
    reveal = "--reveal" in args
    value = store.get(name)
    if value is None:
        print(f"'{name}' non trovato.", file=sys.stderr)
        return 1
    if reveal:
        print(value)
    else:
        print(f"{'*' * min(len(value), 8)}  (usa --reveal per mostrare)")
    return 0


def _cmd_set(args: list[str]) -> int:
    if not args:
        print("Uso: set NOME  (valore letto da stdin o prompt)", file=sys.stderr)
        return 1
    name = args[0]
    if sys.stdin.isatty():
        import getpass
        value = getpass.getpass(f"Valore per '{name}': ")
    else:
        value = sys.stdin.readline().rstrip("\n")
    if not value:
        print("Valore vuoto: operazione annullata.", file=sys.stderr)
        return 1
    store.set(name, value)
    print(f"'{name}' salvato.")
    return 0


def _cmd_delete(args: list[str]) -> int:
    if not args:
        print("Uso: delete NOME", file=sys.stderr)
        return 1
    name = args[0]
    if store.delete(name):
        print(f"'{name}' eliminato.")
    else:
        print(f"'{name}' non trovato.")
    return 0


def _cmd_import_env(args: list[str]) -> int:
    if not args:
        print("Uso: import-env NOME1 NOME2 ...", file=sys.stderr)
        return 1
    errors = 0
    for name in args:
        val = os.environ.get(name)
        if not val:
            print(f"  SKIP {name}: variabile d'ambiente assente o vuota", file=sys.stderr)
            errors += 1
        else:
            store.set(name, val)
            print(f"  OK   {name}  ({len(val)} chars)")
    return 0 if errors == 0 else 1


def _cmd_export_check(_args: list[str]) -> int:
    missing: list[str] = []
    for name in REQUIRED_KEYS:
        val = store.get(name)
        if val:
            src = "archivio" if name in store.names() else "ambiente"
            print(f"  OK   {name}  ({src})")
        else:
            print(f"  MANCA {name}", file=sys.stderr)
            missing.append(name)
    if missing:
        print(f"\n{len(missing)} chiave/i mancante/i.", file=sys.stderr)
        return 1
    print("\nTutte le chiavi richieste sono presenti.")
    return 0


_COMMANDS: dict[str, Any] = {
    "list": _cmd_list,
    "get": _cmd_get,
    "set": _cmd_set,
    "delete": _cmd_delete,
    "import-env": _cmd_import_env,
    "export-check": _cmd_export_check,
}


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    cmd = args[0]
    fn = _COMMANDS.get(cmd)
    if fn is None:
        print(f"Comando sconosciuto: {cmd!r}. Comandi: {', '.join(_COMMANDS)}", file=sys.stderr)
        return 1
    try:
        return fn(args[1:])
    except NotImplementedError as exc:
        print(f"Errore: {exc}", file=sys.stderr)
        return 1
    except Exception:
        log.exception("Errore CLI")
        return 1


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    sys.exit(main())
