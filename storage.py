from pathlib import Path

from cryptography.fernet import Fernet

_KEY_FILE = Path(__file__).parent / ".key"
_ENV_FILE = Path(__file__).parent / ".env"


def _fernet() -> Fernet:
    if _KEY_FILE.exists():
        return Fernet(_KEY_FILE.read_bytes())
    key = Fernet.generate_key()
    _KEY_FILE.write_bytes(key)
    return Fernet(key)


def _load_raw_env() -> dict[str, str]:
    if not _ENV_FILE.exists():
        return {}
    result = {}
    for line in _ENV_FILE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip()
    return result


def _save_raw_env(data: dict[str, str]) -> None:
    _ENV_FILE.write_text(
        "\n".join(f"{k}={v}" for k, v in data.items()) + "\n"
    )


def load_settings() -> dict[str, str]:
    raw = _load_raw_env()
    if not raw:
        return {}
    f = _fernet()
    result = {}
    for key, val in raw.items():
        try:
            result[key] = f.decrypt(val.encode()).decode()
        except Exception:
            result[key] = ""
    return result


def save_settings(settings: dict[str, str]) -> None:
    f = _fernet()
    _save_raw_env({k: f.encrypt(v.encode()).decode() for k, v in settings.items()})
