from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = Path(os.getenv("ENGLISH_LAB_DATA_DIR", PROJECT_ROOT / "data")).resolve()


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


@dataclass(frozen=True)
class ServerConfig:
    data_root: Path = DATA_ROOT
    database_path: Path = DATA_ROOT / "app.db"
    media_root: Path = Path(os.getenv("MEDIA_STORAGE_ROOT", DATA_ROOT / "media")).resolve()
    import_root: Path = Path(os.getenv("MEDIA_IMPORT_ROOT", DATA_ROOT / "import")).resolve()
    session_cookie: str = os.getenv("SESSION_COOKIE_NAME", "english_lab_session")
    cookie_secure: bool = _env_bool("COOKIE_SECURE", False)
    session_days: int = _env_int("SESSION_DAYS", 30)
    max_upload_bytes: int = _env_int("MAX_AUDIO_UPLOAD_BYTES", 4 * 1024 * 1024 * 1024)
    expose_docs: bool = _env_bool("EXPOSE_API_DOCS", False)


config = ServerConfig()


def ensure_server_dirs() -> None:
    config.data_root.mkdir(parents=True, exist_ok=True)
    config.media_root.mkdir(parents=True, exist_ok=True)
    (config.media_root / "originals").mkdir(parents=True, exist_ok=True)
    (config.media_root / "staging").mkdir(parents=True, exist_ok=True)
    config.import_root.mkdir(parents=True, exist_ok=True)
