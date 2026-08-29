import ctypes
import json
import logging
import os
from ctypes import wintypes

logger = logging.getLogger("Bridge")

SECRET_KEYS = ("npsso", "steam_refresh_token", "steam_pass")


def split_public(config: dict):
    if not isinstance(config, dict):
        return {}, {}
    public = {key: value for key, value in config.items() if key not in SECRET_KEYS}
    secrets = {key: value for key, value in config.items() if key in SECRET_KEYS and value}
    return public, secrets


def _secrets_path(data_dir: str) -> str:
    name = "secrets.bin" if _use_dpapi() else "secrets.json"
    return os.path.join(data_dir, name)


def _use_dpapi() -> bool:
    return os.name == "nt" and not os.environ.get("BRIDGE_PLAIN_SECRETS")


def load_public(data_dir: str) -> dict:
    path = os.path.join(data_dir, "config.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        logger.error("Could not read config: %s", exc)
        return {}
    public, _ = split_public(data if isinstance(data, dict) else {})
    return public


def save_public(data_dir: str, public: dict) -> None:
    path = os.path.join(data_dir, "config.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(public, handle, indent=4, ensure_ascii=False)


def load_secrets(data_dir: str) -> dict:
    path = _secrets_path(data_dir)
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
        if _use_dpapi():
            raw = _dpapi_unprotect(raw)
        data = json.loads(raw.decode("utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.error("Could not read secrets: %s", exc)
        return {}
    return {key: data[key] for key in SECRET_KEYS if data.get(key)}


def save_secrets(data_dir: str, secrets: dict) -> None:
    payload = {key: secrets[key] for key in SECRET_KEYS if secrets.get(key)}
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if _use_dpapi():
        raw = _dpapi_protect(raw)
    path = _secrets_path(data_dir)
    with open(path, "wb") as handle:
        handle.write(raw)
    if os.name != "nt":
        os.chmod(path, 0o600)


def migrate_plaintext_config(data_dir: str) -> bool:
    path = os.path.join(data_dir, "config.json")
    if not os.path.exists(path):
        return False
    try:
        with open(path, encoding="utf-8") as handle:
            config = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return False
    public, secrets = split_public(config if isinstance(config, dict) else {})
    if not secrets:
        return False
    merged = {**load_secrets(data_dir), **secrets}
    save_secrets(data_dir, merged)
    save_public(data_dir, public)
    logger.info("Moved login tokens out of config.json")
    return True


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_protect(data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), buffer)
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 1, ctypes.byref(blob_out)
    ):
        raise OSError("DPAPI protect failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)


def _dpapi_unprotect(data: bytes) -> bytes:
    buffer = ctypes.create_string_buffer(data, len(data))
    blob_in = DATA_BLOB(len(data), buffer)
    blob_out = DATA_BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 1, ctypes.byref(blob_out)
    ):
        raise OSError("DPAPI unprotect failed")
    try:
        return ctypes.string_at(blob_out.pbData, blob_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(blob_out.pbData)
