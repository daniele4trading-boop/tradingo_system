"""
Secure Credentials Storage - Load encrypted DPAPI secrets.

Usage:
    from secrets_store import load_secrets
    secrets = load_secrets()
    api_key = secrets["api_key"]
    password = secrets["password"]
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)


def load_secrets() -> Dict[str, str]:
    """
    Load encrypted credentials from DPAPI storage.

    Returns:
        Dict with keys: api_key, account, password, created_at, machine, user

    Raises:
        FileNotFoundError: If secrets file not found
        ValueError: If decryption fails
    """

    # Determine secrets location
    secrets_file = Path(os.getenv("APPDATA", "")) / "agents_trading" / "secrets.json"

    if not secrets_file.exists():
        raise FileNotFoundError(
            f"Secrets file not found: {secrets_file}\n"
            f"Setup credentials first: .\\deployment\\set_secrets.ps1"
        )

    try:
        # Read encrypted bytes
        encrypted_bytes = secrets_file.read_bytes()

        # Decrypt using DPAPI
        import ctypes
        from ctypes import wintypes

        class DATA_BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(wintypes.BYTE))]

        def decrypt_dpapi(ciphertext: bytes) -> bytes:
            """Decrypt DPAPI-encrypted bytes."""
            input_blob = DATA_BLOB()
            output_blob = DATA_BLOB()

            input_blob.pbData = ctypes.cast(ctypes.c_char_p(ciphertext), ctypes.POINTER(wintypes.BYTE))
            input_blob.cbData = len(ciphertext)

            ret = ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(input_blob),
                None,
                None,
                None,
                None,
                0,
                ctypes.byref(output_blob)
            )

            if not ret:
                raise ValueError("DPAPI decryption failed")

            plaintext = ctypes.string_at(output_blob.pbData, output_blob.cbData)
            return plaintext

        plaintext = decrypt_dpapi(encrypted_bytes)
        secrets_json = plaintext.decode("utf-8")
        secrets = json.loads(secrets_json)

        logger.info(f"✅ Loaded credentials for account {secrets.get('account')}")
        logger.info(f"   Machine: {secrets.get('machine')} | User: {secrets.get('user')}")

        return secrets

    except ImportError:
        logger.error("⚠️  DPAPI decryption requires Windows (ctypes.windll)")
        raise
    except Exception as e:
        logger.error(f"❌ Failed to load secrets: {e}")
        raise


def get_api_key() -> str:
    """Get Anthropic API key from secrets."""
    secrets = load_secrets()
    return secrets["api_key"]


def get_mt5_password(account: int) -> str:
    """Get MT5 password from secrets."""
    secrets = load_secrets()
    if secrets.get("account") != account:
        raise ValueError(f"Secrets are for account {secrets.get('account')}, not {account}")
    return secrets["password"]


def get_account() -> int:
    """Get Exness account number from secrets."""
    secrets = load_secrets()
    return secrets["account"]


if __name__ == "__main__":
    # Test: load and display secrets (passwords redacted)
    try:
        secrets = load_secrets()
        print("✅ Secrets loaded successfully")
        print(f"  Account: {secrets['account']}")
        print(f"  API Key: {secrets['api_key'][:10]}...")
        print(f"  Password: {'*' * len(secrets['password'])}")
        print(f"  Created: {secrets['created_at']}")
        print(f"  Machine: {secrets['machine']}")
    except Exception as e:
        print(f"❌ Error: {e}")
