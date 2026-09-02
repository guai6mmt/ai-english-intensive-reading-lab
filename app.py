# -*- coding: utf-8 -*-
from __future__ import annotations

import base64
import difflib
import hashlib
import html
import io
import json
import math
import os
import posixpath
import re
import requests
import shutil
import subprocess
import struct
import threading
import time
import uuid
import wave
import zipfile
import zlib
import xml.etree.ElementTree as ET
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from bs4 import BeautifulSoup
from cryptography.fernet import Fernet, InvalidToken
from docx import Document
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from english_lab.config import config as server_config
from english_lab.database import connect, initialize_database

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - fallback keeps local mode usable.
    OpenAI = None

import video_render

try:
    import oss2
except Exception:  # pragma: no cover - OSS is optional until aligned audio is used.
    oss2 = None


ROOT = Path(__file__).resolve().parent
DATA_DIR = server_config.data_root
UPLOAD_DIR = DATA_DIR / "uploads"
AUDIO_CACHE_DIR = DATA_DIR / "audio_cache"
COVERS_DIR = DATA_DIR / "covers"
VIDEO_EXPORT_DIR = DATA_DIR / "video_export"
STATIC_DIR = ROOT / "static"
LIBRARY_PATH = DATA_DIR / "library.json"
OUTPUTS_PATH = DATA_DIR / "outputs.json"
PROGRESS_PATH = DATA_DIR / "progress.json"
SETTINGS_PATH = DATA_DIR / "settings.json"
SETTINGS_KEY_PATH = DATA_DIR / ".settings.key"

SUPPORTED_EXTENSIONS = {".epub", ".docx", ".txt"}
MAX_AI_CHARS = 10000
EPUB_PARSER_VERSION = 2

AI_PROVIDERS = {
    "deepseek": {
        "api_key_env": "DEEPSEEK_API_KEY",
        "base_url": os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        "model": os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash"),
    },
    "qwen": {
        "api_key_env": "QWEN_API_KEY",
        "base_url": os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
        "model": os.getenv("QWEN_MODEL", "qwen-plus"),
    },
}
DEFAULT_TASK_PROVIDERS = {
    "text": "deepseek",
    "image": "qwen",
    "audio": "qwen",
}
# Per-task list of selectable providers. Text/image use the LLM providers in
# AI_PROVIDERS; audio additionally supports MiniMax (TTS only, not an LLM).
TASK_PROVIDER_CHOICES = {
    "text": set(AI_PROVIDERS),
    "image": set(AI_PROVIDERS),
    "audio": {"qwen", "minimax"},
}
QWEN_TTS_DEFAULTS = {
    "base_url": os.getenv("QWEN_TTS_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")),
    "model": os.getenv("QWEN_TTS_MODEL", "qwen3-tts-flash"),
    "voice": os.getenv("QWEN_TTS_VOICE", "Ethan"),
    "language_type": os.getenv("QWEN_TTS_LANGUAGE_TYPE", "English"),
}
QWEN_ASR_DEFAULTS = {
    "base_url": os.getenv("QWEN_ASR_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")),
    "model": os.getenv("QWEN_ASR_MODEL", "qwen3-asr-flash-filetrans"),
}
MINIMAX_TTS_DEFAULTS = {
    "base_url": os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1"),
    "model": os.getenv("MINIMAX_TTS_MODEL", "speech-2.6-hd"),
    "voice": os.getenv("MINIMAX_TTS_VOICE", "English_Graceful_Lady"),
    "speed": os.getenv("MINIMAX_TTS_SPEED", "1.0"),
    "language_boost": os.getenv("MINIMAX_LANGUAGE_BOOST", "English"),
    # T2A v2 accepts up to 10000 chars; keep margin for whole-article single-pass.
    "max_chars": 9000,
}
OSS_DEFAULTS = {
    "endpoint": os.getenv("OSS_ENDPOINT", ""),
    "bucket": os.getenv("OSS_BUCKET", ""),
    "temp_prefix": os.getenv("OSS_TEMP_PREFIX", "asr-temp/"),
}
# Keyless last-resort translation for word lookup when there is no local entry and
# no language model configured. Only the single queried word is ever sent.
TRANSLATION_API_DEFAULTS = {
    "endpoint": os.getenv("TRANSLATION_API_URL", "https://api.mymemory.translated.net/get"),
    "langpair": os.getenv("TRANSLATION_API_LANGPAIR", "en|zh-CN"),
}

STOPWORDS = {
    "the", "and", "that", "for", "with", "this", "from", "are", "was", "were",
    "have", "has", "had", "not", "but", "his", "her", "its", "their", "they",
    "you", "your", "our", "out", "about", "into", "than", "then", "there",
    "which", "would", "could", "should", "will", "can", "may", "might", "been",
    "being", "when", "where", "what", "who", "why", "how", "more", "most",
    "some", "such", "only", "also", "one", "two", "new", "now", "all", "over",
    "under", "after", "before", "between", "because", "through", "while",
}

MINI_GLOSSARY = {
    "tariff": "关税",
    "inflation": "通货膨胀",
    "deficit": "赤字",
    "subsidy": "补贴",
    "recession": "衰退",
    "productivity": "生产率",
    "regulation": "监管",
    "sovereign": "主权的",
    "geopolitical": "地缘政治的",
    "populist": "民粹主义者/民粹主义的",
    "monetary": "货币的",
    "fiscal": "财政的",
    "climate": "气候",
    "renewable": "可再生的",
    "semiconductor": "半导体",
    "diplomacy": "外交",
    "sanction": "制裁",
    "coalition": "联盟",
    "election": "选举",
    "migration": "移民",
    "demographic": "人口结构的",
    "manufacturing": "制造业",
    "investment": "投资",
    "consumption": "消费",
    "currency": "货币",
    "dollarisation": "美元化",
    "obscure": "模糊的；不清楚的",
    "prudence": "审慎",
    "valiant": "英勇的",
    "implication": "影响；含义",
    "resilience": "韧性",
}

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    initialize_database()
    yield

app = FastAPI(
    title="AI English Intensive Reading Lab",
    lifespan=lifespan,
    docs_url="/docs" if server_config.expose_docs else None,
    redoc_url="/redoc" if server_config.expose_docs else None,
    openapi_url="/openapi.json" if server_config.expose_docs else None,
)


class PackRequest(BaseModel):
    refresh: bool = False


class SentenceRequest(BaseModel):
    sentence: str
    user_imitation: str | None = None


class ReadingAnswerRequest(BaseModel):
    question_id: str
    question: str
    answer: str


class DictationRequest(BaseModel):
    source: str
    answer: str


class WritingFeedbackRequest(BaseModel):
    task: str
    content: str


class VocabSentenceRequest(BaseModel):
    term: str
    sentence: str


class SaveOutputRequest(BaseModel):
    article_id: str
    kind: str
    content: str
    feedback: Any | None = None


class DictionaryLookupRequest(BaseModel):
    term: str
    context: str = ""


class ProgressRequest(BaseModel):
    article_id: str
    status: str
    minutes: int = 0
    activity: str | None = None


class ModelSettingsRequest(BaseModel):
    primary_provider: str | None = None
    text_provider: str | None = None
    image_provider: str | None = None
    audio_provider: str | None = None
    deepseek_api_key: str | None = None
    deepseek_base_url: str | None = None
    deepseek_model: str | None = None
    qwen_api_key: str | None = None
    qwen_base_url: str | None = None
    qwen_model: str | None = None
    qwen_image_model: str | None = None
    qwen_tts_base_url: str | None = None
    qwen_tts_model: str | None = None
    qwen_tts_voice: str | None = None
    qwen_tts_language_type: str | None = None
    dashscope_api_key: str | None = None
    qwen_asr_base_url: str | None = None
    qwen_asr_model: str | None = None
    minimax_api_key: str | None = None
    minimax_group_id: str | None = None
    minimax_base_url: str | None = None
    minimax_tts_model: str | None = None
    minimax_tts_voice: str | None = None
    minimax_tts_speed: str | None = None
    minimax_language_boost: str | None = None
    oss_access_key_id: str | None = None
    oss_access_key_secret: str | None = None
    oss_bucket: str | None = None
    oss_endpoint: str | None = None
    oss_temp_prefix: str | None = None
    translation_fallback: str | None = None
    translation_api_url: str | None = None
    translation_api_langpair: str | None = None


class SpeechRequest(BaseModel):
    text: str
    voice: str | None = None
    language_type: str | None = None


class AlignedAudioRequest(BaseModel):
    refresh: bool = False
    enable_words: bool = True
    voice: str | None = None
    language_type: str | None = None
    provider: str | None = None


class OriginalAlignmentRequest(BaseModel):
    refresh: bool = False
    enable_words: bool = True


class VideoExportRequest(BaseModel):
    provider: str | None = None
    ratios: list[str] = ["16:9"]
    audio_format: str = "wav"


class VideoRenderRequest(BaseModel):
    """路线 C：服务器把第三版设计（H3 横屏 / V3 竖屏）出成「逐帧 HTML + 脚本」素材包，
    截图与 ffmpeg 合成都在用户本机由 render.bat 完成（服务器无需浏览器/字体）。"""
    provider: str | None = None
    ratios: list[str] = ["16:9", "9:16"]
    palette: str = "warm"


class DictationItemsRequest(BaseModel):
    count: int = 6


class NotesRequest(BaseModel):
    notes: str


# ───────── Background job manager (in-memory) ─────────
_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_JOB_TTL_SECONDS = 600  # finished jobs survive 10 min for late polls


def _expire_old_jobs(now: float) -> None:
    stale = [tid for tid, job in _JOBS.items()
             if job.get("finished_at") and now - job["finished_at"] > _JOB_TTL_SECONDS]
    for tid in stale:
        _JOBS.pop(tid, None)


def create_job(kind: str, key: str = "") -> str:
    task_id = uuid.uuid4().hex
    now = time.time()
    with _JOBS_LOCK:
        _expire_old_jobs(now)
        _JOBS[task_id] = {
            "task_id": task_id,
            "kind": kind,
            "key": key,
            "stage": "pending",
            "pct": 0,
            "msg": "排队中…",
            "started_at": now,
            "updated_at": now,
            "finished_at": None,
            "result": None,
            "error": None,
            "extra": {},
        }
    return task_id


def update_job(task_id: str, stage: str | None = None, pct: int | None = None,
               msg: str | None = None, extra: dict[str, Any] | None = None) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(task_id)
        if not job:
            return
        if stage is not None:
            job["stage"] = stage
        if pct is not None:
            job["pct"] = max(0, min(100, int(pct)))
        if msg is not None:
            job["msg"] = msg
        if extra:
            job["extra"].update(extra)
        job["updated_at"] = time.time()


def finish_job(task_id: str, result: Any = None, error: str | None = None) -> None:
    with _JOBS_LOCK:
        job = _JOBS.get(task_id)
        if not job:
            return
        job["finished_at"] = time.time()
        job["updated_at"] = job["finished_at"]
        if error:
            job["error"] = error
            job["stage"] = "failed"
        else:
            job["result"] = result
            job["stage"] = "ready"
            job["pct"] = 100
            job["msg"] = "正文核验完成" if job["kind"] == "content_pairing" else "时间轴已就绪"


def get_job(task_id: str) -> dict[str, Any] | None:
    with _JOBS_LOCK:
        job = _JOBS.get(task_id)
        return dict(job) if job else None


def find_job_by_key(kind: str, key: str) -> dict[str, Any] | None:
    """Return the most recent unfinished job with matching kind+key, if any."""
    with _JOBS_LOCK:
        for job in _JOBS.values():
            if job["kind"] == kind and job["key"] == key and job.get("finished_at") is None:
                return dict(job)
    return None


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    AUDIO_CACHE_DIR.mkdir(exist_ok=True)
    COVERS_DIR.mkdir(exist_ok=True)
    STATIC_DIR.mkdir(exist_ok=True)


_JSON_DATA_LOCK = threading.RLock()
_SETTINGS_KEY_LOCK = threading.Lock()
_ENCRYPTED_PREFIX = "enc:v1:"
_SENSITIVE_SETTING_KEYS = {
    "deepseek_api_key",
    "qwen_api_key",
    "dashscope_api_key",
    "minimax_api_key",
    "oss_access_key_id",
    "oss_access_key_secret",
}


def load_json(path: Path, default: Any) -> Any:
    with _JSON_DATA_LOCK:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default


def save_json(path: Path, data: Any) -> None:
    with _JSON_DATA_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        try:
            with temp_path.open("w", encoding="utf-8", newline="\n") as output:
                json.dump(data, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_path, path)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        finally:
            temp_path.unlink(missing_ok=True)


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def stable_id(*parts: str) -> str:
    return hashlib.sha256("::".join(parts).encode("utf-8")).hexdigest()[:16]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_unlink(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except PermissionError:
        pass


def find_duplicate_source(library: dict[str, Any], content_hash: str) -> dict[str, Any] | None:
    changed = False
    for source in library.get("sources", []):
        source_hash = source.get("content_hash")
        stored_path = Path(source.get("stored_path", ""))
        if not source_hash and stored_path.exists():
            source_hash = file_sha256(stored_path)
            source["content_hash"] = source_hash
            changed = True
        if source_hash == content_hash:
            if changed:
                save_json(LIBRARY_PATH, library)
            return source
    if changed:
        save_json(LIBRARY_PATH, library)
    return None


def _settings_cipher() -> Fernet:
    with _SETTINGS_KEY_LOCK:
        if SETTINGS_KEY_PATH.exists():
            key = SETTINGS_KEY_PATH.read_bytes().strip()
        else:
            SETTINGS_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
            key = Fernet.generate_key()
            try:
                with SETTINGS_KEY_PATH.open("xb") as output:
                    output.write(key)
            except FileExistsError:
                key = SETTINGS_KEY_PATH.read_bytes().strip()
            try:
                os.chmod(SETTINGS_KEY_PATH, 0o600)
            except OSError:
                pass
        return Fernet(key)


def load_settings() -> dict[str, Any]:
    settings = load_json(SETTINGS_PATH, {})
    cipher: Fernet | None = None
    needs_encryption_migration = False
    for key in _SENSITIVE_SETTING_KEYS:
        value = settings.get(key)
        if not isinstance(value, str) or not value:
            continue
        if not value.startswith(_ENCRYPTED_PREFIX):
            needs_encryption_migration = True
            continue
        cipher = cipher or _settings_cipher()
        try:
            settings[key] = cipher.decrypt(value[len(_ENCRYPTED_PREFIX):].encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError, UnicodeDecodeError):
            settings[key] = ""
    if needs_encryption_migration:
        save_settings(settings)
    return settings


def save_settings(settings: dict[str, Any]) -> None:
    stored = dict(settings)
    cipher: Fernet | None = None
    for key in _SENSITIVE_SETTING_KEYS:
        value = stored.get(key)
        if not isinstance(value, str) or not value or value.startswith(_ENCRYPTED_PREFIX):
            continue
        cipher = cipher or _settings_cipher()
        encrypted = cipher.encrypt(value.encode("utf-8")).decode("ascii")
        stored[key] = _ENCRYPTED_PREFIX + encrypted
    save_json(SETTINGS_PATH, stored)


def mask_secret(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "****"
    return value[:4] + "****" + value[-4:]


def normalize_base_url(value: str | None) -> str:
    url = (value or "").strip().rstrip("/")
    for suffix in ("/v1/chat/completions", "/chat/completions"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url


def normalize_dashscope_api_url(value: str | None) -> str:
    url = (value or "").strip().rstrip("/")
    for suffix in ("/services/aigc/multimodal-generation/generation", "/multimodal-generation/generation"):
        if url.endswith(suffix):
            url = url[: -len(suffix)].rstrip("/")
            break
    return url


def task_provider(task: str, settings: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None) -> str:
    settings = settings if settings is not None else load_settings()
    overrides = overrides or {}
    default = DEFAULT_TASK_PROVIDERS.get(task, "deepseek")
    legacy_primary = settings.get("primary_provider") if task == "text" else None
    value = (overrides.get(f"{task}_provider") or settings.get(f"{task}_provider") or legacy_primary or default)
    provider = str(value).strip().lower()
    choices = TASK_PROVIDER_CHOICES.get(task, set(AI_PROVIDERS))
    return provider if provider in choices else default


def primary_provider(settings: dict[str, Any] | None = None, overrides: dict[str, Any] | None = None) -> str:
    return task_provider("text", settings=settings, overrides=overrides)


def resolve_provider(provider: str, overrides: dict[str, Any] | None = None, prefer_primary: bool = True) -> str:
    if not prefer_primary:
        return provider
    return primary_provider(overrides=overrides)


def provider_config(provider: str, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    defaults = AI_PROVIDERS[provider]
    settings = load_settings()
    prefix = provider
    overrides = overrides or {}
    api_key = (
        (overrides.get(f"{prefix}_api_key") or "").strip()
        or (settings.get(f"{prefix}_api_key") or "").strip()
        or (os.getenv(defaults["api_key_env"]) or "").strip()
    )
    base_url = normalize_base_url(
        (overrides.get(f"{prefix}_base_url") or "").strip()
        or settings.get(f"{prefix}_base_url")
        or defaults["base_url"]
    )
    model = (
        (overrides.get(f"{prefix}_model") or "").strip()
        or (settings.get(f"{prefix}_model") or "").strip()
        or defaults["model"]
    )
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "api_key_env": defaults["api_key_env"],
    }


def qwen_tts_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = load_settings()
    overrides = overrides or {}
    qwen = provider_config("qwen", overrides)
    dashscope_key = (
        (overrides.get("dashscope_api_key") or "").strip()
        or (settings.get("dashscope_api_key") or "").strip()
        or (os.getenv("DASHSCOPE_API_KEY") or "").strip()
    )
    base_url = normalize_dashscope_api_url(
        (overrides.get("qwen_tts_base_url") or "").strip()
        or settings.get("qwen_tts_base_url")
        or QWEN_TTS_DEFAULTS["base_url"]
    )
    return {
        "api_key": qwen["api_key"] or dashscope_key,
        "api_key_env": f"{qwen['api_key_env']} / DASHSCOPE_API_KEY",
        "base_url": base_url,
        "model": (
            (overrides.get("qwen_tts_model") or "").strip()
            or (settings.get("qwen_tts_model") or "").strip()
            or QWEN_TTS_DEFAULTS["model"]
        ),
        "voice": (
            (overrides.get("qwen_tts_voice") or "").strip()
            or (settings.get("qwen_tts_voice") or "").strip()
            or QWEN_TTS_DEFAULTS["voice"]
        ),
        "language_type": (
            (overrides.get("qwen_tts_language_type") or "").strip()
            or (settings.get("qwen_tts_language_type") or "").strip()
            or QWEN_TTS_DEFAULTS["language_type"]
        ),
    }


def qwen_asr_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = load_settings()
    overrides = overrides or {}
    qwen = provider_config("qwen", overrides)
    dashscope_key = (
        (overrides.get("dashscope_api_key") or "").strip()
        or (settings.get("dashscope_api_key") or "").strip()
        or (os.getenv("DASHSCOPE_API_KEY") or "").strip()
    )
    return {
        "api_key": qwen["api_key"] or dashscope_key,
        "api_key_env": f"{qwen['api_key_env']} / DASHSCOPE_API_KEY",
        "base_url": normalize_dashscope_api_url(
            (overrides.get("qwen_asr_base_url") or "").strip()
            or settings.get("qwen_asr_base_url")
            or QWEN_ASR_DEFAULTS["base_url"]
        ),
        "model": (
            (overrides.get("qwen_asr_model") or "").strip()
            or (settings.get("qwen_asr_model") or "").strip()
            or QWEN_ASR_DEFAULTS["model"]
        ),
    }


def minimax_tts_config(overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    settings = load_settings()
    overrides = overrides or {}

    def pick(key: str, default: str) -> str:
        return (
            (overrides.get(key) or "").strip()
            or (settings.get(key) or "").strip()
            or default
        )

    api_key = (
        (overrides.get("minimax_api_key") or "").strip()
        or (settings.get("minimax_api_key") or "").strip()
        or (os.getenv("MINIMAX_API_KEY") or "").strip()
    )
    group_id = (
        (overrides.get("minimax_group_id") or "").strip()
        or (settings.get("minimax_group_id") or "").strip()
        or (os.getenv("MINIMAX_GROUP_ID") or "").strip()
    )
    base_url = pick("minimax_base_url", MINIMAX_TTS_DEFAULTS["base_url"]).rstrip("/")
    return {
        "api_key": api_key,
        "api_key_env": "MINIMAX_API_KEY",
        "group_id": group_id,
        "base_url": base_url,
        "model": pick("minimax_tts_model", MINIMAX_TTS_DEFAULTS["model"]),
        "voice": pick("minimax_tts_voice", MINIMAX_TTS_DEFAULTS["voice"]),
        "speed": pick("minimax_tts_speed", MINIMAX_TTS_DEFAULTS["speed"]),
        "language_boost": pick("minimax_language_boost", MINIMAX_TTS_DEFAULTS["language_boost"]),
        "max_chars": MINIMAX_TTS_DEFAULTS["max_chars"],
    }


def oss_config() -> dict[str, str]:
    settings = load_settings()

    def pick(key: str, env: str, fallback: str = "") -> str:
        value = (settings.get(key) or "").strip()
        if value:
            return value
        return (os.getenv(env, fallback) or "").strip()

    prefix = pick("oss_temp_prefix", "OSS_TEMP_PREFIX", OSS_DEFAULTS["temp_prefix"]) or "asr-temp/"
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    return {
        "access_key_id": pick("oss_access_key_id", "OSS_ACCESS_KEY_ID"),
        "access_key_secret": pick("oss_access_key_secret", "OSS_ACCESS_KEY_SECRET"),
        "bucket": pick("oss_bucket", "OSS_BUCKET", OSS_DEFAULTS["bucket"]),
        "endpoint": pick("oss_endpoint", "OSS_ENDPOINT", OSS_DEFAULTS["endpoint"]),
        "temp_prefix": prefix,
    }


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
    mojibake = {
        "â€™": "’", "â€˜": "‘", "â€œ": "“", "â€": "”",
        "â€“": "–", "â€”": "—", "Â·": "·", "Â": "",
    }
    for broken, repaired in mojibake.items():
        text = text.replace(broken, repaired)
    text = re.sub(r"\ufffd{1,2}", "’", text)
    text = text.replace("\u00a0", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def normalize_paragraphs(paragraphs: list[str]) -> list[str]:
    cleaned = []
    for paragraph in paragraphs:
        paragraph = clean_text(paragraph)
        paragraph = re.sub(r"\s+([,.;:!?])", r"\1", paragraph)
        paragraph = re.sub(r"([A-Za-z])-\s+([a-z])", r"\1\2", paragraph)
        paragraph = repair_initial_letter_spacing(paragraph)
        if paragraph:
            cleaned.append(paragraph)
    return cleaned


def repair_initial_letter_spacing(paragraph: str) -> str:
    paragraph = re.sub(r"^([A-Z])\s+([a-z]{2,})\b", lambda m: m.group(1) + m.group(2), paragraph)
    paragraph = re.sub(r"^([A-Z])\s+([A-Z]{2,})\b", lambda m: m.group(1) + m.group(2).lower(), paragraph)
    return paragraph


def is_noise_paragraph(paragraph: str) -> tuple[bool, str]:
    text = clean_text(paragraph)
    low = text.lower()
    if not text:
        return True, "空段落"
    if re.fullmatch(r"\d{1,2}月\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}\s+(上午|下午)", text):
        return True, "日期时间"
    noise_patterns = [
        ("read the rest of our cover package", "相关文章引导"),
        ("for subscribers only", "订阅推广"),
        ("this article was downloaded by calibre", "calibre来源"),
        ("sign up to our weekly", "订阅推广"),
        ("chapter menu", "导航菜单"),
        ("main menu", "导航菜单"),
        ("next item", "导航菜单"),
        ("previous item", "导航菜单"),
        ("章节菜单", "导航菜单"),
        ("主菜单", "导航菜单"),
        ("下一项", "导航菜单"),
        ("上一项", "导航菜单"),
    ]
    for needle, reason in noise_patterns:
        if needle in low:
            return True, reason
    if "https://www.economist.com/" in low and len(words(text)) < 30:
        return True, "来源链接"
    if text.endswith("·") and len(words(text)) <= 12:
        return True, "相关文章链接"
    if len(words(text)) <= 12 and not re.search(r"[.!?。！？]$", text):
        return True, "短链接/标题碎片"
    if len(words(text)) <= 8 and not re.search(r"[.!?]$", text):
        return True, "短链接/标题碎片"
    return False, ""


def clean_article_paragraphs(paragraphs: list[str]) -> dict[str, Any]:
    cleaned = []
    removed = []
    notes = []
    for index, paragraph in enumerate(paragraphs, 1):
        original = clean_text(paragraph)
        should_remove, reason = is_noise_paragraph(original)
        if should_remove:
            removed.append({"index": index, "reason": reason, "text": original})
            continue
        fixed = clean_text(original)
        fixed = re.sub(r"\s+([,.;:!?])", r"\1", fixed)
        fixed = re.sub(r"([A-Za-z])-\s+([a-z])", r"\1\2", fixed)
        fixed = re.sub(r"\s*·\s*([,.;:!?])", r"\1", fixed)
        fixed = re.sub(r"(?<=[A-Za-z])·\s+(?=[a-z])", " ", fixed)
        fixed = re.sub(r"\s*·\s*$", "", fixed)
        repaired = repair_initial_letter_spacing(fixed)
        if repaired != original:
            notes.append({"index": index, "before": original[:180], "after": repaired[:180], "reason": "首字母/空格/断词修复"})
        cleaned.append(repaired)
    return {"cleaned_paragraphs": cleaned, "removed_paragraphs": removed, "normalization_notes": notes}


def split_sentences(text: str) -> list[str]:
    protected = text
    abbreviations = [
        "Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "Sr.", "Jr.", "vs.", "i.e.",
        "e.g.", "etc.", "St.", "U.S.", "U.K.", "a.m.", "p.m.", "Inc.", "Ltd.",
    ]
    for abbr in abbreviations:
        protected = protected.replace(abbr, abbr.replace(".", "<DOT>"))
    protected = re.sub(r"(\d+)\.(\d+)", r"\1<DOT>\2", protected)
    chunks = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", protected)
    sentences = [clean_text(c.replace("<DOT>", ".")) for c in chunks]
    return [s for s in sentences if len(s.split()) > 3]


def words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z'\-]*", text or "")


def article_text(article: dict[str, Any], cleaned: bool = True) -> str:
    paragraphs = article.get("cleaned_paragraphs") if cleaned else None
    paragraphs = paragraphs or article.get("paragraphs", [])
    return "\n\n".join(paragraphs)


def truncate_text(text: str, limit: int = MAX_AI_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n\n...[middle omitted]...\n\n" + text[-limit // 2 :]


def article_stats(paragraphs: list[str]) -> dict[str, Any]:
    text = " ".join(paragraphs)
    token_list = words(text)
    lower = [w.lower().strip("-'") for w in token_list]
    sentences = split_sentences(text)
    long_sentences = [s for s in sentences if len(words(s)) >= 32]
    content_words = [w for w in lower if w and w not in STOPWORDS]
    unique = set(content_words)
    avg_sentence = round(len(token_list) / max(len(sentences), 1), 1)
    lexical_density = round(len(content_words) / max(len(lower), 1), 2)
    difficulty_score = min(avg_sentence / 6, 4) + min(len(long_sentences) / 3, 3) + min(lexical_density * 4, 3)
    cefr = "B1" if difficulty_score < 4 else "B2" if difficulty_score < 6 else "C1" if difficulty_score < 8 else "C2"
    top_terms = [
        {"term": term, "count": count}
        for term, count in Counter(content_words).most_common(18)
        if len(term) > 3
    ][:12]
    return {
        "word_count": len(token_list),
        "sentence_count": len(sentences),
        "paragraph_count": len(paragraphs),
        "reading_minutes": max(1, round(len(token_list) / 180)),
        "avg_sentence_words": avg_sentence,
        "long_sentence_count": len(long_sentences),
        "unique_word_count": len(unique),
        "lexical_density": lexical_density,
        "cefr": cefr,
        "top_terms": top_terms,
    }


def infer_ai_tags(article: dict[str, Any]) -> dict[str, list[str]]:
    text = article_text(article).lower()
    stats = article["stats"]
    topics = []
    topic_rules = {
        "经济": ["inflation", "tariff", "market", "growth", "fiscal", "monetary", "investment", "trade"],
        "科技": ["ai", "technology", "software", "chip", "semiconductor", "data", "digital"],
        "社会": ["society", "family", "people", "migration", "demographic", "inequality"],
        "文化": ["culture", "art", "book", "film", "music", "religion"],
        "教育": ["school", "education", "student", "university", "teacher"],
        "商业": ["company", "business", "firm", "profit", "consumer", "brand"],
        "时事": ["election", "government", "war", "policy", "president", "minister"],
    }
    for label, needles in topic_rules.items():
        if any(n in text for n in needles):
            topics.append(label)
    language = []
    if stats["long_sentence_count"] >= 4:
        language.append("长难句较多")
    if stats["lexical_density"] >= 0.52:
        language.append("学术词汇较多")
    if any(x in text for x in ["however", "therefore", "although", "whereas", "despite"]):
        language.append("逻辑连接词较多")
    if stats["cefr"] in {"C1", "C2"}:
        language.append("适合精读")
    if stats["avg_sentence_words"] < 24:
        language.append("适合听写")
    if any(x in text for x in ["should", "must", "argue", "claim", "because"]):
        language.append("适合写作模仿")
    return {
        "difficulty": [stats["cefr"]],
        "topics": topics or ["综合"],
        "language": language or ["适合泛读"],
    }


def enrich_article(article: dict[str, Any]) -> dict[str, Any]:
    clean_result = clean_article_paragraphs(article.get("paragraphs", []))
    article["cleaned_paragraphs"] = clean_result["cleaned_paragraphs"]
    article["removed_paragraphs"] = clean_result["removed_paragraphs"]
    article["normalization_notes"] = clean_result["normalization_notes"]
    article["cleaned_stats"] = article_stats(article["cleaned_paragraphs"])
    article["stats"] = article.get("stats") or article_stats(article.get("paragraphs", []))
    article["ai_tags"] = infer_ai_tags(article)
    article["favorite"] = article.get("favorite", False)
    article["last_opened_at"] = article.get("last_opened_at")
    return article


def extract_epub_articles(path: Path, source_id: str) -> list[dict[str, Any]]:
    articles: list[dict[str, Any]] = []
    with zipfile.ZipFile(path) as book:
        names = book.namelist()

        # Build feed_N → section name map from feed index pages (e.g. feed_0/index_*.html)
        feed_sections: dict[str, str] = {}
        for idx_name in names:
            m = re.match(r"(feed_(\d+))/index[^/]*\.(html|xhtml)$", idx_name, re.IGNORECASE)
            if not m:
                continue
            feed_num = m.group(2)
            if feed_num in feed_sections:
                continue
            raw = decode_epub_markup(book.read(idx_name))
            soup = BeautifulSoup(raw, "html.parser")
            headings = [clean_text(h.get_text(" ")) for h in soup.find_all(["h1", "h2", "h3"])]
            headings = [h for h in headings if h and h.lower() not in {"unknown", "未知"}]
            if headings:
                feed_sections[feed_num] = headings[0]

        article_names = [n for n in names if n.lower().endswith((".html", ".xhtml")) and "/article_" in n.lower()]
        spine_names = epub_spine_documents(book)
        html_names = article_names
        require_article_path = True
        if spine_names and not article_names:
            html_names = spine_names
            require_article_path = False
        elif not article_names:
            html_names = [n for n in names if n.lower().endswith((".html", ".xhtml"))]
            require_article_path = False
        current_section = "Articles"
        for index, name in enumerate(html_names, 1):
            raw = decode_epub_markup(book.read(name))
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            section_index = soup.select_one(".section_index_title")
            if section_index:
                section_name = clean_text(section_index.get_text(" "))
                if section_name:
                    current_section = section_name
                continue
            headings = [clean_text(h.get_text(" ")) for h in soup.find_all(["h1", "h2", "h3"])]
            headings = [h for h in headings if h and h.lower() not in {"unknown", "未知"}]
            explicit_title = soup.select_one(".te_article_title")
            explicit_subtitle = soup.select_one(".te_article_rubric")
            explicit_section = soup.select_one(".te_section_title")
            title = clean_text(explicit_title.get_text(" ")) if explicit_title else (headings[0] if headings else "")
            subtitle = clean_text(explicit_subtitle.get_text(" ")) if explicit_subtitle else (headings[1] if len(headings) > 1 else "")
            # Prefer feed index section name for accurate grouping
            feed_m = re.search(r"feed_(\d+)/", name)
            if feed_m and feed_m.group(1) in feed_sections:
                section = feed_sections[feed_m.group(1)]
            elif explicit_section:
                section = clean_text(explicit_section.get_text(" ")) or current_section
            elif spine_names:
                section = current_section
            else:
                section = headings[2] if len(headings) > 2 else infer_section(name)
            paragraphs = filter_article_paragraphs([clean_text(p.get_text(" ")) for p in soup.find_all(["p", "li"])])
            if len(" ".join(paragraphs).split()) < 80:
                paragraphs = infer_paragraphs_from_text(clean_text(soup.get_text(" | ")))
            explicit_article = explicit_title is not None
            if not explicit_article and len(" ".join(paragraphs).split()) < 80:
                continue
            if explicit_article:
                if len(words(" ".join(paragraphs))) < 8:
                    continue
            elif not is_valid_article_page(name, title, paragraphs, require_article_path=require_article_path):
                continue
            if not title:
                title = infer_title(paragraphs, fallback=f"Article {index}")
            article = {
                "id": stable_id(source_id, name),
                "source_id": source_id,
                "path": name,
                "title": title,
                "subtitle": subtitle,
                "section": section,
                "paragraphs": paragraphs,
                "stats": article_stats(paragraphs),
                "created_at": now_iso(),
            }
            articles.append(enrich_article(article))
    return articles


def epub_spine_documents(book: zipfile.ZipFile) -> list[str]:
    """Return readable EPUB documents in package spine order.

    EPUB 3 books often use opaque filenames. Reading entries in ZIP order (or
    alphabetically) therefore scrambles articles and loses section boundaries.
    Invalid or incomplete package metadata simply falls back to the legacy
    scanner used by :func:`extract_epub_articles`.
    """
    names = set(book.namelist())
    try:
        container = ET.fromstring(book.read("META-INF/container.xml"))
        rootfile = next(
            node for node in container.iter()
            if node.tag.rsplit("}", 1)[-1] == "rootfile" and node.attrib.get("full-path")
        )
        package_path = posixpath.normpath(rootfile.attrib["full-path"].replace("\\", "/"))
        package = ET.fromstring(book.read(package_path))
    except (KeyError, StopIteration, ET.ParseError, ValueError):
        return []

    package_dir = posixpath.dirname(package_path)
    manifest: dict[str, tuple[str, str]] = {}
    for node in package.iter():
        if node.tag.rsplit("}", 1)[-1] != "item" or not node.attrib.get("id"):
            continue
        href = node.attrib.get("href", "").split("#", 1)[0]
        resolved = posixpath.normpath(posixpath.join(package_dir, href.replace("\\", "/")))
        manifest[node.attrib["id"]] = (resolved, node.attrib.get("properties", ""))

    ordered: list[str] = []
    for node in package.iter():
        if node.tag.rsplit("}", 1)[-1] != "itemref":
            continue
        entry = manifest.get(node.attrib.get("idref", ""))
        if not entry:
            continue
        resolved, properties = entry
        if "nav" in properties.split() or not resolved.lower().endswith((".html", ".xhtml")):
            continue
        if resolved in names and resolved not in ordered:
            ordered.append(resolved)
    return ordered


def decode_epub_markup(data: bytes) -> str:
    """Decode EPUB markup while tolerating mislabelled legacy feeds."""
    head = data[:512].decode("ascii", errors="ignore")
    match = re.search(r"encoding=[\"']\s*([^\"']+)", head, re.I)
    candidates = [match.group(1).strip()] if match else []
    candidates.extend(["utf-8-sig", "utf-8", "windows-1252"])
    seen: set[str] = set()
    for encoding in candidates:
        key = encoding.lower()
        if key in seen:
            continue
        seen.add(key)
        try:
            return data.decode(encoding)
        except (LookupError, UnicodeDecodeError):
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx_articles(path: Path, source_id: str) -> list[dict[str, Any]]:
    doc = Document(str(path))
    paragraphs = [clean_text(p.text) for p in doc.paragraphs if clean_text(p.text)]
    if not paragraphs:
        return []
    title_candidates = [p for p in paragraphs[:160] if 4 <= len(p.split()) <= 16 and not p.endswith(".")]
    title_set = set(title_candidates)
    chunks: list[tuple[str, list[str]]] = []
    current_title = infer_title(paragraphs)
    current_body: list[str] = []
    for p in paragraphs:
        is_heading = p in title_set and len(current_body) > 3
        if is_heading:
            if len(" ".join(current_body).split()) > 120:
                chunks.append((current_title, current_body))
            current_title = p
            current_body = []
        else:
            current_body.append(p)
    if len(" ".join(current_body).split()) > 120:
        chunks.append((current_title, current_body))
    if not chunks:
        chunks = [(infer_title(paragraphs), paragraphs)]
    articles = []
    for index, (title, body) in enumerate(chunks, 1):
        article = {
            "id": stable_id(source_id, str(index), title),
            "source_id": source_id,
            "path": f"docx:{index}",
            "title": title,
            "subtitle": "",
            "section": "DOCX",
            "paragraphs": body,
            "stats": article_stats(body),
            "created_at": now_iso(),
        }
        articles.append(enrich_article(article))
    return articles


def extract_txt_article(path: Path, source_id: str) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    paragraphs = [clean_text(p) for p in re.split(r"\n\s*\n", text) if clean_text(p)]
    article = {
        "id": stable_id(source_id, path.name),
        "source_id": source_id,
        "path": path.name,
        "title": infer_title(paragraphs, fallback=path.stem),
        "subtitle": "",
        "section": "Text",
        "paragraphs": paragraphs,
        "stats": article_stats(paragraphs),
        "created_at": now_iso(),
    }
    return [enrich_article(article)]


def filter_article_paragraphs(paragraphs: list[str]) -> list[str]:
    blocked = {"unknown", "未知", "下一项", "上一项", "章节菜单", "主菜单", "table of contents", "contents"}
    cleaned = []
    for paragraph in paragraphs:
        paragraph = clean_text(paragraph)
        low = paragraph.lower()
        if not paragraph or low in blocked:
            continue
        if len(paragraph) < 20 and any(item in low for item in blocked):
            continue
        cleaned.append(paragraph)
    return cleaned


def infer_paragraphs_from_text(text: str) -> list[str]:
    parts = [clean_text(p) for p in re.split(r"\s*\|\s*", text) if clean_text(p)]
    blocked = {"未知", "下一项", "上一项", "章节菜单", "主菜单"}
    return [p for p in parts if p not in blocked and len(p.split()) > 8]


def infer_title(paragraphs: list[str], fallback: str = "Untitled article") -> str:
    for p in paragraphs[:8]:
        if 3 <= len(p.split()) <= 18 and not p.endswith("."):
            return p
    return fallback


def infer_section(path: str) -> str:
    match = re.search(r"feed_(\d+)", path)
    return f"Feed {match.group(1)}" if match else "Articles"


def is_valid_article_page(path: str, title: str, paragraphs: list[str], require_article_path: bool = True) -> bool:
    low_path = path.lower()
    if require_article_path and "/article_" not in low_path:
        return False
    low_title = (title or "").strip().lower()
    if low_title in {"table of contents", "contents", "目录", "未知", "unknown"}:
        return False
    clean_result = clean_article_paragraphs(paragraphs)
    useful = clean_result["cleaned_paragraphs"]
    useful_words = len(words(" ".join(useful)))
    if useful_words < 120:
        return False
    punctuation_ratio = sum(1 for p in useful if re.search(r"[.!?]$", p)) / max(len(useful), 1)
    if len(useful) >= 8 and punctuation_ratio < 0.35:
        return False
    return True


def parse_upload(path: Path, source_id: str) -> list[dict[str, Any]]:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return extract_epub_articles(path, source_id)
    if suffix == ".docx":
        return extract_docx_articles(path, source_id)
    if suffix == ".txt":
        return extract_txt_article(path, source_id)
    if suffix == ".pdf":
        raise HTTPException(400, "当前版本支持 EPUB、DOCX 或 TXT；PDF 请先转换为 DOCX/TXT。")
    raise HTTPException(400, f"Unsupported file type: {suffix}")


def library_summary(library: dict[str, Any]) -> dict[str, Any]:
    sources = library.get("sources", [])
    articles = [a for s in sources for a in s.get("articles", [])]
    return {
        "source_count": len(sources),
        "article_count": len(articles),
        "word_count": sum(a.get("stats", {}).get("word_count", 0) for a in articles),
        "reading_minutes": sum(a.get("stats", {}).get("reading_minutes", 0) for a in articles),
        "cefr_distribution": dict(Counter(a.get("stats", {}).get("cefr", "?") for a in articles)),
    }


def mutate_library(mutator: Callable[[dict[str, Any]], Any]) -> Any:
    with _JSON_DATA_LOCK:
        library = load_json(LIBRARY_PATH, {"sources": []})
        result = mutator(library)
        save_json(LIBRARY_PATH, library)
        return result


def find_article(article_id: str) -> dict[str, Any]:
    library = load_json(LIBRARY_PATH, {"sources": []})
    for source in library.get("sources", []):
        for article in source.get("articles", []):
            if article["id"] == article_id:
                return enrich_article(article)
    raise HTTPException(404, "Article not found")


def update_article(article_id: str, updater: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    def apply(library: dict[str, Any]) -> dict[str, Any]:
        for source in library.get("sources", []):
            for article in source.get("articles", []):
                if article["id"] == article_id:
                    enrich_article(article)
                    updater(article)
                    return article
        raise HTTPException(404, "Article not found")
    return mutate_library(apply)


def cached_meta(provider: str, model: str = "saved-result") -> dict[str, Any]:
    return {
        "provider": provider,
        "requested_provider": provider,
        "primary_provider": primary_provider(),
        "model": model,
        "base_url": "",
        "used_ai": False,
        "cached": True,
    }


def save_article_fields(article_id: str, fields: dict[str, Any]) -> dict[str, Any]:
    return update_article(article_id, lambda article: article.update(fields))


def ai_available(
    provider: str,
    config_overrides: dict[str, Any] | None = None,
    prefer_primary: bool = True,
) -> bool:
    actual_provider = resolve_provider(provider, config_overrides, prefer_primary)
    config = provider_config(actual_provider, config_overrides)
    return bool(OpenAI and config["api_key"])


def call_ai_json(
    provider: str,
    system_prompt: str,
    user_prompt: str,
    fallback: Any,
    config_overrides: dict[str, Any] | None = None,
    prefer_primary: bool = True,
) -> tuple[Any, dict[str, Any]]:
    requested_provider = provider
    provider = resolve_provider(provider, config_overrides, prefer_primary)
    config = provider_config(provider, config_overrides)
    if not ai_available(provider, config_overrides, prefer_primary=False):
        reason = "openai package is not installed" if OpenAI is None else f"{config['api_key_env']} is not configured"
        return fallback, {
            "provider": provider,
            "requested_provider": requested_provider,
            "primary_provider": primary_provider(overrides=config_overrides),
            "model": "local-fallback",
            "base_url": config["base_url"],
            "used_ai": False,
            "error": reason,
        }
    client = OpenAI(api_key=config["api_key"], base_url=config["base_url"])
    try:
        completion = client.chat.completions.create(
            model=config["model"],
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            timeout=90,
        )
        content = completion.choices[0].message.content or "{}"
        return json.loads(content), {
            "provider": provider,
            "requested_provider": requested_provider,
            "primary_provider": primary_provider(overrides=config_overrides),
            "model": config["model"],
            "base_url": config["base_url"],
            "used_ai": True,
        }
    except json.JSONDecodeError as exc:
        fallback_meta = {
            "provider": provider,
            "requested_provider": requested_provider,
            "primary_provider": primary_provider(overrides=config_overrides),
            "model": config["model"],
            "base_url": config["base_url"],
            "used_ai": False,
            "error": f"AI returned non-JSON content: {exc}",
        }
        return fallback, fallback_meta
    except Exception as exc:
        fallback_meta = {
            "provider": provider,
            "requested_provider": requested_provider,
            "primary_provider": primary_provider(overrides=config_overrides),
            "model": config["model"],
            "base_url": config["base_url"],
            "used_ai": False,
            "error": str(exc),
        }
        return fallback, fallback_meta


def coerce_list(value: Any, fallback: list[Any] | None = None) -> list[Any]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return list(fallback or [])
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, dict):
        nested = value.get("items")
        if isinstance(nested, list):
            return nested
        return list(value.values()) if value else list(fallback or [])
    if isinstance(value, str):
        parts = [item.strip() for item in re.split(r"\r?\n|[;；,，]", value) if item.strip()]
        return parts or list(fallback or [])
    return [value]


def coerce_string_list(value: Any, fallback: list[Any] | None = None) -> list[str]:
    items = coerce_list(value, fallback)
    result: list[str] = []
    for item in items:
        if item is None:
            continue
        if isinstance(item, dict):
            text = item.get("term") or item.get("text") or item.get("word") or item.get("note") or json.dumps(item, ensure_ascii=False)
        else:
            text = str(item)
        text = clean_text(text)
        if text:
            result.append(text)
    return result


def coerce_dict(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if isinstance(value, dict):
        merged = dict(fallback)
        merged.update(value)
        return merged
    return dict(fallback)


def normalize_dictation_feedback(result: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    data = coerce_dict(result, fallback)
    return {
        "score": data.get("score", fallback.get("score", "-")),
        "original": clean_text(str(data.get("original") or fallback.get("original") or "")),
        "user_answer": clean_text(str(data.get("user_answer") or fallback.get("user_answer") or "")),
        "missing_words": coerce_string_list(data.get("missing_words"), fallback.get("missing_words", [])),
        "spelling_or_extra": coerce_string_list(data.get("spelling_or_extra"), fallback.get("spelling_or_extra", [])),
        "listening_notes": coerce_string_list(data.get("listening_notes"), fallback.get("listening_notes", [])),
        "why_difficult": clean_text(str(data.get("why_difficult") or fallback.get("why_difficult") or "")),
    }


def normalize_dictation_items(raw_items: Any, article: dict[str, Any], count: int) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    article_full_text = article_text(article)
    raw_list = [raw_items] if isinstance(raw_items, str) else coerce_list(raw_items)
    for item in raw_list[:count]:
        if isinstance(item, str):
            item = {"source": item}
        if not isinstance(item, dict):
            continue
        source = clean_text(str(item.get("source") or item.get("sentence") or item.get("text") or ""))
        if not source:
            continue
        normalized.append({
            "id": str(item.get("id") or stable_id(article["id"], "dictation", source)),
            "source": source,
            "focus": clean_text(str(item.get("focus") or "听主干、功能词、词尾辅音和连读")),
            "rounds": coerce_string_list(item.get("rounds"), ["完整听一遍", "逐句听写", "对照纠错", "跟读模仿"]),
            "from_article": source in article_full_text,
        })
    return normalized


def normalize_reading_questions(raw_questions: Any, article_id: str, fallback: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for item in coerce_list(raw_questions, fallback):
        if isinstance(item, str):
            item = {"question": item}
        if not isinstance(item, dict):
            continue
        question = clean_text(str(item.get("question") or ""))
        if not question:
            continue
        normalized.append({
            "id": str(item.get("id") or stable_id(article_id, question)),
            "question": question,
            "focus": clean_text(str(item.get("focus") or "content and expression")),
            "keywords": coerce_string_list(item.get("keywords"), []),
            "reference_answer": clean_text(str(item.get("reference_answer") or "")),
        })
    return normalized or fallback


def text_check_fallback(article: dict[str, Any]) -> dict[str, Any]:
    clean_result = clean_article_paragraphs(article.get("paragraphs", []))
    cleaned = clean_result["cleaned_paragraphs"]
    issues = [
        {"paragraph": item["index"], "issue": f"删除：{item['reason']}", "suggestion": item["text"][:160]}
        for item in clean_result["removed_paragraphs"]
    ]
    issues.extend(
        {"paragraph": item["index"], "issue": item["reason"], "suggestion": item["after"]}
        for item in clean_result["normalization_notes"]
    )
    return {
        "cleaned_paragraphs": cleaned,
        "removed_paragraphs": clean_result["removed_paragraphs"],
        "normalization_notes": clean_result["normalization_notes"],
        "issues": issues[:20],
        "summary": "已完成文本清洗：删除导航/订阅/来源等无效段落，修正常见断词、首字母空格和标点空格。",
    }


def overview_fallback(article: dict[str, Any]) -> dict[str, Any]:
    text = article_text(article)
    sentences = split_sentences(text)
    keywords = [item["term"] for item in article["stats"].get("top_terms", [])[:8]]
    return {
        "main_idea_zh": f"本文围绕{article['title']}展开，核心内容可从标题、首段和高频关键词入手理解。",
        "main_idea_en": sentences[0] if sentences else "",
        "core_viewpoints": [
            {"zh": "文章提出一个值得分析的现象或问题。", "en": sentences[0] if sentences else ""},
            {"zh": "后文通过事实、解释或对比推进论证。", "en": sentences[min(2, len(sentences) - 1)] if len(sentences) > 2 else ""},
        ],
        "structure": ["引入话题", "解释背景或原因", "展开影响与争议", "形成判断或开放结论"],
        "key_vocabulary": [{"term": k, "translation": MINI_GLOSSARY.get(k, "结合原文理解")} for k in keywords],
        "background_zh": "请结合文章栏目和标题补充相关经济、社会或时事背景。",
        "reading_difficulties_zh": ["注意长句中的插入语、让步关系和因果关系。", "先抓段落主旨，再处理细节。"],
    }


def paragraph_analysis_fallback(article: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for i, paragraph in enumerate(article.get("cleaned_paragraphs", article["paragraphs"])[:16], 1):
        sentences = split_sentences(paragraph)
        lower = paragraph.lower()
        if i == 1:
            function = "引出话题"
        elif any(x in lower for x in ["for example", "such as"]):
            function = "举例说明"
        elif any(x in lower for x in ["however", "but", "yet", "although"]):
            function = "对比转折"
        elif any(x in lower for x in ["because", "therefore", "so", "as a result"]):
            function = "解释原因"
        else:
            function = "展开论证"
        expressions = suggest_vocabulary(paragraph, article["id"])[:4]
        result.append({
            "index": i,
            "main_idea": sentences[0] if sentences else paragraph[:180],
            "function": function,
            "logic": "先识别本段第一句的主题，再观察连接词判断句间关系。",
            "expressions": [e["term"] for e in expressions],
            "writing_template": "Topic sentence + explanation + evidence/comment.",
            "chinese_help": "本段阅读时先判断它在全文中承担的功能，再处理具体信息。",
        })
    return result


def long_sentence_fallback(article: dict[str, Any]) -> list[dict[str, Any]]:
    sentences = split_sentences(article_text(article))
    difficult = sorted(sentences, key=lambda s: (len(words(s)), len(s)), reverse=True)[:8]
    return [analyze_sentence_fallback(sentence, article) for sentence in difficult]


def analyze_sentence_fallback(sentence: str, article: dict[str, Any] | None = None) -> dict[str, Any]:
    token_list = words(sentence)
    main = " ".join(token_list[: min(12, len(token_list))])
    phrases = [w for w in token_list if len(w) >= 8][:6]
    logic = "并列/补充"
    lower = sentence.lower()
    if any(x in lower for x in ["although", "despite", "whereas", "while"]):
        logic = "让步或对比"
    elif any(x in lower for x in ["because", "therefore", "so that", "as a result"]):
        logic = "因果或结果"
    elif any(x in lower for x in ["if", "unless"]):
        logic = "条件"
    modifiers = ["逗号、which/that 从句、介词短语和非谓语结构通常承担修饰或补充说明。"]
    difficult_terms = []
    seen_terms = set()
    for term in token_list:
        key = term.lower().strip("'")
        if key in seen_terms or key in STOPWORDS or len(key) < 6:
            continue
        seen_terms.add(key)
        difficult_terms.append({
            "term": term,
            "meaning": MINI_GLOSSARY.get(key, "结合上下文判断语境义"),
            "note": "先确认词性和搭配，再带回句子理解。",
        })
        if len(difficult_terms) >= 6:
            break
    sentence_structure = {
        "main_clause": main,
        "modifiers": modifiers,
        "logic": logic,
        "reading_order": ["找主语和谓语", "判断连接词", "拆出修饰成分", "整合成自然中文意思"],
    }
    return {
        "sentence": sentence,
        "difficult_vocabulary": difficult_terms,
        "sentence_structure": sentence_structure,
        "translation": "本地模式提示：请先抓主干，再处理修饰成分；配置 DeepSeek/Qwen 后可获得自然中文解释。",
        "core_structure": main,
        "modifiers": modifiers,
        "logic": logic,
        "understanding_order": ["找主语和谓语", "判断连接词", "拆出修饰成分", "整合成自然中文意思"],
        "transferable_expressions": phrases,
        "imitation_task": "Imitate the sentence structure and write one sentence about technology, education or the economy.",
        "imitation_feedback": "",
    }


def vocabulary_fallback(article: dict[str, Any]) -> list[dict[str, Any]]:
    vocab = suggest_vocabulary(article_text(article), article["id"])[:28]
    layers = ["核心必会词", "阅读理解词", "写作可用词", "学术表达词", "熟词僻义词"]
    for i, item in enumerate(vocab):
        item["layer"] = layers[i % len(layers)]
        item["collocations"] = [f"{item['term']} + noun", f"to {item['term']} something"]
        item["synonym_note"] = "配置 AI 后可获得更准确的近义词辨析。"
        item["example"] = item["context"]
        item["imitation_task"] = f"Write one sentence using '{item['term']}' in a similar context."
    return vocab


def reading_questions_fallback(article: dict[str, Any]) -> list[dict[str, Any]]:
    keywords = [item["term"] for item in article["stats"].get("top_terms", [])[:5]]
    questions = [
        "What is the main issue discussed in the article?",
        "What evidence or example does the article use to support its point?",
        "How are the key terms connected in the article?",
        "What is a possible counterargument to the article's view?",
        "What can you learn from the article for your own writing?",
    ]
    return [
        {
            "id": stable_id(article["id"], q),
            "question": q,
            "focus": "content and expression",
            "keywords": keywords,
            "reference_answer": "",
        }
        for q in questions
    ]


def grade_answer_fallback(question: str, answer: str) -> dict[str, Any]:
    word_count = len(words(answer))
    score = min(20, max(6, word_count // 4 + 8))
    return {
        "score": score,
        "content": "答案长度和基本回应已检查；配置 Qwen 后可获得更精细的内容判断。",
        "logic": "建议使用 first/however/therefore 等连接词组织回答。",
        "grammar": "本地模式不做深度语法纠错。",
        "vocabulary": "尝试复用文章关键词和表达。",
        "improved_answer": answer if answer else "A stronger answer should directly address the question and cite one detail from the article.",
        "reference_answer": "参考答案会在 AI 模式下结合文章内容生成。",
    }


def dictation_feedback_fallback(source: str, answer: str) -> dict[str, Any]:
    source_words = words(source.lower())
    answer_words = words(answer.lower())
    matcher = difflib.SequenceMatcher(None, answer_words, source_words)
    missing = []
    extra = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in {"replace", "insert"}:
            missing.extend(source_words[j1:j2])
        if tag in {"replace", "delete"}:
            extra.extend(answer_words[i1:i2])
    similarity = round(matcher.ratio() * 100)
    return {
        "score": similarity,
        "original": source,
        "user_answer": answer,
        "missing_words": missing[:20],
        "spelling_or_extra": extra[:20],
        "listening_notes": ["注意功能词、弱读和词尾辅音。", "第二遍逐句听时先抓重读实词。"],
        "why_difficult": "长句中弱读词、连读和相似音容易造成漏听。",
    }


def dictation_items_fallback(article: dict[str, Any], count: int = 6) -> list[dict[str, Any]]:
    candidates = []
    for sentence in split_sentences(article_text(article)):
        word_count = len(words(sentence))
        if 8 <= word_count <= 34:
            candidates.append((abs(word_count - 18), sentence))
    if not candidates:
        candidates = [(0, sentence) for sentence in split_sentences(article_text(article))[:count]]
    selected = [sentence for _, sentence in sorted(candidates, key=lambda item: (item[0], len(item[1])))[:count]]
    return [
        {
            "id": stable_id(article["id"], "dictation", sentence),
            "source": sentence,
            "focus": "听主干、功能词、词尾辅音和连读",
            "rounds": ["完整听一遍", "逐句听写", "对照纠错", "跟读模仿"],
        }
        for sentence in selected
    ]


def dashscope_generation_url(base_url: str) -> str:
    return f"{normalize_dashscope_api_url(base_url)}/services/aigc/multimodal-generation/generation"


def synthesize_qwen_speech(text: str, voice: str | None = None, language_type: str | None = None) -> dict[str, Any]:
    config = qwen_tts_config()
    if not config["api_key"]:
        raise HTTPException(400, f"{config['api_key_env']} 未配置，已无法调用 Qwen 朗读。")
    clean = clean_text(text)
    if not clean:
        raise HTTPException(400, "朗读文本不能为空。")
    payload = {
        "model": config["model"],
        "input": {
            "text": truncate_text(clean, 1800),
            "voice": voice or config["voice"],
            "language_type": language_type or config["language_type"],
        },
    }
    try:
        response = requests.post(
            dashscope_generation_url(config["base_url"]),
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=90,
        )
    except requests.RequestException as exc:
        raise HTTPException(502, f"Qwen 朗读请求失败：{exc}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(response.status_code or 502, "Qwen 朗读返回了非 JSON 响应。") from exc
    if response.status_code >= 400 or data.get("status_code", 200) >= 400:
        raise HTTPException(response.status_code or 502, data.get("message") or data.get("code") or "Qwen 朗读失败。")
    audio = ((data.get("output") or {}).get("audio") or {})
    audio_url = audio.get("url")
    audio_data = audio.get("data")
    if not audio_url and not audio_data:
        raise HTTPException(502, "Qwen 朗读没有返回音频。")
    return {
        "audio_url": audio_url,
        "audio_data": audio_data,
        "mime_type": "audio/wav",
        "meta": {
            "provider": "qwen",
            "model": config["model"],
            "voice": payload["input"]["voice"],
            "language_type": payload["input"]["language_type"],
            "base_url": config["base_url"],
            "used_ai": True,
            "request_id": data.get("request_id"),
        },
    }


def _parse_minimax_subtitles(raw: Any) -> list[dict[str, Any]]:
    """Normalise a MiniMax subtitle array into the same shape as ASR sentences:
    [{"text", "begin_ms", "end_ms", "words": []}]. Field names vary across
    MiniMax responses, so accept the common aliases defensively."""
    entries = raw
    if isinstance(raw, dict):
        entries = raw.get("subtitles") or raw.get("data") or raw.get("result") or []
    if not isinstance(entries, list):
        return []
    sentences: list[dict[str, Any]] = []
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        text = clean_text(str(entry.get("text") or entry.get("sentence") or ""))
        begin = _time_value(entry, "time_begin", "begin_time", "start_time", "begin", "start")
        end = _time_value(entry, "time_end", "end_time", "finish_time", "end", "stop")
        if text and begin is not None and end is not None and end > begin:
            sentences.append({"text": text, "begin_ms": begin, "end_ms": end, "words": []})
    return sentences


def _minimax_t2a(
    text: str,
    config: dict[str, Any],
    subtitle_enable: bool = False,
) -> tuple[bytes, list[dict[str, Any]], dict[str, Any]]:
    """Call MiniMax T2A v2 and return (wav_bytes, subtitles, meta).

    Audio is requested as WAV (hex output) so it concatenates with the existing
    WAV pipeline. When ``subtitle_enable`` is set, MiniMax returns a
    ``subtitle_file`` URL whose JSON gives sentence-level timestamps."""
    if not config["api_key"]:
        raise HTTPException(400, f"{config['api_key_env']} 未配置，无法调用 MiniMax 朗读。")
    clean = clean_text(text)
    if not clean:
        raise HTTPException(400, "朗读文本不能为空。")
    try:
        speed = float(config.get("speed") or 1.0)
    except (TypeError, ValueError):
        speed = 1.0
    url = f"{config['base_url']}/t2a_v2"
    if config.get("group_id"):
        url = f"{url}?GroupId={config['group_id']}"
    payload: dict[str, Any] = {
        "model": config["model"],
        "text": truncate_text(clean, config.get("max_chars", 9000)),
        "stream": False,
        "output_format": "hex",
        "voice_setting": {"voice_id": config["voice"], "speed": speed},
        "audio_setting": {"sample_rate": 32000, "format": "wav", "channel": 1},
        "subtitle_enable": bool(subtitle_enable),
    }
    if config.get("language_boost"):
        payload["language_boost"] = config["language_boost"]
    try:
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=180,
        )
    except requests.RequestException as exc:
        raise HTTPException(502, f"MiniMax 朗读请求失败：{exc}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        snippet = (response.text or "").strip().replace("\n", " ")[:200]
        raise HTTPException(
            response.status_code or 502,
            f"MiniMax 朗读返回了非 JSON 响应（HTTP {response.status_code}，URL {url}）。"
            f"通常是 Base URL 与账号区域不匹配——国际站用 https://api.minimax.io/v1，"
            f"国内站用 https://api.minimaxi.com/v1。响应片段：{snippet}",
        ) from exc
    base_resp = data.get("base_resp") or {}
    if response.status_code >= 400 or int(base_resp.get("status_code", 0)) != 0:
        raise HTTPException(
            response.status_code or 502,
            base_resp.get("status_msg") or "MiniMax 朗读失败。",
        )
    block = data.get("data") or {}
    audio_hex = block.get("audio")
    if not audio_hex:
        raise HTTPException(502, "MiniMax 朗读没有返回音频。")
    try:
        audio_bytes = bytes.fromhex(audio_hex)
    except ValueError as exc:
        raise HTTPException(502, "MiniMax 返回的音频无法解码。") from exc

    subtitles: list[dict[str, Any]] = []
    if subtitle_enable:
        subtitle_file = block.get("subtitle_file")
        if subtitle_file:
            try:
                sub_resp = requests.get(subtitle_file, timeout=60)
                sub_resp.raise_for_status()
                subtitles = _parse_minimax_subtitles(sub_resp.json())
            except (requests.RequestException, ValueError):
                subtitles = []
        elif block.get("subtitles"):
            subtitles = _parse_minimax_subtitles(block.get("subtitles"))

    meta = {
        "provider": "minimax",
        "model": config["model"],
        "voice": config["voice"],
        "base_url": config["base_url"],
        "used_ai": True,
        "trace_id": data.get("trace_id"),
    }
    return audio_bytes, subtitles, meta


def synthesize_minimax_speech(
    text: str,
    voice: str | None = None,
    language_boost: str | None = None,
) -> dict[str, Any]:
    config = minimax_tts_config()
    if voice:
        config = {**config, "voice": voice.strip() or config["voice"]}
    if language_boost:
        config = {**config, "language_boost": language_boost.strip() or config["language_boost"]}
    audio_bytes, subtitles, meta = _minimax_t2a(text, config)
    return {
        "audio_url": None,
        "audio_data": base64.b64encode(audio_bytes).decode("ascii"),
        "mime_type": "audio/wav",
        "subtitles": subtitles,
        "meta": meta,
    }


def generate_beep_wav(
    frequency: int = 520,
    duration: float = 0.45,
    sample_rate: int = 24000,
    channels: int = 1,
    sample_width: int = 2,
    volume: float = 0.22,
) -> bytes:
    """Generate a gentle sine-wave tone as WAV bytes matching the given audio format."""
    n = int(sample_rate * duration)
    fade = max(1, int(sample_rate * 0.04))
    max_val = (1 << (sample_width * 8 - 1)) - 1
    frames = bytearray()
    for i in range(n):
        s = math.sin(2 * math.pi * frequency * i / sample_rate)
        if i < fade:
            s *= i / fade
        elif i > n - fade:
            s *= (n - i) / fade
        val = max(-max_val - 1, min(max_val, int(s * volume * max_val)))
        sample = struct.pack("<h" if sample_width == 2 else "b", val)
        frames += sample * channels
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sample_width)
        w.setframerate(sample_rate)
        w.writeframes(bytes(frames))
    return buf.getvalue()


def concat_wav(wav1: bytes, wav2: bytes, silence_ms: int = 380) -> bytes:
    """Concatenate two WAV files with a short silence gap between them."""
    buf2 = io.BytesIO(wav2)
    with wave.open(buf2, "rb") as w2:
        rate, chans, width = w2.getframerate(), w2.getnchannels(), w2.getsampwidth()
        pcm2 = w2.readframes(w2.getnframes())
    buf1 = io.BytesIO(wav1)
    with wave.open(buf1, "rb") as w1:
        pcm1 = w1.readframes(w1.getnframes())
    silence = b"\x00" * (int(rate * silence_ms / 1000) * chans * width)
    out = io.BytesIO()
    with wave.open(out, "wb") as wo:
        wo.setnchannels(chans)
        wo.setsampwidth(width)
        wo.setframerate(rate)
        wo.writeframes(pcm1 + silence + pcm2)
    return out.getvalue()


def concat_wav_many(chunks: list[bytes], silence_ms: int = 240) -> tuple[bytes, list[int]]:
    """Concatenate WAV chunks with a silence gap between them.

    Returns ``(audio_bytes, offsets_ms)`` where ``offsets_ms[c]`` is the exact start
    time of chunk ``c`` inside the concatenated audio. These offsets are drift-free
    anchors (they come from sample counts, not ASR) for the listening timeline.
    The audio bytes are identical to the previous behaviour.
    """
    if not chunks:
        raise HTTPException(400, "没有可拼接的音频。")
    if len(chunks) == 1:
        return chunks[0], [0]
    pcm_parts: list[bytes] = []
    rate = chans = width = 0
    for i, chunk in enumerate(chunks):
        with wave.open(io.BytesIO(chunk), "rb") as wav_file:
            chunk_rate = wav_file.getframerate()
            chunk_chans = wav_file.getnchannels()
            chunk_width = wav_file.getsampwidth()
            if i == 0:
                rate, chans, width = chunk_rate, chunk_chans, chunk_width
            elif (chunk_rate, chunk_chans, chunk_width) != (rate, chans, width):
                raise HTTPException(502, "TTS 返回的音频格式不一致，无法拼接。")
            pcm_parts.append(wav_file.readframes(wav_file.getnframes()))
    silence_frames = int(rate * silence_ms / 1000)
    silence = b"\x00" * (silence_frames * chans * width)
    bytes_per_frame = max(1, chans * width)

    offsets_ms: list[int] = []
    acc_frames = 0
    for part in pcm_parts:
        offsets_ms.append(int(acc_frames / rate * 1000) if rate else 0)
        acc_frames += len(part) // bytes_per_frame + silence_frames

    out = io.BytesIO()
    with wave.open(out, "wb") as wo:
        wo.setnchannels(chans)
        wo.setsampwidth(width)
        wo.setframerate(rate)
        wo.writeframes(silence.join(pcm_parts))
    return out.getvalue(), offsets_ms


def wav_duration_ms(audio_bytes: bytes) -> int:
    with wave.open(io.BytesIO(audio_bytes), "rb") as wav_file:
        rate = wav_file.getframerate()
        frames = wav_file.getnframes()
    return int(frames / rate * 1000) if rate else 0


def speech_result_to_bytes(tts: dict[str, Any]) -> bytes:
    if tts.get("audio_data"):
        return base64.b64decode(tts["audio_data"])
    if tts.get("audio_url"):
        try:
            r = requests.get(tts["audio_url"], timeout=60)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise HTTPException(502, f"下载 Qwen 朗读音频失败：{exc}") from exc
        return r.content
    raise HTTPException(502, "Qwen 未返回音频。")


def listening_sentence_items(article: dict[str, Any]) -> list[dict[str, Any]]:
    paragraphs = article.get("cleaned_paragraphs") or article.get("paragraphs") or []
    items: list[dict[str, Any]] = []
    for para_idx, paragraph in enumerate(paragraphs):
        for sentence in split_sentences(paragraph):
            items.append({"index": len(items), "para": para_idx, "text": sentence})
    return items


def chunk_sentences_for_tts(items: list[dict[str, Any]], max_chars: int = 1450) -> list[list[dict[str, Any]]]:
    """Group whole sentences into ≤max_chars chunks. Returns the sentence items per
    chunk (not joined text) so callers can map each chunk back to its sentences for
    timeline anchoring. Use ``chunk_text`` to get the string fed to TTS."""
    chunks: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_len = 0
    for item in items:
        sentence = clean_text(item["text"])
        if not sentence:
            continue
        extra = len(sentence) + (1 if current else 0)
        if current and current_len + extra > max_chars:
            chunks.append(current)
            current = [item]
            current_len = len(sentence)
        else:
            current.append(item)
            current_len += extra
    if current:
        chunks.append(current)
    return chunks


def chunk_text(group: list[dict[str, Any]]) -> str:
    return " ".join(filter(None, (clean_text(item["text"]) for item in group)))


def aligned_audio_cache_key(
    article_id: str,
    items: list[dict[str, Any]],
    voice: str,
    language_type: str,
    tts_model: str,
    asr_model: str,
    enable_words: bool = True,
) -> str:
    text_hash = stable_id(*(item["text"] for item in items[:300]), str(len(items)))
    payload = f"{article_id}|{text_hash}|{voice}|{language_type}|{tts_model}|{asr_model}|words:{int(enable_words)}|v4"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def oss_bucket_client() -> tuple[Any, dict[str, str]]:
    config = oss_config()
    missing = [key for key in ("access_key_id", "access_key_secret", "bucket", "endpoint") if not config[key]]
    if missing:
        raise HTTPException(400, f"OSS 配置不完整，请检查：{', '.join(missing)}")
    if oss2 is None:
        raise HTTPException(500, "缺少 oss2 依赖，请重新安装 requirements.txt。")
    auth = oss2.Auth(config["access_key_id"], config["access_key_secret"])
    return oss2.Bucket(auth, config["endpoint"], config["bucket"]), config


def upload_temp_audio_to_oss(audio_bytes: bytes, object_key: str, mime_type: str = "audio/wav", expires: int = 1800) -> tuple[Any, str]:
    bucket, config = oss_bucket_client()
    headers = {"Content-Type": mime_type}
    try:
        bucket.put_object(object_key, audio_bytes, headers=headers)
        signed_url = bucket.sign_url("GET", object_key, expires)
    except Exception as exc:
        raise HTTPException(502, f"上传音频到 OSS 失败：{exc}") from exc
    return bucket, signed_url


def upload_temp_audio_file_to_oss(path: Path, object_key: str, mime_type: str, expires: int = 3600) -> tuple[Any, str]:
    bucket, _config = oss_bucket_client()
    try:
        bucket.put_object_from_file(object_key, str(path), headers={"Content-Type": mime_type})
        signed_url = bucket.sign_url("GET", object_key, expires)
    except Exception as exc:
        raise HTTPException(502, f"上传原版音频到 OSS 失败：{exc}") from exc
    return bucket, signed_url


def dashscope_asr_url(base_url: str) -> str:
    return f"{normalize_dashscope_api_url(base_url)}/services/audio/asr/transcription"


def dashscope_task_url(base_url: str, task_id: str) -> str:
    return f"{normalize_dashscope_api_url(base_url)}/tasks/{task_id}"


def start_qwen_filetranscription(audio_url: str, enable_words: bool = False) -> tuple[str, dict[str, Any]]:
    config = qwen_asr_config()
    if not config["api_key"]:
        raise HTTPException(400, f"{config['api_key_env']} 未配置，无法调用 Qwen ASR。")
    if config["model"].startswith("qwen3-asr-flash-filetrans"):
        input_payload = {"file_url": audio_url}
        parameters: dict[str, Any] = {
            "channel_id": [0],
            "enable_itn": False,
            "enable_words": bool(enable_words),
        }
    else:
        input_payload = {"file_urls": [audio_url]}
        parameters = {
            "language_hints": ["en"],
        }
        parameters["timestamp_alignment_enabled"] = True
        if enable_words:
            parameters["disfluency_removal_enabled"] = False
    payload = {
        "model": config["model"],
        "input": input_payload,
        "parameters": parameters,
    }
    try:
        response = requests.post(
            dashscope_asr_url(config["base_url"]),
            headers={
                "Authorization": f"Bearer {config['api_key']}",
                "Content-Type": "application/json",
                "X-DashScope-Async": "enable",
            },
            json=payload,
            timeout=60,
        )
    except requests.RequestException as exc:
        raise HTTPException(502, f"Qwen ASR 请求失败：{exc}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(response.status_code or 502, "Qwen ASR 返回了非 JSON 响应。") from exc
    if response.status_code >= 400 or data.get("code"):
        raise HTTPException(response.status_code or 502, data.get("message") or data.get("code") or "Qwen ASR 创建任务失败。")
    task_id = ((data.get("output") or {}).get("task_id") or data.get("task_id") or "").strip()
    if not task_id:
        raise HTTPException(502, "Qwen ASR 没有返回 task_id。")
    return task_id, {"provider": "qwen", "model": config["model"], "base_url": config["base_url"], "request_id": data.get("request_id")}


def poll_qwen_asr_task(
    task_id: str,
    base_url: str,
    api_key: str,
    timeout_seconds: int = 180,
    on_tick: Callable[[int, str], None] | None = None,
) -> dict[str, Any]:
    started = time.time()
    deadline = started + timeout_seconds
    last_data: dict[str, Any] = {}
    while time.time() < deadline:
        try:
            response = requests.get(
                dashscope_task_url(base_url, task_id),
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "X-DashScope-Async": "enable",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise HTTPException(502, f"查询 Qwen ASR 任务失败：{exc}") from exc
        last_data = data
        output = data.get("output") or {}
        status = str(output.get("task_status") or data.get("task_status") or "").upper()
        if status in {"SUCCEEDED", "SUCCESS"}:
            return data
        if status in {"FAILED", "CANCELED", "CANCELLED"}:
            message = output.get("message") or data.get("message") or "Qwen ASR 任务失败。"
            raise HTTPException(502, message)
        if on_tick is not None:
            try:
                on_tick(int(time.time() - started), status or "PENDING")
            except Exception:
                pass
        time.sleep(2)
    raise HTTPException(504, f"Qwen ASR 转写超时：{json.dumps(last_data, ensure_ascii=False)[:300]}")


def fetch_qwen_transcription(task_result: dict[str, Any]) -> dict[str, Any]:
    output = task_result.get("output") or {}
    result = output.get("result") or {}
    results = output.get("results") or []
    transcription_url = ""
    if isinstance(result, dict):
        transcription_url = str(result.get("transcription_url") or "")
    if results and isinstance(results[0], dict):
        transcription_url = transcription_url or str(results[0].get("transcription_url") or "")
    transcription_url = transcription_url or str(output.get("transcription_url") or "")
    if not transcription_url:
        raise HTTPException(502, "Qwen ASR 任务完成，但没有返回 transcription_url。")
    try:
        response = requests.get(transcription_url, timeout=60)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise HTTPException(502, f"读取 Qwen ASR 转写结果失败：{exc}") from exc


def _time_value(item: dict[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, (int, float)):
            return int(value)
        if isinstance(value, str) and value.strip().replace(".", "", 1).isdigit():
            return int(float(value))
    return None


def extract_asr_sentences(node: Any) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    if isinstance(node, dict):
        sentences = node.get("sentences")
        if isinstance(sentences, list):
            for sentence in sentences:
                if not isinstance(sentence, dict):
                    continue
                text = clean_text(str(sentence.get("text") or sentence.get("sentence") or ""))
                begin = _time_value(sentence, "begin_time", "start_time", "begin", "start")
                end = _time_value(sentence, "end_time", "finish_time", "end", "stop")
                if text and begin is not None and end is not None and end > begin:
                    found.append({
                        "text": text,
                        "begin_ms": begin,
                        "end_ms": end,
                        "words": sentence.get("words") if isinstance(sentence.get("words"), list) else [],
                    })
        for value in node.values():
            if isinstance(value, (dict, list)):
                found.extend(extract_asr_sentences(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(extract_asr_sentences(value))
    deduped: list[dict[str, Any]] = []
    seen = set()
    for sentence in sorted(found, key=lambda item: (item["begin_ms"], item["end_ms"])):
        key = (sentence["begin_ms"], sentence["end_ms"], sentence["text"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(sentence)
    return deduped


def normalize_for_alignment(text: str) -> str:
    text = clean_text(text).lower()
    text = re.sub(r"[^a-z0-9'\s]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _asr_word_timings(asr_sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    word_items: list[dict[str, Any]] = []
    for sentence in asr_sentences:
        for word in sentence.get("words") or []:
            if not isinstance(word, dict):
                continue
            text = clean_text(str(word.get("text") or word.get("word") or ""))
            norm = normalize_for_alignment(text)
            begin = _time_value(word, "begin_time", "start_time", "begin", "start")
            end = _time_value(word, "end_time", "finish_time", "end", "stop")
            if not text or not norm or begin is None or end is None or end <= begin:
                continue
            word_items.append({
                "text": text,
                "norm": norm,
                "begin_ms": begin,
                "end_ms": end,
                "raw": word,
            })
    return sorted(word_items, key=lambda item: (item["begin_ms"], item["end_ms"]))


def align_asr_words_to_original(items: list[dict[str, Any]], asr_sentences: list[dict[str, Any]]) -> list[dict[str, Any]]:
    word_items = _asr_word_timings(asr_sentences)
    if not word_items:
        return []

    alignments: list[dict[str, Any]] = []
    cursor = 0
    for item in items:
        original_norm = normalize_for_alignment(item["text"])
        original_words = original_norm.split()
        if not original_words or cursor >= len(word_items):
            alignments.append({
                "index": item["index"],
                "para": item["para"],
                "text": item["text"],
                "asr_text": "",
                "begin_ms": None,
                "end_ms": None,
                "confidence": 0.0,
                "words": [],
            })
            continue

        expected = len(original_words)
        min_end = cursor + max(1, expected - 8)
        max_end = min(len(word_items), cursor + expected + 10)
        min_end = min(min_end, max_end)
        best: tuple[float, int, str] | None = None
        for end in range(cursor + 1, max_end + 1):
            if end < min_end:
                continue
            candidate = " ".join(w["norm"] for w in word_items[cursor:end])
            score = difflib.SequenceMatcher(None, original_norm, candidate).ratio()
            if best is None or score > best[0]:
                best = (score, end, candidate)

        if best is None:
            end = min(len(word_items), cursor + expected)
            score = 0.0
        else:
            score, end, _candidate = best

        selected = word_items[cursor:end]
        cursor = end
        alignments.append({
            "index": item["index"],
            "para": item["para"],
            "text": item["text"],
            "asr_text": " ".join(w["text"] for w in selected),
            "begin_ms": selected[0]["begin_ms"] if selected else None,
            "end_ms": selected[-1]["end_ms"] if selected else None,
            "confidence": round(score, 3),
            "words": [w["raw"] for w in selected],
        })
    return alignments


def _empty_alignment(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": item["index"],
        "para": item["para"],
        "text": item["text"],
        "asr_text": "",
        "begin_ms": None,
        "end_ms": None,
        "confidence": 0.0,
        "words": [],
    }


def _chunk_for_time(windows: list[tuple[int, int]], ms: int) -> int:
    for c, (start, end) in enumerate(windows):
        if start <= ms < end:
            return c
    if windows and ms < windows[0][0]:
        return 0
    return max(0, len(windows) - 1)


def _fill_word_times_window(wb: list[Any], we: list[Any], start: int, end: int) -> None:
    """Fill missing (None) per-word times by linear interpolation, clamped to
    [start, end]. Begins become monotonic and each word's end is the next word's
    begin (contiguous), so the highlight never sits in a dead zone."""
    n = len(wb)
    if n == 0:
        return
    span = max(1, end - start)
    known = [k for k in range(n) if wb[k] is not None]
    t: list[float] = [0.0] * n
    if not known:
        for k in range(n):
            t[k] = start + span * k / n
    else:
        for k in known:
            t[k] = float(wb[k])
        first = known[0]
        for k in range(first):
            t[k] = start + (t[first] - start) * (k / first if first else 0)
        for a, b in zip(known, known[1:]):
            for k in range(a + 1, b):
                t[k] = t[a] + (t[b] - t[a]) * ((k - a) / (b - a))
        last = known[-1]
        for k in range(last + 1, n):
            t[k] = t[last] + (end - t[last]) * ((k - last) / (n - last))
    for k in range(n):
        wb[k] = int(min(max(t[k], start), end))
    for k in range(n):
        nxt = t[k + 1] if k + 1 < n else end
        we[k] = int(min(max(nxt, wb[k] + 1), end))


def _align_group(group: list[dict[str, Any]], words: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    """Align one TTS chunk's sentences against the ASR words inside its audio window.
    difflib's equal-blocks give exact word anchors; gaps are interpolated within the
    window, so a sentence can never escape its chunk."""
    tokens: list[str] = []
    counts: list[int] = []
    for item in group:
        toks = normalize_for_alignment(item["text"]).split()
        counts.append(len(toks))
        tokens.extend(toks)
    n = len(tokens)
    if n == 0:
        return [_empty_alignment(item) for item in group]

    wb: list[Any] = [None] * n
    we: list[Any] = [None] * n
    hit = [False] * n
    if words:
        sm = difflib.SequenceMatcher(None, tokens, [w["norm"] for w in words], autojunk=False)
        for tag, i1, i2, j1, j2 in sm.get_opcodes():
            if tag != "equal":
                continue
            for k in range(i2 - i1):
                wb[i1 + k] = words[j1 + k]["begin_ms"]
                we[i1 + k] = words[j1 + k]["end_ms"]
                hit[i1 + k] = True
    _fill_word_times_window(wb, we, start, end)

    out: list[dict[str, Any]] = []
    pos = 0
    for ci, item in enumerate(group):
        cnt = counts[ci]
        if cnt == 0:
            out.append(_empty_alignment(item))
            continue
        seg = range(pos, pos + cnt)
        begin = min(wb[k] for k in seg)
        finish = max(we[k] for k in seg)
        hits = sum(1 for k in seg if hit[k])
        pos += cnt
        out.append({
            "index": item["index"],
            "para": item["para"],
            "text": item["text"],
            "asr_text": "",
            "begin_ms": int(begin),
            "end_ms": int(max(finish, begin + 1)),
            "confidence": round(hits / cnt, 3),
            "words": [],
        })
    return out


def _enforce_monotonic(alignments: list[dict[str, Any]], total_ms: int) -> None:
    prev_end = 0
    for a in alignments:
        if a.get("begin_ms") is None:
            continue
        begin = max(int(a["begin_ms"]), prev_end)
        end = int(a["end_ms"]) if a.get("end_ms") is not None else begin + 1
        if total_ms:
            begin = min(begin, max(0, total_ms - 1))
            end = min(end, total_ms)
        if end <= begin:
            end = begin + 1
        a["begin_ms"], a["end_ms"] = begin, end
        prev_end = end


def align_with_chunk_anchors(
    items: list[dict[str, Any]],
    asr_sentences: list[dict[str, Any]],
    chunk_groups: list[list[dict[str, Any]]],
    chunk_offsets_ms: list[int],
    total_ms: int,
) -> list[dict[str, Any]]:
    """Drift-free sentence timeline. Each TTS chunk owns an exact audio window
    [offset_c, offset_{c+1}); ASR words are bucketed into their chunk by time and
    aligned within that window. A local ASR error can never push a sentence outside
    its chunk, so the highlight cannot drift across the article."""
    word_items = _asr_word_timings(asr_sentences)
    if not word_items or not chunk_groups:
        return []

    windows: list[tuple[int, int]] = []
    for c in range(len(chunk_groups)):
        start = chunk_offsets_ms[c] if c < len(chunk_offsets_ms) else 0
        end = chunk_offsets_ms[c + 1] if c + 1 < len(chunk_offsets_ms) else total_ms
        if end <= start:
            end = start + 1
        windows.append((start, end))

    buckets: list[list[dict[str, Any]]] = [[] for _ in chunk_groups]
    for w in word_items:
        buckets[_chunk_for_time(windows, w["begin_ms"])].append(w)

    by_index: dict[int, dict[str, Any]] = {}
    for c, group in enumerate(chunk_groups):
        start, end = windows[c]
        for a in _align_group(group, buckets[c], start, end):
            by_index[a["index"]] = a

    out = [by_index.get(item["index"]) or _empty_alignment(item) for item in items]
    _enforce_monotonic(out, total_ms)
    return out


def align_asr_to_original(
    items: list[dict[str, Any]],
    asr_sentences: list[dict[str, Any]],
    chunk_groups: list[list[dict[str, Any]]] | None = None,
    chunk_offsets_ms: list[int] | None = None,
    total_ms: int | None = None,
) -> list[dict[str, Any]]:
    if chunk_groups and chunk_offsets_ms and total_ms:
        anchored = align_with_chunk_anchors(items, asr_sentences, chunk_groups, chunk_offsets_ms, total_ms)
        if anchored:
            return anchored
    word_alignments = align_asr_words_to_original(items, asr_sentences)
    valid_word_alignments = [
        item for item in word_alignments
        if item.get("begin_ms") is not None and item.get("end_ms") is not None
    ]
    if valid_word_alignments and len(valid_word_alignments) >= max(1, int(len(items) * 0.65)):
        return word_alignments

    alignments: list[dict[str, Any]] = []
    j = 0
    for item in items:
        original_norm = normalize_for_alignment(item["text"])
        best: tuple[float, int, str] | None = None
        for end_idx in range(j, min(len(asr_sentences), j + 4)):
            combined = " ".join(s["text"] for s in asr_sentences[j:end_idx + 1])
            score = difflib.SequenceMatcher(None, original_norm, normalize_for_alignment(combined)).ratio()
            if best is None or score > best[0]:
                best = (score, end_idx, combined)
        if best and j < len(asr_sentences):
            score, end_idx, asr_text = best
            begin_ms = asr_sentences[j]["begin_ms"]
            end_ms = asr_sentences[end_idx]["end_ms"]
            words_out = []
            for sent in asr_sentences[j:end_idx + 1]:
                for word in sent.get("words") or []:
                    if isinstance(word, dict):
                        words_out.append(word)
            j = end_idx + 1
        else:
            score, asr_text, begin_ms, end_ms, words_out = 0.0, "", None, None, []
        alignments.append({
            "index": item["index"],
            "para": item["para"],
            "text": item["text"],
            "asr_text": asr_text,
            "begin_ms": begin_ms,
            "end_ms": end_ms,
            "confidence": round(score, 3),
            "words": words_out,
        })
    return alignments


def writing_feedback_fallback(task: str, content: str, article: dict[str, Any]) -> dict[str, Any]:
    article_terms = [item["term"] for item in article["stats"].get("top_terms", [])[:8]]
    used = [t for t in article_terms if re.search(rf"\b{re.escape(t)}\b", content, re.I)]
    word_count = len(words(content))
    return {
        "score": min(20, max(6, word_count // 8 + len(used) + 8)),
        "content": "内容需要围绕任务直接回答，并引用文章中的事实或观点。",
        "structure": "建议采用 topic sentence + evidence + comment 的结构。",
        "grammar": "本地模式不做逐句语法纠错；配置 Qwen 后会提供细化批改。",
        "vocabulary": f"已使用文章词汇：{', '.join(used) if used else '暂无'}。",
        "improved_version": content,
        "next_step": "补充一个具体例子，并使用至少三个文章关键词。",
    }


def vocab_sentence_feedback_fallback(term: str, sentence: str) -> dict[str, Any]:
    used = bool(re.search(rf"\b{re.escape(term)}\b", sentence, re.I))
    return {
        "naturalness": "基本可接受" if used else "需要包含目标词",
        "issue": "" if used else f"句子中没有使用 {term}。",
        "improved_sentence": sentence if used else f"Please write a sentence using {term}.",
        "usage_tip": "注意目标词的搭配和语境，不要只按中文意思硬套。",
    }


def suggest_vocabulary(text: str, article_id: str) -> list[dict[str, Any]]:
    token_list = [w.lower().strip("-'") for w in words(text)]
    candidates = [w for w in token_list if len(w) >= 7 and w not in STOPWORDS and not w.endswith("'s")]
    sentences = split_sentences(text)
    vocab = []
    seen = set()
    for term, count in Counter(candidates).most_common(100):
        if term in seen:
            continue
        seen.add(term)
        context = next((s for s in sentences if re.search(rf"\b{re.escape(term)}\b", s, re.I)), "")
        vocab.append({
            "id": stable_id(article_id, term),
            "term": term,
            "translation": MINI_GLOSSARY.get(term, "结合原文语境理解"),
            "kind": "word",
            "frequency": count,
            "context": context,
            "source": "auto",
        })
    return vocab


def make_pack(article: dict[str, Any]) -> dict[str, Any]:
    overview = overview_fallback(article)
    paragraphs = paragraph_analysis_fallback(article)
    difficult = long_sentence_fallback(article)
    vocabulary = vocabulary_fallback(article)
    questions = reading_questions_fallback(article)
    return {
        "article_id": article["id"],
        "generated_at": now_iso(),
        "overview": overview,
        "paragraphs": paragraphs,
        "long_sentences": difficult,
        "vocabulary": vocabulary,
        "reading_questions": questions,
        "dictation_items": [
            {
                "id": stable_id(article["id"], item["sentence"]),
                "source": item["sentence"],
                "rounds": ["整体听", "逐句听", "对照纠错", "跟读模仿"],
            }
            for item in difficult[:6]
        ],
        "speaking_tasks": [
            "Give a 30-second summary of the article.",
            "Explain the author's argument in two points.",
            "State your own view and support it with one example.",
        ],
        "writing_tasks": [
            "Write an 80-word neutral summary.",
            "Write a 150-word response using at least three article expressions.",
            "Imitate one paragraph structure from the article.",
        ],
        "study_route": [
            {"stage": "文本检查", "minutes": 3, "task": "对比原文和 AI 清洗版。"},
            {"stage": "文章总览", "minutes": 8, "task": "建立主旨、结构和背景框架。"},
            {"stage": "段落分析", "minutes": 15, "task": "逐段判断功能和逻辑关系。"},
            {"stage": "句法词汇", "minutes": 18, "task": "突破长难句，积累可迁移表达。"},
            {"stage": "听写输出", "minutes": 18, "task": "完成听写、口语复述和写作反馈。"},
        ],
    }


SYSTEM_TEACHER = (
    "你是一名英语专业教授、资深英语教学专家和AI学习产品教练。"
    "你的输出必须是JSON对象，不要输出Markdown。所有解释面向中文母语的中高级英语学习者，"
    "重点是提升阅读理解、词汇掌握、句法分析、段落逻辑、听写和写作输出能力。"
)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/config")
def get_config() -> dict[str, Any]:
    return settings_response()


def provider_public_config(name: str) -> dict[str, Any]:
    config = provider_config(name)
    result = {
        "configured": bool(config["api_key"]),
        "model": config["model"],
        "base_url": config["base_url"],
        "api_key_env": config["api_key_env"],
        "api_key_masked": mask_secret(config["api_key"]),
    }
    if name == "qwen":
        tts = qwen_tts_config()
        asr = qwen_asr_config()
        settings = load_settings()
        dashscope_key = (
            (settings.get("dashscope_api_key") or "").strip()
            or (os.getenv("DASHSCOPE_API_KEY") or "").strip()
        )
        result["image_model"] = settings.get("qwen_image_model", "qwen-vl-plus")
        result["tts"] = {
            "configured": bool(tts["api_key"]),
            "model": tts["model"],
            "voice": tts["voice"],
            "language_type": tts["language_type"],
            "base_url": tts["base_url"],
        }
        result["asr"] = {
            "configured": bool(asr["api_key"]),
            "model": asr["model"],
            "base_url": asr["base_url"],
        }
        result["dashscope_api_key_masked"] = mask_secret(dashscope_key)
    return result


def settings_response() -> dict[str, Any]:
    settings = load_settings()
    text = task_provider("text", settings=settings)
    image = task_provider("image", settings=settings)
    audio = task_provider("audio", settings=settings)
    return {
        "primary_provider": text,
        "primary_model": provider_config(text)["model"],
        "task_providers": {
            "text": text,
            "image": image,
            "audio": audio,
        },
        "providers": {name: provider_public_config(name) for name in AI_PROVIDERS},
        "minimax": minimax_public_config(),
        "oss": oss_public_config(),
        "translation_fallback": "off" if not translation_fallback_enabled(settings) else "on",
    }


def minimax_public_config() -> dict[str, Any]:
    config = minimax_tts_config()
    return {
        "configured": bool(config["api_key"]),
        "base_url": config["base_url"],
        "model": config["model"],
        "voice": config["voice"],
        "speed": config["speed"],
        "language_boost": config["language_boost"],
        "group_id": config["group_id"],
        "api_key_masked": mask_secret(config["api_key"]),
    }


def oss_public_config() -> dict[str, Any]:
    config = oss_config()
    return {
        "configured": bool(
            config["access_key_id"] and config["access_key_secret"] and config["bucket"] and config["endpoint"]
        ),
        "endpoint": config["endpoint"],
        "bucket": config["bucket"],
        "temp_prefix": config["temp_prefix"],
        "access_key_id_masked": mask_secret(config["access_key_id"]),
        "access_key_secret_masked": mask_secret(config["access_key_secret"]),
    }


@app.get("/api/settings")
def get_settings() -> dict[str, Any]:
    return settings_response()


@app.post("/api/settings")
def update_settings(request: ModelSettingsRequest) -> dict[str, Any]:
    settings = load_settings()
    incoming = request.model_dump()
    for key, value in incoming.items():
        if value is None:
            continue
        value = value.strip()
        if key == "primary_provider":
            if value in AI_PROVIDERS:
                settings[key] = value
                settings["text_provider"] = value
            continue
        if key.endswith("_provider"):
            task = key[: -len("_provider")]
            if value in TASK_PROVIDER_CHOICES.get(task, set(AI_PROVIDERS)):
                settings[key] = value
            continue
        if key == "translation_fallback":
            settings[key] = "off" if value.lower() == "off" else "on"
            continue
        if key.endswith("_api_key") and not value:
            continue
        if key in {"oss_access_key_id", "oss_access_key_secret"} and not value:
            continue
        if key.endswith("_base_url") and value:
            if key in {"qwen_tts_base_url", "qwen_asr_base_url"}:
                value = normalize_dashscope_api_url(value)
            else:
                value = normalize_base_url(value)
        if key == "oss_temp_prefix" and value and not value.endswith("/"):
            value = value + "/"
        if value:
            settings[key] = value
        elif key in settings:
            del settings[key]
    save_settings(settings)
    return settings_response()


# Declared before the {provider} route below so the static path wins the match.
@app.post("/api/settings/test/minimax")
def test_minimax(request: ModelSettingsRequest | None = None) -> dict[str, Any]:
    overrides = request.model_dump() if request else {}
    config = minimax_tts_config(overrides)
    if not config["api_key"]:
        return {"ok": False, "message": f"{config['api_key_env']} 未配置。"}
    try:
        audio_bytes, _subs, meta = _minimax_t2a("Hello, this is a MiniMax test.", config)
    except HTTPException as exc:
        return {"ok": False, "message": str(exc.detail), "model": config["model"], "base_url": config["base_url"]}
    return {
        "ok": bool(audio_bytes),
        "message": "connected",
        "model": meta["model"],
        "base_url": meta["base_url"],
    }


@app.post("/api/settings/test/{provider}")
def test_provider(provider: str, request: ModelSettingsRequest | None = None) -> dict[str, Any]:
    if provider not in AI_PROVIDERS:
        raise HTTPException(404, "Unknown provider")
    overrides = request.model_dump() if request else {}
    fallback = {"ok": False, "message": "未配置 API key，当前会使用本地降级分析。"}
    result, meta = call_ai_json(
        provider,
        "Return JSON only.",
        "Return {\"ok\": true, \"message\": \"connected\"}.",
        fallback,
        overrides,
        prefer_primary=False,
    )
    return {"result": result, "meta": meta}


def synthesize_speech(text: str, voice: str | None = None, language_type: str | None = None,
                      settings: dict[str, Any] | None = None) -> dict[str, Any]:
    """Provider-aware single-shot TTS. Returns a dict with audio_data/audio_url,
    mime_type and meta (MiniMax additionally returns sentence subtitles)."""
    provider = task_provider("audio", settings=settings)
    if provider == "minimax":
        return synthesize_minimax_speech(text, voice, language_type)
    return synthesize_qwen_speech(text, voice, language_type)


@app.post("/api/audio/speech")
def audio_speech(request: SpeechRequest) -> dict[str, Any]:
    settings = load_settings()
    return synthesize_speech(request.text, request.voice, request.language_type, settings=settings)


def sentence_audio_cache_key(text: str, voice: str, model: str, language_type: str) -> str:
    payload = f"{model}|{voice}|{language_type}|{clean_text(text)}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


@app.post("/api/audio/sentence")
def audio_sentence(request: SpeechRequest) -> dict[str, Any]:
    clean = clean_text(request.text or "")
    if not clean:
        raise HTTPException(400, "朗读文本不能为空。")
    settings = load_settings()
    provider = task_provider("audio", settings=settings)
    if provider == "minimax":
        config = minimax_tts_config()
        voice = (request.voice or config["voice"]).strip() or config["voice"]
        language_type = (request.language_type or config["language_boost"]).strip() or config["language_boost"]
    else:
        config = qwen_tts_config()
        voice = (request.voice or config["voice"]).strip() or "Ethan"
        language_type = (request.language_type or config["language_type"]).strip() or "English"
    model = config["model"]
    key = sentence_audio_cache_key(clean, f"{provider}:{voice}", model, language_type)
    cache_path = AUDIO_CACHE_DIR / f"{key}.wav"
    audio_url = f"/audio/{key}.wav"
    if cache_path.exists() and cache_path.stat().st_size > 0:
        return {
            "audio_url": audio_url,
            "cached": True,
            "mime_type": "audio/wav",
            "key": key,
        }
    tts = synthesize_speech(clean, voice, language_type, settings=settings)
    if tts.get("audio_data"):
        audio_bytes = base64.b64decode(tts["audio_data"])
    elif tts.get("audio_url"):
        try:
            r = requests.get(tts["audio_url"], timeout=60)
            r.raise_for_status()
        except requests.RequestException as exc:
            raise HTTPException(502, f"下载 Qwen 朗读音频失败：{exc}") from exc
        audio_bytes = r.content
    else:
        raise HTTPException(502, "Qwen 未返回音频。")
    AUDIO_CACHE_DIR.mkdir(exist_ok=True)
    cache_path.write_bytes(audio_bytes)
    return {
        "audio_url": audio_url,
        "cached": False,
        "mime_type": tts.get("mime_type", "audio/wav"),
        "key": key,
        "meta": tts.get("meta"),
    }


@app.get("/api/articles/{article_id}/export-audio")
def export_article_audio(article_id: str) -> Response:
    article = find_article(article_id)
    title = article.get("title", "Article")
    paragraphs = article.get("cleaned_paragraphs") or article.get("paragraphs") or []
    if not paragraphs:
        raise HTTPException(400, "文章暂无可用文本。请先完成文本检查。")
    settings = load_settings()
    provider = task_provider("audio", settings=settings)
    if provider not in ("qwen", "minimax"):
        raise HTTPException(400, "音频导出需要 Qwen 或 MiniMax TTS，请在设置页面配置。")
    joined = " ".join(str(p) for p in paragraphs if p)
    if len(joined) > 2600:
        joined = joined[:2600].rsplit(" ", 1)[0] + "…"
    tts_text = f"{title}. {joined}"
    tts = synthesize_speech(tts_text, settings=settings)
    if tts.get("audio_data"):
        audio_bytes = base64.b64decode(tts["audio_data"])
    elif tts.get("audio_url"):
        r = requests.get(tts["audio_url"], timeout=60)
        r.raise_for_status()
        audio_bytes = r.content
    else:
        raise HTTPException(502, "TTS 未返回音频。")
    mime = tts.get("mime_type", "audio/wav")
    if audio_bytes[:4] == b"RIFF":
        try:
            buf_info = io.BytesIO(audio_bytes)
            with wave.open(buf_info, "rb") as winfo:
                rate = winfo.getframerate()
                chans = winfo.getnchannels()
                width = winfo.getsampwidth()
            beep = generate_beep_wav(sample_rate=rate, channels=chans, sample_width=width)
            audio_bytes = concat_wav(beep, audio_bytes)
        except Exception:
            pass
    safe_name = re.sub(r"[^\w一-鿿\-]", "_", title)[:60] or "article"
    return Response(
        content=audio_bytes,
        media_type=mime,
        headers={"Content-Disposition": f'attachment; filename="{safe_name}.wav"'},
    )


# ── Library list projection + cover extraction ──
_LIST_ARTICLE_FIELDS = (
    "id", "source_id", "title", "subtitle", "section",
    "stats", "ai_tags", "favorite", "last_opened_at",
)


def _image_ext(data: bytes) -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".jpg"


def extract_epub_cover(epub_path: Path) -> bytes | None:
    """Pull the cover image out of an EPUB — OPF metadata first (EPUB3
    properties=cover-image, then EPUB2 <meta name=cover>), falling back to a
    file whose name looks like a cover. Returns raw image bytes or None."""
    try:
        with zipfile.ZipFile(epub_path) as book:
            names = book.namelist()
            lower = {n.lower(): n for n in names}
            cover_href = None

            container = lower.get("meta-inf/container.xml")
            opf_name = None
            if container:
                m = re.search(r'full-path="([^"]+)"', book.read(container).decode("utf-8", "ignore"))
                if m:
                    opf_name = m.group(1)
            if opf_name and opf_name in names:
                opf_dir = posixpath.dirname(opf_name)
                soup = BeautifulSoup(book.read(opf_name).decode("utf-8", "ignore"), "html.parser")
                item = soup.find("item", attrs={"properties": re.compile("cover-image")})
                if not item:
                    meta = soup.find("meta", attrs={"name": "cover"})
                    cover_id = meta.get("content") if meta else None
                    if cover_id:
                        item = soup.find("item", attrs={"id": cover_id})
                href = item.get("href") if item else None
                if href:
                    cover_href = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else href

            if not cover_href:
                images = [n for n in names if n.lower().endswith((".jpg", ".jpeg", ".png", ".webp", ".gif"))]
                named = [n for n in images if "cover" in n.lower()]
                cover_href = (named or images or [None])[0]

            if cover_href:
                actual = cover_href if cover_href in names else lower.get(cover_href.lower())
                if actual:
                    return book.read(actual)
    except Exception:
        return None
    return None


def _store_cover_bytes(source_id: str, data: bytes) -> str:
    COVERS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{source_id}{_image_ext(data)}"
    (COVERS_DIR / name).write_bytes(data)
    return name


def source_cover_url(source: dict[str, Any]) -> str | None:
    cover_file = source.get("cover_file")
    if cover_file and (COVERS_DIR / cover_file).exists():
        return f"/covers/{cover_file}"
    return None


def slim_article(article: dict[str, Any]) -> dict[str, Any]:
    return {k: article.get(k) for k in _LIST_ARTICLE_FIELDS}


def slim_library(library: dict[str, Any]) -> dict[str, Any]:
    """List projection: only the fields the library/sidebar render, with each
    source's article count, section list and cover. Heavy fields (paragraphs,
    cleaned_paragraphs, AI analyses) are dropped — fetched per article on open."""
    article_ids = [
        str(article.get("id"))
        for source in library.get("sources", [])
        for article in source.get("articles", [])
        if article.get("id")
    ]
    try:
        media_links = linked_media_summaries(article_ids)
    except Exception:
        # The JSON library can also be inspected before the server lifespan has
        # initialized SQLite (for example in maintenance scripts).
        media_links = {}
    sources_out = []
    for source in library.get("sources", []):
        articles = source.get("articles", [])
        sections: list[str] = []
        seen: set[str] = set()
        for article in articles:
            sec = article.get("section") or "Articles"
            if sec not in seen:
                seen.add(sec)
                sections.append(sec)
        sources_out.append({
            "id": source.get("id"),
            "filename": source.get("filename"),
            "uploaded_at": source.get("uploaded_at"),
            "article_count": len(articles),
            "sections": sections,
            "cover_url": source_cover_url(source),
            "articles": [
                {**slim_article(article), "linked_media": media_links.get(str(article.get("id")))}
                for article in articles
            ],
        })
    return {"sources": sources_out}


def _migrate_library_inplace(library: dict[str, Any]) -> bool:
    """One-time backfill: extract EPUB covers for sources that don't have one
    yet, and enrich any article missing derived fields. Returns True if the
    library changed (so the caller persists it once)."""
    changed = False
    for source in library.get("sources", []):
        stored = source.get("stored_path") or ""
        if (
            str(stored).lower().endswith(".epub")
            and int(source.get("parser_version") or 0) < EPUB_PARSER_VERSION
            and Path(stored).is_file()
        ):
            refreshed = parse_upload(Path(stored), str(source.get("id") or ""))
            _replace_source_articles(source, refreshed)
            source["parser_version"] = EPUB_PARSER_VERSION
            changed = True
        if "cover_file" not in source:
            cover_file = None
            if str(stored).lower().endswith(".epub") and Path(stored).exists():
                data = extract_epub_cover(Path(stored))
                if data:
                    cover_file = _store_cover_bytes(source["id"], data)
            source["cover_file"] = cover_file
            changed = True
        for article in source.get("articles", []):
            if not article.get("stats") or "ai_tags" not in article or "cleaned_paragraphs" not in article:
                enrich_article(article)
                changed = True
    return changed


def _replace_source_articles(source: dict[str, Any], articles: list[dict[str, Any]]) -> None:
    """Replace parsed content while retaining user work keyed by stable article id."""
    old_by_id = {str(item.get("id")): item for item in source.get("articles", [])}
    for article in articles:
        previous = old_by_id.get(str(article.get("id"))) or {}
        for field in _AI_PRESERVE_FIELDS:
            if field in previous:
                article[field] = previous[field]
    source["articles"] = articles
    source["article_count"] = len(articles)


@app.get("/api/library")
def get_library() -> dict[str, Any]:
    library = load_json(LIBRARY_PATH, {"sources": []})
    if _migrate_library_inplace(library):
        save_json(LIBRARY_PATH, library)
    return {"library": slim_library(library), "summary": library_summary(library)}


@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)) -> dict[str, Any]:
    ensure_dirs()
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, "请上传 EPUB、DOCX 或 TXT。PDF 请先转换为 DOCX/TXT。")
    source_id = stable_id(file.filename or "upload", str(time.time()), str(uuid.uuid4()))
    safe_name = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]", "_", file.filename or f"upload{suffix}")
    saved_path = UPLOAD_DIR / f"{source_id}_{safe_name}"
    with saved_path.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    content_hash = file_sha256(saved_path)
    library = load_json(LIBRARY_PATH, {"sources": []})
    existing = find_duplicate_source(library, content_hash)
    if existing:
        if suffix == ".epub" and int(existing.get("parser_version") or 0) < EPUB_PARSER_VERSION:
            try:
                refreshed = parse_upload(saved_path, str(existing.get("id") or source_id))
            except Exception as exc:
                safe_unlink(saved_path)
                raise HTTPException(400, f"文件重新解析失败：{exc}") from exc
            if not refreshed:
                safe_unlink(saved_path)
                raise HTTPException(400, "没有解析到可学习的文章。")
            _replace_source_articles(existing, refreshed)
            existing["parser_version"] = EPUB_PARSER_VERSION
            save_json(LIBRARY_PATH, library)
        safe_unlink(saved_path)
        return {"source": existing, "summary": library_summary(library), "duplicate": True}
    try:
        articles = parse_upload(saved_path, source_id)
    except HTTPException:
        safe_unlink(saved_path)
        raise
    except Exception as exc:
        safe_unlink(saved_path)
        raise HTTPException(400, f"文件解析失败：{exc}") from exc
    if not articles:
        safe_unlink(saved_path)
        raise HTTPException(400, "没有解析到可学习的文章。")
    cover_file = None
    if suffix == ".epub":
        cover = extract_epub_cover(saved_path)
        if cover:
            cover_file = _store_cover_bytes(source_id, cover)
    source = {
        "id": source_id,
        "filename": file.filename,
        "stored_path": str(saved_path),
        "content_hash": content_hash,
        "uploaded_at": now_iso(),
        "article_count": len(articles),
        "parser_version": EPUB_PARSER_VERSION if suffix == ".epub" else 1,
        "cover_file": cover_file,
        "articles": articles,
    }
    library.setdefault("sources", []).append(source)
    save_json(LIBRARY_PATH, library)
    return {"source": source, "summary": library_summary(library)}


@app.post("/api/issues/import")
async def import_issue_bundle(
    request: Request,
    epub: UploadFile = File(...),
    audio_zip: UploadFile | None = File(default=None),
) -> dict[str, Any]:
    """Import one publication issue and its optional ZIP audio edition.

    The article import remains the source of truth, so duplicate EPUB uploads
    are idempotent.  Audio members are validated and imported through the same
    managed-media path as browser folder uploads.
    """
    user = request.state.user
    if not user:
        raise HTTPException(401, "请先登录。")
    if Path(epub.filename or "").suffix.lower() != ".epub":
        raise HTTPException(400, "一期合并导入目前需要 EPUB 文章文件。")
    article_result = await upload_file(epub)
    source = article_result["source"]
    result: dict[str, Any] = {
        "source": slim_library({"sources": [source]})["sources"][0],
        "duplicate": bool(article_result.get("duplicate")),
        "summary": article_result.get("summary", {}),
        "audio": None,
        "pairing": None,
        "content_verification": None,
    }
    if audio_zip is None or not audio_zip.filename:
        return result
    if Path(audio_zip.filename).suffix.lower() != ".zip":
        raise HTTPException(400, "音频压缩包请使用 ZIP 格式；也可以在音频库直接选择文件夹。")
    archive_name = re.sub(r"[^A-Za-z0-9_.\-\u4e00-\u9fff]", "_", audio_zip.filename)[:180]
    archive_path = UPLOAD_DIR / f"issue_{uuid.uuid4().hex}_{archive_name}"
    total = 0
    try:
        with archive_path.open("wb") as output:
            while chunk := await audio_zip.read(1024 * 1024):
                total += len(chunk)
                if total > server_config.max_upload_bytes:
                    raise HTTPException(413, "ZIP 压缩包超过服务器允许的最大大小。")
                output.write(chunk)
        collection_name = f"{Path(str(source.get('filename') or audio_zip.filename)).stem} Audio"
        audio_result = import_audio_zip(archive_path, user["id"], collection_name)
        result["audio"] = audio_result
        pairing = preview_content_pairing(source["id"], audio_result["collection_id"])
        result["pairing"] = pairing
        verification_ids = content_verification_media_ids(pairing)
        if verification_ids:
            result["content_verification"] = _start_content_pairing_job(
                source["id"], audio_result["collection_id"], requested=len(verification_ids)
            )
        else:
            result["content_verification"] = {
                "status": "not_needed",
                "requested": 0,
                "message": "元数据匹配已达到高置信度，无需调用 ASR。",
            }
        return result
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f"音频 ZIP 导入失败：{str(exc)[:500]}") from exc
    finally:
        archive_path.unlink(missing_ok=True)
        await audio_zip.close()


_AI_PRESERVE_FIELDS = [
    "overview", "paragraph_analysis", "vocabulary_analysis",
    "long_sentence_analysis", "reading_questions", "text_check",
    "dictation_items", "sentence_analyses", "reading_responses", "dictation_responses",
    "last_writing_feedback", "learning_pack", "favorite", "favorite_at", "last_opened_at", "notes",
]


@app.post("/api/library/rebuild")
def rebuild_library() -> dict[str, Any]:
    library = load_json(LIBRARY_PATH, {"sources": []})
    rebuilt_sources = []
    seen_hashes = set()
    for source in library.get("sources", []):
        stored_path = Path(source.get("stored_path", ""))
        if not stored_path.exists():
            continue
        content_hash = source.get("content_hash") or file_sha256(stored_path)
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)
        old_articles_map = {a["id"]: a for a in source.get("articles", [])}
        articles = parse_upload(stored_path, source["id"])
        for article in articles:
            old = old_articles_map.get(article["id"])
            if old:
                for field in _AI_PRESERVE_FIELDS:
                    if field in old:
                        article[field] = old[field]
        rebuilt_sources.append({
            **source,
            "content_hash": content_hash,
            "article_count": len(articles),
            "rebuilt_at": now_iso(),
            "articles": articles,
        })
    new_library = {"sources": rebuilt_sources}
    _migrate_library_inplace(new_library)
    save_json(LIBRARY_PATH, new_library)
    return {"library": slim_library(new_library), "summary": library_summary(new_library)}


@app.get("/api/articles/{article_id}")
def get_article(article_id: str) -> dict[str, Any]:
    article = update_article(article_id, lambda a: a.update({"last_opened_at": now_iso()}))
    progress = load_json(PROGRESS_PATH, {})
    progress.setdefault(article_id, {
        "article_id": article_id,
        "status": "opened",
        "minutes": 0,
        "activities": [],
        "updated_at": now_iso(),
    })
    progress[article_id]["updated_at"] = now_iso()
    save_json(PROGRESS_PATH, progress)
    article["linked_media"] = linked_media_for_article(article_id)
    return {"article": article}


@app.post("/api/articles/{article_id}/favorite")
def toggle_favorite(article_id: str) -> dict[str, Any]:
    def toggle(article: dict[str, Any]) -> None:
        article["favorite"] = not article.get("favorite", False)
        article["favorite_at"] = now_iso() if article["favorite"] else None
    return {"article": update_article(article_id, toggle)}


@app.post("/api/articles/{article_id}/text-check")
def text_check(article_id: str, refresh: bool = False) -> dict[str, Any]:
    article = find_article(article_id)
    if article.get("text_check") and not refresh:
        return {"text_check": article["text_check"], "meta": cached_meta("deepseek"), "article": article}
    fallback = text_check_fallback(article)
    prompt = (
        "请在已完成基础清洗的英文文章上做AI二次检查，返回JSON字段：cleaned_paragraphs(字符串数组), "
        "issues(数组，每项含paragraph, issue, suggestion), summary。只修正明显OCR错误/标点/拼写疑点，不改写作者风格和句式。\n\n"
        + truncate_text(article_text(article, cleaned=True))
    )
    result, meta = call_ai_json("deepseek", SYSTEM_TEACHER, prompt, fallback)
    cleaned = normalize_paragraphs(result.get("cleaned_paragraphs") or fallback["cleaned_paragraphs"])
    result["cleaned_paragraphs"] = cleaned
    result["removed_paragraphs"] = result.get("removed_paragraphs") or fallback["removed_paragraphs"]
    result["normalization_notes"] = result.get("normalization_notes") or fallback["normalization_notes"]
    result["issues"] = result.get("issues") or fallback["issues"]
    def apply(article_data: dict[str, Any]) -> None:
        article_data["cleaned_paragraphs"] = cleaned
        article_data["removed_paragraphs"] = result["removed_paragraphs"]
        article_data["normalization_notes"] = result["normalization_notes"]
        article_data["text_check"] = result
        article_data["cleaned_stats"] = article_stats(cleaned)
    updated = update_article(article_id, apply)
    return {"text_check": result, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/overview")
def article_overview(article_id: str, refresh: bool = False) -> dict[str, Any]:
    article = find_article(article_id)
    if article.get("overview") and not refresh:
        return {"overview": article["overview"], "meta": cached_meta("deepseek"), "article": article}
    fallback = overview_fallback(article)
    prompt = (
        "请生成高质量双语文章总览，必须返回以下所有JSON字段（不可省略）："
        "main_idea_zh（中文主旨，一句话），"
        "main_idea_en（直接引用文章中最能概括主旨的英文原句，不要翻译），"
        "core_viewpoints（数组，每项含zh中文分析和en英文原句，en必须直接引自文章原文），"
        "structure（中文字符串数组，文章层次结构），"
        "key_vocabulary（数组，每项含term英文词和translation中文义），"
        "background_zh（中文背景知识），"
        "reading_difficulties_zh（中文字符串数组，阅读难点）。"
        "en字段必须是文章原文句子，禁止翻译或改写。\n\n"
        f"标题：{article['title']}\n文章：\n{truncate_text(article_text(article))}"
    )
    result, meta = call_ai_json("deepseek", SYSTEM_TEACHER, prompt, fallback)
    updated = save_article_fields(article_id, {"overview": result})
    return {"overview": result, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/paragraphs/analyze")
def paragraph_analysis(article_id: str, refresh: bool = False) -> dict[str, Any]:
    article = find_article(article_id)
    if article.get("paragraph_analysis") and not refresh:
        return {"paragraphs": coerce_list(article["paragraph_analysis"]), "meta": cached_meta("deepseek"), "article": article}
    fallback = {"paragraphs": paragraph_analysis_fallback(article)}
    prompt = (
        "请逐段分析英文文章，返回JSON字段paragraphs。每段对象必须包含：index, main_idea, function, "
        "logic, expressions, writing_template, chinese_help。重点解释段落功能和句间逻辑，不要做全文翻译。\n\n"
        + truncate_text(article_text(article))
    )
    result, meta = call_ai_json("deepseek", SYSTEM_TEACHER, prompt, fallback)
    paragraphs = coerce_list(result.get("paragraphs") if isinstance(result, dict) else result, fallback["paragraphs"])
    updated = save_article_fields(article_id, {"paragraph_analysis": paragraphs})
    return {"paragraphs": paragraphs, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/long-sentences")
def long_sentence_analysis(article_id: str, refresh: bool = False) -> dict[str, Any]:
    article = find_article(article_id)
    if article.get("long_sentence_analysis") and not refresh:
        return {"sentences": coerce_list(article["long_sentence_analysis"]), "meta": cached_meta("deepseek"), "article": article}
    fallback = {"sentences": long_sentence_fallback(article)}
    prompt = (
        "请选择文章中最值得精读的长难句并解析，返回JSON字段sentences。每项必须按学习顺序包含："
        "sentence, difficult_vocabulary(数组，每项含term, meaning, note), "
        "sentence_structure(对象，含main_clause, modifiers数组, logic, reading_order数组), "
        "translation(自然中文翻译), transferable_expressions, imitation_task。"
        "目标是先扫清难词，再拆句式结构，最后给翻译。\n\n"
        + truncate_text(article_text(article))
    )
    result, meta = call_ai_json("deepseek", SYSTEM_TEACHER, prompt, fallback)
    sentences = coerce_list(result.get("sentences") if isinstance(result, dict) else result, fallback["sentences"])
    updated = save_article_fields(article_id, {"long_sentence_analysis": sentences})
    return {"sentences": sentences, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/dictation/items")
def dictation_items(article_id: str, request: DictationItemsRequest | None = None, refresh: bool = False) -> dict[str, Any]:
    article = find_article(article_id)
    count = min(max((request.count if request else 6), 1), 12)
    existing = normalize_dictation_items(article.get("dictation_items"), article, count)
    if existing and len(existing) >= count and not refresh:
        return {"items": existing[:count], "meta": cached_meta("qwen"), "article": article}
    fallback = {"items": dictation_items_fallback(article, count)}
    prompt = (
        "请为这篇文章独立生成听写与跟读材料，返回JSON字段items（数组）。"
        "每项包含id, source, focus, rounds。source必须是文章英文原句或轻微清理后的完整英文句子，"
        "长度适合听写，优先选择8到28词、发音清晰但有训练价值的句子。"
        "不要依赖长难句解析结果。\n\n"
        + truncate_text(article_text(article), 8000)
    )
    result, meta = call_ai_json("qwen", SYSTEM_TEACHER, prompt, fallback, prefer_primary=False)
    items = result.get("items", fallback["items"]) if isinstance(result, dict) else result
    normalized = normalize_dictation_items(items, article, count)
    if not normalized:
        normalized = fallback["items"]
    updated = save_article_fields(article_id, {"dictation_items": normalized})
    return {"items": normalized, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/sentence/analyze")
def sentence_analysis(article_id: str, request: SentenceRequest) -> dict[str, Any]:
    article = find_article(article_id)
    sentence_key = stable_id(article_id, request.sentence)
    cached = (article.get("sentence_analyses") or {}).get(sentence_key)
    if cached and not request.user_imitation:
        return {"analysis": cached, "meta": cached_meta("deepseek"), "article": article}
    fallback = analyze_sentence_fallback(request.sentence, article)
    prompt = (
        "请分析这个英文句子，返回JSON字段：sentence, difficult_vocabulary, sentence_structure, "
        "translation, transferable_expressions, imitation_task。输出顺序必须服务于学习："
        "1) 先提取较难词汇，difficult_vocabulary数组每项含term, meaning, note；"
        "2) 再分析句式结构，sentence_structure对象含main_clause, modifiers数组, logic, reading_order数组；"
        "3) 最后给自然中文翻译translation。"
        "如果提供了user_imitation，请增加imitation_feedback字段，评分并修改。\n\n"
        f"文章标题：{article['title']}\n句子：{request.sentence}\n用户仿写：{request.user_imitation or ''}"
    )
    result, meta = call_ai_json("deepseek", SYSTEM_TEACHER, prompt, fallback)
    analyses = dict(article.get("sentence_analyses") or {})
    analyses[sentence_key] = result
    updated = save_article_fields(article_id, {"sentence_analyses": analyses})
    return {"analysis": result, "meta": meta, "article": updated}


def _analyze_sentence_for_listening(article: dict[str, Any], sentence: str) -> dict[str, Any]:
    fallback = analyze_sentence_fallback(sentence, article)
    prompt = (
        "请分析这个英文句子，返回JSON字段：sentence, difficult_vocabulary, sentence_structure, "
        "translation, transferable_expressions, imitation_task。输出顺序必须服务于学习："
        "1) 先提取较难词汇，difficult_vocabulary数组每项含term, meaning, note；"
        "2) 再分析句式结构，sentence_structure对象含main_clause, modifiers数组, logic, reading_order数组；"
        "3) 最后给自然中文翻译translation。\n\n"
        f"文章标题：{article['title']}\n句子：{sentence}"
    )
    result, _meta = call_ai_json("deepseek", SYSTEM_TEACHER, prompt, fallback)
    return result


@app.get("/api/articles/{article_id}/listening/sentences")
def listening_sentences(article_id: str) -> dict[str, Any]:
    article = find_article(article_id)
    paragraphs = article.get("cleaned_paragraphs") or article.get("paragraphs") or []
    if not paragraphs:
        raise HTTPException(400, "文章暂无可用文本，请先完成文本检查。")
    items = listening_sentence_items(article)
    return {"article_id": article_id, "title": article.get("title", ""), "sentences": items}


def original_audio_alignment_key(
    article_id: str,
    items: list[dict[str, Any]],
    media: dict[str, Any],
    asr_model: str,
    enable_words: bool = True,
) -> str:
    text_hash = stable_id(*(item["text"] for item in items[:300]), str(len(items)))
    payload = (
        f"original|{article_id}|{media['id']}|{media.get('sha256', '')}|"
        f"{media.get('file_size', 0)}|{text_hash}|{asr_model}|words:{int(enable_words)}|v1"
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def _original_audio_context(article_id: str, enable_words: bool = True) -> dict[str, Any]:
    article = find_article(article_id)
    items = listening_sentence_items(article)
    if not items:
        raise HTTPException(400, "文章暂无可对齐的句子。")
    media = linked_media_source_for_article(article_id)
    if not media:
        raise HTTPException(404, "文章尚未配套原版音频。")
    asr_config = qwen_asr_config()
    oss = oss_config()
    configured = bool(
        asr_config.get("api_key")
        and oss.get("access_key_id") and oss.get("access_key_secret")
        and oss.get("bucket") and oss.get("endpoint")
    )
    key = original_audio_alignment_key(article_id, items, media, asr_config["model"], enable_words)
    return {
        "article": article,
        "article_id": article_id,
        "items": items,
        "media": media,
        "asr_config": asr_config,
        "oss": oss,
        "configured": configured,
        "key": key,
        "align_path": AUDIO_CACHE_DIR / f"{key}.original.align.json",
        "audio_url": media["stream_url"],
    }


def _cached_original_audio_payload(ctx: dict[str, Any]) -> dict[str, Any] | None:
    cached = load_json(ctx["align_path"], {})
    if not cached.get("alignments"):
        return None
    return {
        "article_id": ctx["article_id"],
        "title": ctx["article"].get("title", ""),
        "audio_url": ctx["audio_url"],
        "media_id": ctx["media"]["id"],
        "provider": "original",
        "cached": True,
        **cached,
    }


def _fill_original_alignment_gaps(
    items: list[dict[str, Any]], alignments: list[dict[str, Any]], total_ms: int
) -> list[dict[str, Any]]:
    by_index = {int(item.get("index", -1)): dict(item) for item in alignments}
    out = [by_index.get(item["index"]) or _empty_alignment(item) for item in items]
    valid = [i for i, item in enumerate(out) if _valid_alignment(item)]
    if not valid:
        fallback = _proportional_time_alignment(items, total_ms)
        for item in fallback:
            item["estimated"] = True
        return fallback

    cursor = 0
    while cursor < len(out):
        if _valid_alignment(out[cursor]):
            out[cursor]["estimated"] = False
            cursor += 1
            continue
        start = cursor
        while cursor < len(out) and not _valid_alignment(out[cursor]):
            cursor += 1
        end = cursor
        window_start = int(out[start - 1]["end_ms"]) if start > 0 and _valid_alignment(out[start - 1]) else 0
        window_end = int(out[end]["begin_ms"]) if end < len(out) and _valid_alignment(out[end]) else total_ms
        window_end = max(window_start + (end - start), window_end)
        weights = [max(1, len(normalize_for_alignment(items[i]["text"]).split())) for i in range(start, end)]
        total_weight = sum(weights) or 1
        acc = window_start
        for offset, index in enumerate(range(start, end)):
            begin = acc
            acc = window_end if index == end - 1 else acc + int((window_end - window_start) * weights[offset] / total_weight)
            out[index].update({
                "begin_ms": begin,
                "end_ms": max(begin + 1, acc),
                "confidence": 0.0,
                "estimated": True,
            })
    _enforce_monotonic(out, total_ms)
    return out


def _run_original_audio_alignment(
    ctx: dict[str, Any], enable_words: bool, progress_cb: Callable[[str, int, str], None]
) -> dict[str, Any]:
    if not ctx["configured"]:
        raise PipelineError("原版音频精确对齐需要配置 Qwen ASR 与 OSS。")
    media = ctx["media"]
    suffix = media["path"].suffix.lower() or ".audio"
    object_key = f"{ctx['oss']['temp_prefix']}original-{ctx['key']}{suffix}"
    bucket = None
    asr_task_id = ""
    try:
        progress_cb("oss_upload", 12, f"上传原版音频到 OSS ({ctx['oss']['bucket']})…")
        bucket, signed_url = upload_temp_audio_file_to_oss(
            media["path"], object_key, media.get("mime_type") or "application/octet-stream"
        )
        progress_cb("asr_submit", 24, "提交原版音频转写任务…")
        asr_task_id, meta = start_qwen_filetranscription(signed_url, enable_words=enable_words)

        def _asr_tick(elapsed: int, status: str) -> None:
            pct = 28 + min(58, int(elapsed / 240 * 58))
            mm, ss = divmod(elapsed, 60)
            progress_cb("asr_polling", pct, f"正在识别原版音频… {mm:02d}:{ss:02d} · {status}")

        task_result = poll_qwen_asr_task(
            asr_task_id,
            ctx["asr_config"]["base_url"],
            ctx["asr_config"]["api_key"],
            timeout_seconds=300,
            on_tick=_asr_tick,
        )
        progress_cb("asr_fetch", 88, "读取原版音频转写结果…")
        transcript = fetch_qwen_transcription(task_result)
    finally:
        if bucket is not None:
            try:
                bucket.delete_object(object_key)
            except Exception:
                pass

    asr_sentences = extract_asr_sentences(transcript)
    if not asr_sentences:
        raise PipelineError("Qwen ASR 没有返回可用的原版音频时间戳。")
    progress_cb("align", 94, "将原版音频与文章逐句对齐…")
    total_ms = int(media.get("duration_ms") or 0)
    if total_ms <= 0:
        total_ms = max((int(item.get("end_ms") or 0) for item in asr_sentences), default=0)
    if total_ms <= 0:
        raise PipelineError("无法读取原版音频时长。")
    raw_alignments = align_asr_to_original(ctx["items"], asr_sentences, total_ms=total_ms)
    alignments = _fill_original_alignment_gaps(ctx["items"], raw_alignments, total_ms)
    precise_count = sum(1 for item in alignments if not item.get("estimated"))
    payload = {
        "alignments": alignments,
        "asr_sentences": asr_sentences,
        "meta": {
            **meta,
            "task_id": asr_task_id,
            "source": "original",
            "media_id": media["id"],
            "precise_count": precise_count,
            "estimated_count": len(alignments) - precise_count,
            "enable_words": enable_words,
        },
    }
    progress_cb("cleanup", 98, "保存原版音频时间轴…")
    save_json(ctx["align_path"], payload)
    return {
        "article_id": ctx["article_id"],
        "title": ctx["article"].get("title", ""),
        "audio_url": ctx["audio_url"],
        "media_id": media["id"],
        "provider": "original",
        "cached": False,
        **payload,
    }


@app.get("/api/articles/{article_id}/listening/audio-variants")
def listening_audio_variants(article_id: str) -> dict[str, Any]:
    """List the per-provider aligned-audio variants and whether each is already
    cached on disk, so the listening UI can let the user pick a generated one."""
    article = find_article(article_id)
    items = listening_sentence_items(article)
    settings = load_settings()
    current = task_provider("audio", settings=settings)
    variants = []
    try:
        original_ctx = _original_audio_context(article_id, enable_words=True)
        original_cached = bool(_cached_original_audio_payload(original_ctx))
        variants.append({
            "provider": "original",
            "label": "原版音频",
            "model": original_ctx["asr_config"]["model"],
            "voice": "",
            "configured": original_ctx["configured"],
            "cached": original_cached,
            "audio_url": original_ctx["audio_url"],
            "media_id": original_ctx["media"]["id"],
        })
    except HTTPException:
        pass
    for provider in ("qwen", "minimax"):
        try:
            ctx = _build_aligned_context(article, article_id, items, provider)
        except HTTPException:
            continue
        cached = ctx["audio_path"].exists() and ctx["align_path"].exists()
        variants.append({
            "provider": provider,
            "label": AUDIO_PROVIDER_LABELS.get(provider, provider),
            "model": ctx["tts_config"]["model"],
            "voice": ctx["voice"],
            "configured": bool(ctx["tts_config"].get("api_key")),
            "cached": cached,
            "audio_url": ctx["audio_url"] if cached else None,
        })
    return {"article_id": article_id, "current": current, "variants": variants}


class PipelineError(Exception):
    """Friendly error raised inside the aligned-audio pipeline; surfaced to the user."""


AUDIO_PROVIDER_LABELS = {"qwen": "Qwen", "minimax": "MiniMax"}


def _build_aligned_context(
    article: dict[str, Any],
    article_id: str,
    items: list[dict[str, Any]],
    provider: str,
    voice_override: str | None = None,
    language_override: str | None = None,
    enable_words: bool = True,
) -> dict[str, Any]:
    """Resolve provider configs, voice/language and the cache key/paths for one
    audio provider. Pure: does not require the provider to be the current
    setting, so it is also used to probe which variants are cached."""
    if provider == "minimax":
        tts_config = minimax_tts_config()
        asr_config = {"model": "minimax-subtitle"}
        voice = (voice_override or tts_config["voice"]).strip() or tts_config["voice"]
        language_type = (language_override or tts_config["language_boost"]).strip() or tts_config["language_boost"]
    elif provider == "qwen":
        tts_config = qwen_tts_config()
        asr_config = qwen_asr_config()
        voice = (voice_override or tts_config["voice"]).strip() or "Ethan"
        language_type = (language_override or tts_config["language_type"]).strip() or "English"
    else:
        raise HTTPException(400, "整篇时间轴音频需要 Qwen 或 MiniMax，请在设置中配置 AI 朗读。")

    key = aligned_audio_cache_key(
        article_id, items, f"{provider}:{voice}", language_type,
        tts_config["model"], asr_config["model"], enable_words=enable_words,
    )
    return {
        "article": article,
        "article_id": article_id,
        "items": items,
        "provider": provider,
        "tts_config": tts_config,
        "asr_config": asr_config,
        "voice": voice,
        "language_type": language_type,
        "key": key,
        "audio_url": f"/audio/{key}.wav",
        "audio_path": AUDIO_CACHE_DIR / f"{key}.wav",
        "align_path": AUDIO_CACHE_DIR / f"{key}.align.json",
    }


def _aligned_audio_prepare(article_id: str, request: AlignedAudioRequest) -> dict[str, Any]:
    """Common validation: article, items, provider configs, cache key. Returns a
    context dict consumed by the provider-specific pipelines."""
    article = find_article(article_id)
    paragraphs = article.get("cleaned_paragraphs") or article.get("paragraphs") or []
    if not paragraphs:
        raise HTTPException(400, "文章暂无可用文本，请先完成文本检查。")
    settings = load_settings()
    requested = (request.provider or "").strip().lower()
    provider = requested if requested in ("qwen", "minimax") else task_provider("audio", settings=settings)
    items = listening_sentence_items(article)
    if not items:
        raise HTTPException(400, "无法切分出句子。")
    return _build_aligned_context(
        article, article_id, items, provider,
        voice_override=request.voice, language_override=request.language_type,
        enable_words=request.enable_words,
    )


def _run_aligned_audio_for_provider(ctx: dict[str, Any], enable_words: bool,
                                    progress_cb: Callable[[str, int, str], None]) -> dict[str, Any]:
    if ctx["provider"] == "minimax":
        return _run_aligned_audio_minimax(
            ctx["article"], ctx["items"], ctx["tts_config"], ctx["voice"], ctx["language_type"],
            ctx["audio_path"], ctx["align_path"], ctx["audio_url"], progress_cb,
        )
    return _run_aligned_audio_pipeline(
        ctx["article"], ctx["items"], ctx["tts_config"], ctx["asr_config"],
        ctx["voice"], ctx["language_type"], ctx["key"], ctx["audio_path"],
        ctx["align_path"], ctx["audio_url"], enable_words=enable_words, progress_cb=progress_cb,
    )


def _cached_aligned_audio_payload(
    article: dict[str, Any], article_id: str, audio_url: str, align_path: Path
) -> dict[str, Any] | None:
    cached = load_json(align_path, {})
    if not cached.get("alignments"):
        return None
    return {
        "article_id": article_id,
        "title": article.get("title", ""),
        "audio_url": audio_url,
        "cached": True,
        **cached,
    }


def _run_aligned_audio_pipeline(
    article: dict[str, Any],
    items: list[dict[str, Any]],
    tts_config: dict[str, Any],
    asr_config: dict[str, Any],
    voice: str,
    language_type: str,
    key: str,
    audio_path: Path,
    align_path: Path,
    audio_url: str,
    enable_words: bool,
    progress_cb: Callable[[str, int, str], None],
) -> dict[str, Any]:
    progress_cb("prep", 2, "准备文本分段…")
    chunks = chunk_sentences_for_tts(items)
    if not chunks:
        raise PipelineError("没有可朗读的文本。")

    wav_chunks: list[bytes] = []
    total_chunks = len(chunks)
    for i, group in enumerate(chunks):
        pct = 5 + int(35 * i / max(1, total_chunks))
        progress_cb("tts", pct, f"合成语音 {i + 1}/{total_chunks} 段…")
        tts = synthesize_qwen_speech(chunk_text(group), voice, language_type)
        audio_bytes_chunk = speech_result_to_bytes(tts)
        if audio_bytes_chunk[:4] != b"RIFF":
            raise PipelineError("当前 TTS 返回的不是 WAV 音频，无法拼接整篇音频。")
        wav_chunks.append(audio_bytes_chunk)

    progress_cb("concat", 42, "拼接并写入音频文件…")
    audio_bytes, chunk_offsets_ms = concat_wav_many(wav_chunks)
    AUDIO_CACHE_DIR.mkdir(exist_ok=True)
    audio_path.write_bytes(audio_bytes)

    oss = oss_config()
    if not (oss["access_key_id"] and oss["access_key_secret"] and oss["bucket"] and oss["endpoint"]):
        raise PipelineError("OSS 未配置，无法上传音频做精准对齐。请在设置中填入 OSS Access Key ID/Secret/Bucket/Endpoint。")
    object_key = f"{oss['temp_prefix']}{key}.wav"

    bucket = None
    asr_task_id = ""
    try:
        progress_cb("oss_upload", 45, f"上传音频到 OSS ({oss['bucket']})…")
        bucket, signed_url = upload_temp_audio_to_oss(audio_bytes, object_key)

        progress_cb("asr_submit", 55, "提交 ASR 解析任务…")
        asr_task_id, meta = start_qwen_filetranscription(signed_url, enable_words=enable_words)

        def _asr_tick(elapsed: int, status: str) -> None:
            pct = 60 + min(28, int(elapsed / 180 * 28))
            mm, ss = divmod(elapsed, 60)
            short_id = asr_task_id[:12] + "…" if len(asr_task_id) > 12 else asr_task_id
            progress_cb(
                "asr_polling",
                pct,
                f"OSS 内容解析中…(已等待 {mm:02d}:{ss:02d}, {status}, 任务 {short_id})",
            )

        _asr_tick(0, "PENDING")
        task_result = poll_qwen_asr_task(
            asr_task_id, asr_config["base_url"], asr_config["api_key"], on_tick=_asr_tick
        )

        progress_cb("asr_fetch", 90, "拉取转写结果…")
        transcript = fetch_qwen_transcription(task_result)
    finally:
        if bucket is not None:
            try:
                bucket.delete_object(object_key)
            except Exception:
                pass

    asr_sentences = extract_asr_sentences(transcript)
    if not asr_sentences:
        raise PipelineError("Qwen ASR 没有返回可用的句级时间戳。")

    progress_cb("align", 95, "对齐原文句子…")
    total_ms = wav_duration_ms(audio_bytes)
    alignments = align_asr_to_original(items, asr_sentences, chunks, chunk_offsets_ms, total_ms)
    payload = {
        "alignments": alignments,
        "asr_sentences": asr_sentences,
        "meta": {
            **meta,
            "task_id": asr_task_id,
            "chunks": len(chunks),
            "enable_words": enable_words,
        },
    }

    progress_cb("cleanup", 98, "保存缓存…")
    save_json(align_path, payload)

    return {
        "article_id": article["id"] if "id" in article else "",
        "title": article.get("title", ""),
        "audio_url": audio_url,
        "cached": False,
        **payload,
    }


def _run_aligned_audio_minimax(
    article: dict[str, Any],
    items: list[dict[str, Any]],
    tts_config: dict[str, Any],
    voice: str,
    language_boost: str,
    audio_path: Path,
    align_path: Path,
    audio_url: str,
    progress_cb: Callable[[str, int, str], None],
) -> dict[str, Any]:
    """Whole-article TTS via MiniMax with its own sentence subtitles for the
    timeline — no OSS/ASR round-trip. Synthesises the entire article in one
    request when it fits MiniMax's character limit, otherwise falls back to the
    sentence-chunking used by the Qwen pipeline and offsets each chunk's
    subtitles by its position in the concatenated audio."""
    progress_cb("prep", 4, "准备文本…")
    config = {**tts_config, "voice": voice, "language_boost": language_boost}
    max_chars = config.get("max_chars", 9000)

    full_text = chunk_text(items)
    if not full_text:
        raise PipelineError("没有可朗读的文本。")

    wav_chunks: list[bytes] = []
    chunk_subs: list[list[dict[str, Any]]] = []

    if len(full_text) <= max_chars:
        chunks = [items]
        progress_cb("tts", 30, "MiniMax 合成整篇语音…")
        audio_bytes_chunk, subs, meta = _minimax_t2a(full_text, config, subtitle_enable=True)
        if audio_bytes_chunk[:4] != b"RIFF":
            raise PipelineError("MiniMax 返回的不是 WAV 音频，无法生成时间轴。")
        wav_chunks.append(audio_bytes_chunk)
        chunk_subs.append(subs)
    else:
        chunks = chunk_sentences_for_tts(items, max_chars=max_chars)
        total_chunks = len(chunks)
        meta = {}
        for i, group in enumerate(chunks):
            pct = 5 + int(45 * i / max(1, total_chunks))
            progress_cb("tts", pct, f"MiniMax 合成语音 {i + 1}/{total_chunks} 段…")
            audio_bytes_chunk, subs, meta = _minimax_t2a(chunk_text(group), config, subtitle_enable=True)
            if audio_bytes_chunk[:4] != b"RIFF":
                raise PipelineError("MiniMax 返回的不是 WAV 音频，无法生成时间轴。")
            wav_chunks.append(audio_bytes_chunk)
            chunk_subs.append(subs)

    progress_cb("concat", 75, "拼接并写入音频文件…")
    audio_bytes, chunk_offsets_ms = concat_wav_many(wav_chunks)
    AUDIO_CACHE_DIR.mkdir(exist_ok=True)
    audio_path.write_bytes(audio_bytes)

    subtitles: list[dict[str, Any]] = []
    for offset, subs in zip(chunk_offsets_ms, chunk_subs):
        for s in subs:
            subtitles.append({
                "text": s["text"],
                "begin_ms": s["begin_ms"] + offset,
                "end_ms": s["end_ms"] + offset,
                "words": [],
            })

    total_ms = wav_duration_ms(audio_bytes)
    progress_cb("align", 92, "对齐原文句子…")
    alignments, align_method = align_minimax_sentences(items, subtitles, total_ms)

    payload = {
        "alignments": alignments,
        "asr_sentences": subtitles,
        "meta": {
            **meta,
            "provider": "minimax",
            "chunks": len(chunks),
            "subtitle_sentences": len(subtitles),
            "align_method": align_method,
        },
    }

    progress_cb("cleanup", 98, "保存缓存…")
    save_json(align_path, payload)
    return {
        "article_id": article["id"] if "id" in article else "",
        "title": article.get("title", ""),
        "audio_url": audio_url,
        "cached": False,
        **payload,
    }


def _proportional_time_alignment(items: list[dict[str, Any]], total_ms: int) -> list[dict[str, Any]]:
    """Distribute total_ms across sentences weighted by word count. Far closer to
    real speech timing than an equal split, used when no subtitle timestamps are
    available."""
    weights = [max(1, len(normalize_for_alignment(it["text"]).split())) for it in items]
    total_w = sum(weights) or 1
    out: list[dict[str, Any]] = []
    acc = 0
    for item, w in zip(items, weights):
        begin = int(total_ms * acc / total_w) if total_ms else 0
        acc += w
        end = int(total_ms * acc / total_w) if total_ms else 0
        out.append({
            "index": item["index"],
            "para": item["para"],
            "text": item["text"],
            "asr_text": "",
            "begin_ms": begin,
            "end_ms": max(end, begin + 1),
            "confidence": 0.0,
            "words": [],
        })
    return out


def _interp_subtitle_time(spans: list[tuple[float, float, int, int]], frac: float) -> int:
    """Map a cumulative-character fraction [0,1] onto a millisecond timestamp by
    locating the subtitle span covering that fraction and interpolating linearly
    inside it."""
    if not spans:
        return 0
    for start_frac, end_frac, begin_ms, end_ms in spans:
        if frac <= end_frac or end_frac >= 1.0:
            span_w = end_frac - start_frac
            ratio = 0.0 if span_w <= 0 else max(0.0, min(1.0, (frac - start_frac) / span_w))
            return int(begin_ms + (end_ms - begin_ms) * ratio)
    return spans[-1][3]


def align_minimax_sentences(
    items: list[dict[str, Any]], subtitles: list[dict[str, Any]], total_ms: int,
) -> tuple[list[dict[str, Any]], str]:
    """Align original sentences to MiniMax's sentence-level subtitle timestamps.

    MiniMax reads our text in order, so we anchor by cumulative character
    position: each original sentence's character span [s,e) is projected onto the
    subtitle timeline (also indexed by cumulative characters), and the matching
    real timestamps are interpolated. This is drift-free and tolerant of the two
    sides segmenting sentences differently. Returns (alignments, method)."""
    if not subtitles:
        return _proportional_time_alignment(items, total_ms), "proportional"

    sub_lens = [max(1, len(normalize_for_alignment(s["text"]))) for s in subtitles]
    total_sub = sum(sub_lens) or 1
    spans: list[tuple[float, float, int, int]] = []
    acc = 0
    for s, length in zip(subtitles, sub_lens):
        start_frac = acc / total_sub
        acc += length
        spans.append((start_frac, acc / total_sub, int(s["begin_ms"]), int(s["end_ms"])))

    item_lens = [max(1, len(normalize_for_alignment(it["text"]))) for it in items]
    total_item = sum(item_lens) or 1
    out: list[dict[str, Any]] = []
    acc = 0
    for item, length in zip(items, item_lens):
        s_frac = acc / total_item
        acc += length
        e_frac = acc / total_item
        begin = _interp_subtitle_time(spans, s_frac)
        end = _interp_subtitle_time(spans, e_frac)
        out.append({
            "index": item["index"],
            "para": item["para"],
            "text": item["text"],
            "asr_text": "",
            "begin_ms": begin,
            "end_ms": max(end, begin + 1),
            "confidence": 0.6,
            "words": [],
        })
    _enforce_monotonic(out, total_ms)
    return out, "subtitle-charmap"


@app.post("/api/articles/{article_id}/listening/original-audio/start")
def listening_original_audio_start(article_id: str, request: OriginalAlignmentRequest) -> dict[str, Any]:
    ctx = _original_audio_context(article_id, enable_words=request.enable_words)
    if not request.refresh:
        cached = _cached_original_audio_payload(ctx)
        if cached:
            return {"cached": True, "result": cached}
    elif ctx["align_path"].exists():
        safe_unlink(ctx["align_path"])

    existing = find_job_by_key("original_audio_alignment", ctx["key"])
    if existing:
        return {"cached": False, "task_id": existing["task_id"], "reused": True}

    task_id = create_job("original_audio_alignment", key=ctx["key"])

    def _worker() -> None:
        def cb(stage: str, pct: int, msg: str) -> None:
            update_job(task_id, stage=stage, pct=pct, msg=msg)
        try:
            result = _run_original_audio_alignment(ctx, request.enable_words, cb)
            finish_job(task_id, result=result)
        except PipelineError as exc:
            finish_job(task_id, error=str(exc))
        except HTTPException as exc:
            finish_job(task_id, error=str(exc.detail))
        except Exception as exc:  # noqa: BLE001
            finish_job(task_id, error=f"内部错误：{exc}")

    threading.Thread(target=_worker, daemon=True).start()
    return {"cached": False, "task_id": task_id, "reused": False}


@app.get("/api/articles/{article_id}/listening/original-audio/status/{task_id}")
def listening_original_audio_status(article_id: str, task_id: str) -> dict[str, Any]:
    job = get_job(task_id)
    if not job or job.get("kind") != "original_audio_alignment":
        raise HTTPException(404, "原版音频对齐任务不存在或已过期。")
    out = {
        "task_id": task_id,
        "stage": job["stage"],
        "pct": job["pct"],
        "msg": job["msg"],
        "started_at": job["started_at"],
        "updated_at": job["updated_at"],
        "finished_at": job["finished_at"],
    }
    if job["error"]:
        out["error"] = job["error"]
    if job["result"]:
        out["result"] = job["result"]
    return out


@app.post("/api/articles/{article_id}/listening/aligned-audio")
def listening_aligned_audio(article_id: str, request: AlignedAudioRequest) -> dict[str, Any]:
    ctx = _aligned_audio_prepare(article_id, request)
    if not request.refresh and ctx["audio_path"].exists() and ctx["align_path"].exists():
        cached = _cached_aligned_audio_payload(ctx["article"], article_id, ctx["audio_url"], ctx["align_path"])
        if cached:
            return cached
    try:
        result = _run_aligned_audio_for_provider(
            ctx, enable_words=request.enable_words, progress_cb=lambda *_args, **_kw: None,
        )
    except PipelineError as exc:
        raise HTTPException(400, str(exc))
    result["article_id"] = article_id
    return result


@app.post("/api/articles/{article_id}/listening/aligned-audio/start")
def listening_aligned_audio_start(article_id: str, request: AlignedAudioRequest) -> dict[str, Any]:
    ctx = _aligned_audio_prepare(article_id, request)
    if not request.refresh and ctx["audio_path"].exists() and ctx["align_path"].exists():
        cached = _cached_aligned_audio_payload(ctx["article"], article_id, ctx["audio_url"], ctx["align_path"])
        if cached:
            return {"cached": True, "result": cached}

    existing = find_job_by_key("aligned_audio", ctx["key"])
    if existing:
        return {"cached": False, "task_id": existing["task_id"], "reused": True}

    task_id = create_job("aligned_audio", key=ctx["key"])

    def _worker() -> None:
        def cb(stage: str, pct: int, msg: str) -> None:
            update_job(task_id, stage=stage, pct=pct, msg=msg)
        try:
            result = _run_aligned_audio_for_provider(
                ctx, enable_words=request.enable_words, progress_cb=cb,
            )
            result["article_id"] = article_id
            finish_job(task_id, result=result)
        except PipelineError as exc:
            finish_job(task_id, error=str(exc))
        except HTTPException as exc:
            finish_job(task_id, error=str(exc.detail))
        except Exception as exc:  # noqa: BLE001
            finish_job(task_id, error=f"内部错误：{exc}")

    threading.Thread(target=_worker, daemon=True).start()
    return {"cached": False, "task_id": task_id, "reused": False}


@app.get("/api/articles/{article_id}/listening/aligned-audio/status/{task_id}")
def listening_aligned_audio_status(article_id: str, task_id: str) -> dict[str, Any]:
    job = get_job(task_id)
    if not job:
        raise HTTPException(404, "任务不存在或已过期，请重新发起。")
    out = {
        "task_id": task_id,
        "stage": job["stage"],
        "pct": job["pct"],
        "msg": job["msg"],
        "started_at": job["started_at"],
        "updated_at": job["updated_at"],
        "finished_at": job["finished_at"],
        "extra": job["extra"],
    }
    if job["error"]:
        out["error"] = job["error"]
    if job["result"]:
        out["result"] = job["result"]
    return out


@app.post("/api/articles/{article_id}/listening/prepare")
def listening_prepare(article_id: str) -> dict[str, Any]:
    article = find_article(article_id)
    paragraphs = article.get("cleaned_paragraphs") or article.get("paragraphs") or []
    if not paragraphs:
        raise HTTPException(400, "文章暂无可用文本，请先完成文本检查。")
    items = listening_sentence_items(article)
    if not items:
        raise HTTPException(400, "无法切分出句子。")

    analyses_cache = dict(article.get("sentence_analyses") or {})
    pending: list[tuple[int, str, str]] = []
    for i, item in enumerate(items):
        key = stable_id(article_id, item["text"])
        if key not in analyses_cache:
            pending.append((i, key, item["text"]))

    if pending:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_analyze_sentence_for_listening, article, text): (idx, key)
                for idx, key, text in pending
            }
            for future in as_completed(futures):
                idx, key = futures[future]
                try:
                    analyses_cache[key] = future.result()
                except Exception:
                    analyses_cache[key] = analyze_sentence_fallback(items[idx]["text"], article)
        save_article_fields(article_id, {"sentence_analyses": analyses_cache})

    sentences_out: list[dict[str, Any]] = []
    for i, item in enumerate(items):
        key = stable_id(article_id, item["text"])
        analysis = analyses_cache.get(key) or {}
        vocab_items = analysis.get("difficult_vocabulary") or []
        vocab = []
        for v in vocab_items:
            if not isinstance(v, dict):
                continue
            term = clean_text(str(v.get("term") or ""))
            if not term:
                continue
            vocab.append({
                "term": term,
                "meaning": clean_text(str(v.get("meaning") or "")),
                "note": clean_text(str(v.get("note") or "")),
            })
        sentences_out.append({
            "index": i,
            "para": item["para"],
            "text": item["text"],
            "translation": clean_text(str(analysis.get("translation") or "")),
            "vocab": vocab,
        })
    return {
        "article_id": article_id,
        "title": article.get("title", ""),
        "sentences": sentences_out,
    }


# ───────── Video export（路线 B：后端导素材，本地 ffmpeg 合成）─────────

_RENDER_BAT_16X9 = """@echo off
chcp 65001 >nul
echo Rendering 16:9 video with ffmpeg...
ffmpeg -y -loop 1 -i background_16x9.png -i audio.wav -vf "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,ass=subtitle_16x9.ass" -c:v libx264 -tune stillimage -pix_fmt yuv420p -c:a aac -b:a 192k -shortest out_16x9.mp4
echo Done. Output: out_16x9.mp4
pause
"""

_RENDER_SH_16X9 = """#!/bin/sh
set -e
echo "Rendering 16:9 video with ffmpeg..."
ffmpeg -y -loop 1 -i background_16x9.png -i audio.wav \\
  -vf "scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,ass=subtitle_16x9.ass" \\
  -c:v libx264 -tune stillimage -pix_fmt yuv420p \\
  -c:a aac -b:a 192k -shortest out_16x9.mp4
echo "Done. Output: out_16x9.mp4"
"""

_RENDER_README = """英语精读视频导出包（B1 · 16:9）
================================

文件清单：
  subtitle_16x9.ass   双语字幕 + 难词框 + 中文标题（render 默认使用，完整版式）
  subtitle_16x9.srt   纯双语字幕（备用，仅英文 + 中文译文）
  background_16x9.png  16:9 深色占位背景
  audio.wav           整篇朗读音频（与字幕同源，同一 TTS provider）
  render.bat          Windows 一键合成脚本
  render.sh           mac / Linux 一键合成脚本
  meta.json           元信息（标题 / provider / 句数 / 时长）

使用步骤：
  1. 安装 ffmpeg（https://ffmpeg.org/download.html），确保终端能运行 ffmpeg。
  2. Windows：双击 render.bat；mac / Linux：在本目录运行  sh render.sh
  3. 稍候片刻，生成 out_16x9.mp4。

可调整：
  - 背景：默认使用 background_16x9.png 纯色占位。替换为同名图片即可使用自定义背景。
  - 中文字体：字幕样式默认用 Microsoft YaHei（Windows 自带）。若中文显示为方块，
    请把 subtitle_16x9.ass 中 [V4+ Styles] 各 Style 的 Fontname 改成本机已装的中文字体
    （如 mac 改为 PingFang SC）。
"""


def _write_solid_png(path: Path, width: int, height: int, rgb: tuple[int, int, int]) -> None:
    """Write a tiny dependency-free RGB PNG for the default video background."""
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk("IHDR".encode(), struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk("IDAT".encode(), zlib.compress(raw, level=9))
        + chunk("IEND".encode(), b"")
    )
    path.write_bytes(png)


def _safe_download_stem(text: str, fallback: str = "video_export") -> str:
    stem = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff._-]+", "_", text).strip("._-")
    return stem[:80] or fallback


def _ms_to_srt_time(ms: float) -> str:
    """毫秒 → SRT 时间戳 HH:MM:SS,mmm。"""
    total = max(0, int(ms))
    h, total = divmod(total, 3_600_000)
    m, total = divmod(total, 60_000)
    s, millis = divmod(total, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{millis:03d}"


def _valid_alignment(a: dict[str, Any]) -> bool:
    b, e = a.get("begin_ms"), a.get("end_ms")
    return isinstance(b, (int, float)) and isinstance(e, (int, float)) and e > b


def _resolve_export_provider(
    article: dict[str, Any], article_id: str, items: list[dict[str, Any]], requested: str | None
) -> str:
    """确定导出哪个 provider 的音频/对齐：显式请求 > 已生成(cached)的 > 全局设置。
    与听力前端的选择逻辑一致，保证音画同源。"""
    req = (requested or "").strip().lower()
    if req in ("qwen", "minimax"):
        return req
    for provider in ("qwen", "minimax"):
        try:
            ctx = _build_aligned_context(article, article_id, items, provider)
        except HTTPException:
            continue
        if ctx["audio_path"].exists() and ctx["align_path"].exists():
            return provider
    return task_provider("audio")


def _build_bilingual_srt(
    items: list[dict[str, Any]],
    translations: dict[int, str],
    alignments: dict[int, dict[str, Any]],
) -> str:
    """生成双语 SRT：每句 英文 + 中文译文，时间取该句对齐的 begin_ms/end_ms。
    没有有效时间轴的句子跳过。"""
    blocks: list[str] = []
    n = 0
    for i, item in enumerate(items):
        a = alignments.get(i)
        if not a or not _valid_alignment(a):
            continue
        n += 1
        en = (item.get("text") or "").strip()
        zh = (translations.get(i) or "").strip()
        body = f"{en}\n{zh}" if zh else en
        blocks.append(
            f"{n}\n{_ms_to_srt_time(a['begin_ms'])} --> {_ms_to_srt_time(a['end_ms'])}\n{body}\n"
        )
    return "\n".join(blocks)


# 难词强调色：听力 accent #FFD166 → ASS 颜色为 &HAABBGGRR（BGR），即 &H0066D1FF&
_ASS_ACCENT = "&H0066D1FF&"

# 16:9（1920×1080）样式表。Alignment 走 numpad：2=底中、8=顶中、9=右上。
# SubEN/SubZH 左右各留 260 边距 + WrapStyle:0 让长英文句自动均匀折行。
_ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Title,Microsoft YaHei,46,&H00FFFFFF,&H000000FF,&H00202020,&H64000000,1,0,0,0,100,100,0,0,1,2,1,8,60,60,30,1
Style: SubEN,Arial,52,&H00FFFFFF,&H000000FF,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,3,1,2,260,260,92,1
Style: SubZH,Microsoft YaHei,36,&H00D8D8D8,&H000000FF,&H00101010,&H00000000,0,0,0,0,100,100,0,0,1,3,1,2,260,260,42,1
Style: VocabBox,Microsoft YaHei,32,&H00FFFFFF,&H000000FF,&H00101010,&H64000000,0,0,0,0,100,100,0,0,3,2,0,9,40,40,40,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def _ms_to_ass_time(ms: float) -> str:
    """毫秒 → ASS 时间戳 H:MM:SS.cc（百分秒）。"""
    total = max(0, int(ms))
    h, total = divmod(total, 3_600_000)
    m, total = divmod(total, 60_000)
    s, total = divmod(total, 1000)
    cs = total // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_escape(text: str) -> str:
    """转义放入 ASS Dialogue 文本字段的内容，避免 { } \\ 破坏样式标签或布局。
    顺序：先处理原文已有的反斜杠，再加我们自己合法的 \\{ \\} \\N。"""
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\\", "\\​")  # 反斜杠后插零宽空格(U+200B)，阻止其被当作 \tag 前缀
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = text.replace("\n", "\\N")
    return text


def _build_vocab_box(vocab: list[Any]) -> str:
    """难词框文本：每词「单词(强调色加粗) + 中文翻译」，最多 6 个，用 \\N 分行。"""
    parts: list[str] = []
    for v in vocab[:6]:
        if not isinstance(v, dict):
            continue
        term = _ass_escape(str(v.get("term") or "").strip())
        if not term:
            continue
        meaning = _ass_escape(str(v.get("meaning") or "").strip())
        if meaning:
            parts.append(f"{{\\b1\\c{_ASS_ACCENT}}}{term}{{\\r}}  {meaning}")
        else:
            parts.append(f"{{\\b1\\c{_ASS_ACCENT}}}{term}{{\\r}}")
    return "\\N".join(parts)


def _build_ass_16x9(
    title: str,
    items: list[dict[str, Any]],
    translations: dict[int, str],
    vocab_by_index: dict[int, list[Any]],
    alignments: dict[int, dict[str, Any]],
    total_ms: int,
) -> str:
    """生成 16:9 ASS：顶部中文标题(常显) + 底部双语字幕 + 右上难词框(逐句更新)。"""
    lines = [_ASS_HEADER.rstrip("\n")]
    if title:
        end = _ms_to_ass_time(max(total_ms, 1000))
        lines.append(f"Dialogue: 0,0:00:00.00,{end},Title,,0,0,0,,{_ass_escape(title)}")
    for i, item in enumerate(items):
        a = alignments.get(i)
        if not a or not _valid_alignment(a):
            continue
        start = _ms_to_ass_time(a["begin_ms"])
        end = _ms_to_ass_time(a["end_ms"])
        en = _ass_escape((item.get("text") or "").strip())
        zh = _ass_escape((translations.get(i) or "").strip())
        if en:
            lines.append(f"Dialogue: 0,{start},{end},SubEN,,0,0,0,,{en}")
        if zh:
            lines.append(f"Dialogue: 0,{start},{end},SubZH,,0,0,0,,{zh}")
        box = _build_vocab_box(vocab_by_index.get(i) or [])
        if box:
            lines.append(f"Dialogue: 0,{start},{end},VocabBox,,0,0,0,,{{\\pos(1560,140)}}{box}")
    return "\n".join(lines) + "\n"


@app.post("/api/articles/{article_id}/video/export-package")
def video_export_package(article_id: str, request: VideoExportRequest) -> dict[str, Any]:
    """路线 B（B1）：导出 16:9 视频素材包（ASS/SRT 字幕 + 同源音频 + ffmpeg 脚本）。
    素材落在本机 data/video_export/{id}/，用户进该目录跑 render 脚本即可合成 MP4。"""
    article = find_article(article_id)
    items = listening_sentence_items(article)
    if not items:
        raise HTTPException(400, "无法切分出句子。")

    # provider 解析 → 算 key/路径（音画同源）
    provider = _resolve_export_provider(article, article_id, items, request.provider)
    ctx = _build_aligned_context(article, article_id, items, provider)
    if not (ctx["audio_path"].exists() and ctx["align_path"].exists()):
        label = AUDIO_PROVIDER_LABELS.get(provider, provider)
        raise HTTPException(
            400,
            f"该文章尚未生成 {label} 的整篇对齐音频，请先在听力模式用 {label} 生成后再导出。",
        )

    align_data = load_json(ctx["align_path"], {})
    alignments = {
        a["index"]: a
        for a in align_data.get("alignments", [])
        if isinstance(a, dict) and "index" in a
    }
    if not alignments:
        raise HTTPException(400, "对齐数据为空，请在听力模式重新生成整篇音频。")

    # 译文（缺则内部触发一次 prepare）
    prep = listening_prepare(article_id)
    translations = {s["index"]: s.get("translation", "") for s in prep.get("sentences", [])}
    vocab_by_index = {s["index"]: s.get("vocab", []) for s in prep.get("sentences", [])}

    srt = _build_bilingual_srt(items, translations, alignments)
    if not srt.strip():
        raise HTTPException(400, "没有有效的逐句时间轴，无法生成字幕。")
    total_ms = max((a["end_ms"] for a in alignments.values() if _valid_alignment(a)), default=0)
    ass = _build_ass_16x9(
        article.get("title", ""),
        items,
        translations,
        vocab_by_index,
        alignments,
        int(total_ms),
    )

    out_dir = VIDEO_EXPORT_DIR / article_id
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_solid_png(out_dir / "background_16x9.png", 1920, 1080, (15, 17, 21))
    (out_dir / "subtitle_16x9.ass").write_text(ass, encoding="utf-8")
    (out_dir / "subtitle_16x9.srt").write_text(srt, encoding="utf-8")
    (out_dir / "audio.wav").write_bytes(ctx["audio_path"].read_bytes())
    (out_dir / "render.bat").write_text(_RENDER_BAT_16X9, encoding="utf-8")
    (out_dir / "render.sh").write_text(_RENDER_SH_16X9, encoding="utf-8", newline="\n")
    (out_dir / "README.txt").write_text(_RENDER_README, encoding="utf-8")

    save_json(out_dir / "meta.json", {
        "article_id": article_id,
        "title": article.get("title", ""),
        "provider": provider,
        "key": ctx["key"],
        "ratio": "16:9",
        "sentence_count": len(alignments),
        "duration_ms": int(total_ms),
    })

    return {
        "article_id": article_id,
        "provider": provider,
        "export_dir": str(out_dir),
        "files": [
            "background_16x9.png",
            "subtitle_16x9.ass",
            "subtitle_16x9.srt",
            "audio.wav",
            "render.bat",
            "render.sh",
            "README.txt",
            "meta.json",
        ],
        "sentence_count": len(alignments),
        "duration_ms": int(total_ms),
        "hint": f"进入目录 {out_dir}，运行 render.bat (Windows) 或 sh render.sh (mac/Linux) 合成 out_16x9.mp4",
    }


@app.post("/api/articles/{article_id}/video/export-package/download")
def download_video_export_package(article_id: str, request: VideoExportRequest) -> FileResponse:
    result = video_export_package(article_id, request)
    out_dir = Path(result["export_dir"])
    provider = str(result.get("provider") or "audio")
    title = str(find_article(article_id).get("title") or article_id)
    zip_stem = _safe_download_stem(f"video_16x9_{title}_{provider}", f"video_16x9_{article_id}_{provider}")
    zip_path = VIDEO_EXPORT_DIR / f"{article_id}_{provider}_16x9.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name in result.get("files", []):
            file_path = out_dir / name
            if file_path.exists() and file_path.is_file():
                zf.write(file_path, arcname=name)
    return FileResponse(zip_path, media_type="application/zip", filename=f"{zip_stem}.zip")


# ───────── Video export 路线 C：第三版设计（H3/V3）→ HTML 帧 → MP4 ─────────

_VIDEO_ENRICH_SYSTEM = "你是英语词典助手。只输出 JSON，不要解释。"

_RENDER_C_README = """英语精读视频导出包 · 第三版设计（H3 横屏 / V3 竖屏）
=================================================

每个 render_* 目录是一个独立的视频素材包：
  f0000.html, f0001.html ...  逐帧画面（HTML，含全部排版样式）
  frames.txt                  ffmpeg concat 清单（每帧时长已写好）
  audio.wav                   整篇朗读音频（与画面同源）
  render.bat / render.sh      一键脚本：本机浏览器截图 + ffmpeg 合成

在本机合成（两步全自动）：
  1. 装好两样：Chrome 或 Edge（把 HTML 截成图）、ffmpeg（合成视频，https://ffmpeg.org）。
  2. 进入 render_16x9（横屏）或 render_9x16（竖屏）目录。
     Windows 双击 render.bat；mac / Linux 运行  sh render.sh
  3. 脚本会自动：用本机浏览器把每帧 HTML 截成 PNG → ffmpeg 合成 out.mp4。

说明：
  - 字体用的是你本机的字体，请确保系统有中文字体（Windows 自带宋体/雅黑即可）。
  - 解压路径尽量不要含空格。
  - 16:9 = 1920×1080（B站/横屏）；9:16 = 1080×1920（抖音/小红书/竖屏）。
"""


def _enrich_vocab_phonetics(terms: list[str], article: dict[str, Any], article_id: str) -> dict[str, dict[str, str]]:
    """为难词补 IPA 音标 + 词性（第三版卡片需要），结果缓存在文章的 vocab_phonetics。"""
    cache = dict(article.get("vocab_phonetics") or {})
    missing = [t for t in terms if t and t not in cache]
    if missing:
        prompt = (
            "为下列英文单词或短语逐个标注 IPA 音标（用斜杠包裹，如 /ˈstʌdi/）和词性"
            "（英文缩写：n. v. adj. adv. prep. conj. phr. 等）。返回 JSON 字段 items，"
            '形如 {"word": {"ipa": "...", "pos": "..."}}。词表：\n' + "\n".join(missing)
        )
        result, _meta = call_ai_json("deepseek", _VIDEO_ENRICH_SYSTEM, prompt, {"items": {}})
        items = result.get("items") if isinstance(result, dict) else {}
        if not isinstance(items, dict):
            items = {}
        for t in missing:
            entry = items.get(t) if isinstance(items.get(t), dict) else {}
            cache[t] = {
                "ipa": clean_text(str(entry.get("ipa", ""))),
                "pos": clean_text(str(entry.get("pos", ""))),
            }
        save_article_fields(article_id, {"vocab_phonetics": cache})
    return cache


def _video_title_cn(article: dict[str, Any], article_id: str) -> str:
    """第三版标题区需要中文标题；缺则 AI 翻译一次并缓存。"""
    cached = clean_text(str(article.get("title_cn") or ""))
    if cached:
        return cached
    title = clean_text(str(article.get("title") or ""))
    if not title:
        return ""
    result, _meta = call_ai_json(
        "deepseek", _VIDEO_ENRICH_SYSTEM,
        f"把下面的英文文章标题翻译成简洁地道的中文，只返回 JSON 字段 title_cn：\n{title}",
        {"title_cn": ""},
    )
    cn = clean_text(str(result.get("title_cn") or "")) if isinstance(result, dict) else ""
    if cn:
        save_article_fields(article_id, {"title_cn": cn})
    return cn


def _build_video_frames(
    items: list[dict[str, Any]],
    translations: dict[int, str],
    vocab_by_index: dict[int, list[Any]],
    phonetics: dict[str, dict[str, str]],
    alignments: dict[int, dict[str, Any]],
    ratio: str,
    title_meta: dict[str, str],
) -> list[dict[str, Any]]:
    """逐句→每句一帧。长句通过模板动态字号适配，避免打断学习者的完整句法感知。"""
    valid = [i for i in range(len(items)) if alignments.get(i) and _valid_alignment(alignments[i])]
    total = len(valid)
    frames: list[dict[str, Any]] = []
    for sentence_no, i in enumerate(valid, start=1):
        a = alignments[i]
        en = (items[i].get("text") or "").strip()
        cn = (translations.get(i) or "").strip()
        words: list[dict[str, str]] = []
        for v in (vocab_by_index.get(i) or [])[:6]:
            if not isinstance(v, dict):
                continue
            term = (v.get("term") or "").strip()
            if not term:
                continue
            ph = phonetics.get(term) or {}
            words.append({
                "en": term, "ipa": ph.get("ipa", ""), "pos": ph.get("pos", ""),
                "cn": (v.get("meaning") or "").strip(),
            })
        frames.append({
            **title_meta,
            "sentence": {"en": en, "cn": cn, "index": sentence_no, "total": total},
            "words": words,
            "begin_ms": a["begin_ms"],
            "end_ms": a["end_ms"],
        })
    return frames


def _build_listen_scroll_frames(
    items: list[dict[str, Any]],
    translations: dict[int, str],
    vocab_by_index: dict[int, list[Any]],
    phonetics: dict[str, dict[str, str]],
    alignments: dict[int, dict[str, Any]],
    title_meta: dict[str, str],
    provider_label: str,
) -> list[dict[str, Any]]:
    """Listening-mode video: full article stays in the frame; each sentence state scrolls into view."""
    valid = [i for i in range(len(items)) if alignments.get(i) and _valid_alignment(alignments[i])]
    total = len(valid)
    article_sentences = [
        {"index": i, "para": item.get("para", 0), "text": (item.get("text") or "").strip()}
        for i, item in enumerate(items)
    ]
    frames: list[dict[str, Any]] = []
    for sentence_no, i in enumerate(valid, start=1):
        a = alignments[i]
        en = (items[i].get("text") or "").strip()
        cn = (translations.get(i) or "").strip()
        words: list[dict[str, str]] = []
        for v in (vocab_by_index.get(i) or []):
            if not isinstance(v, dict):
                continue
            term = (v.get("term") or "").strip()
            if not term:
                continue
            ph = phonetics.get(term) or {}
            words.append({
                "en": term, "ipa": ph.get("ipa", ""), "pos": ph.get("pos", ""),
                "cn": (v.get("meaning") or "").strip(),
            })
        frames.append({
            **title_meta,
            "providerLabel": provider_label,
            "article_sentences": article_sentences,
            "sentence": {
                "en": en, "cn": cn, "index": sentence_no, "total": total,
                "source_index": i,
            },
            "words": words,
            "begin_ms": a["begin_ms"],
            "end_ms": a["end_ms"],
        })
    return frames


@app.post("/api/articles/{article_id}/video/render")
def video_render_package(article_id: str, request: VideoRenderRequest) -> dict[str, Any]:
    """服务器把第三版设计（H3/V3）出成「逐帧 HTML + frames.txt + audio + render 脚本」素材包；
    截图和 ffmpeg 合成都在用户本机由 render.bat 完成（服务器无需浏览器/中文字体）。"""
    article = find_article(article_id)
    items = listening_sentence_items(article)
    if not items:
        raise HTTPException(400, "无法切分出句子。")

    provider = _resolve_export_provider(article, article_id, items, request.provider)
    ctx = _build_aligned_context(article, article_id, items, provider)
    if not (ctx["audio_path"].exists() and ctx["align_path"].exists()):
        label = AUDIO_PROVIDER_LABELS.get(provider, provider)
        raise HTTPException(400, f"该文章尚未生成 {label} 的整篇对齐音频，请先在听力模式用 {label} 生成后再导出。")

    align_data = load_json(ctx["align_path"], {})
    alignments = {
        a["index"]: a for a in align_data.get("alignments", [])
        if isinstance(a, dict) and "index" in a
    }
    if not alignments:
        raise HTTPException(400, "对齐数据为空，请在听力模式重新生成整篇音频。")

    prep = listening_prepare(article_id)
    translations = {s["index"]: s.get("translation", "") for s in prep.get("sentences", [])}
    vocab_by_index = {s["index"]: s.get("vocab", []) for s in prep.get("sentences", [])}

    all_terms = sorted({
        (v.get("term") or "").strip()
        for vs in vocab_by_index.values() for v in vs
        if isinstance(v, dict) and (v.get("term") or "").strip()
    })
    phonetics = _enrich_vocab_phonetics(all_terms, article, article_id)
    title_meta = {
        "titleEn": article.get("title", ""),
        "titleCn": _video_title_cn(article, article_id),
        "author": "", "year": "",
    }

    pal = video_render.get_palette(request.palette)
    out_dir = VIDEO_EXPORT_DIR / article_id
    out_dir.mkdir(parents=True, exist_ok=True)
    ratios = [r for r in request.ratios if r in video_render.RATIO_SPEC]
    outputs: dict[str, Any] = {}

    for ratio in ratios:
        slug = ratio.replace(":", "x")
        frames = _build_video_frames(items, translations, vocab_by_index, phonetics, alignments, ratio, title_meta)
        if not frames:
            continue
        spec = video_render.RATIO_SPEC[ratio]
        render = spec["render"]
        frames_dir = out_dir / f"render_{slug}"
        if frames_dir.exists():
            shutil.rmtree(frames_dir, ignore_errors=True)
        frames_dir.mkdir(parents=True, exist_ok=True)

        # 服务器只写「逐帧 HTML」；PNG 由本机 render.bat 用浏览器现截。
        rendered: list[dict[str, Any]] = []
        for idx, fr in enumerate(frames):
            (frames_dir / f"f{idx:04d}.html").write_text(render(fr, pal), encoding="utf-8")
            rendered.append({"png": f"f{idx:04d}.png", "dur_ms": fr["end_ms"] - fr["begin_ms"]})

        video_render.write_concat_list(rendered, frames_dir / "frames.txt")
        (frames_dir / "audio.wav").write_bytes(ctx["audio_path"].read_bytes())
        (frames_dir / "render.bat").write_text(video_render.render_bat(ratio), encoding="utf-8")
        (frames_dir / "render.sh").write_text(video_render.render_sh(ratio), encoding="utf-8", newline="\n")
        (frames_dir / "README.txt").write_text(_RENDER_C_README, encoding="utf-8")

        outputs[ratio] = {
            "design": spec["design"],
            "frames": len(frames),
            "frames_dir": str(frames_dir),
            "resolution": f'{spec["out_w"]}x{spec["out_h"]}',
            "note": "本机运行 render.bat（需 Chrome/Edge + ffmpeg）→ 自动截图并合成 out.mp4。",
        }

    if not outputs:
        raise HTTPException(400, "没有可渲染的画面（请检查比例与对齐数据）。")

    save_json(out_dir / "render_meta.json", {
        "article_id": article_id, "title": article.get("title", ""),
        "provider": provider, "palette": request.palette, "design": "v3",
        "outputs": outputs,
    })
    return {
        "article_id": article_id, "provider": provider,
        "outputs": outputs,
        "hint": "已打包逐帧 HTML 素材：解压后进入 render_16x9 / render_9x16 目录运行 render.bat"
                "（本机需 Chrome/Edge + ffmpeg），自动截图并合成 out.mp4。",
    }


@app.post("/api/articles/{article_id}/video/render/download")
def download_video_render_package(article_id: str, request: VideoRenderRequest) -> FileResponse:
    result = video_render_package(article_id, request)
    title = str(find_article(article_id).get("title") or article_id)
    provider = str(result.get("provider") or "audio")
    zip_stem = _safe_download_stem(f"video_v3_{title}_{provider}", f"video_v3_{article_id}")
    zip_path = VIDEO_EXPORT_DIR / f"{article_id}_{provider}_v3.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in result.get("outputs", {}).values():
            fdir = Path(entry["frames_dir"])
            for p in sorted(fdir.iterdir()):
                if p.is_file():
                    zf.write(p, arcname=f"{fdir.name}/{p.name}")
    return FileResponse(zip_path, media_type="application/zip", filename=f"{zip_stem}.zip")


_RENDER_LISTEN_SCROLL_README = """英语精读视频导出包 · 滚动听力视频（16:9）
=================================================

这个素材包模拟网页听力模式：
  f0000.html, f0001.html ...  每句对应一帧，文章自动滚动并高亮当前句
  frames.txt                  ffmpeg concat 清单
  audio.wav                   整篇朗读音频
  render.bat / render.sh      本机浏览器截图 + ffmpeg 合成

右侧面板包含当前句中文译文和全部标记生词；视频无法手动滑动，所以模板会使用紧凑布局尽量全部显示。

使用：
  Windows 双击 render.bat；mac / Linux 运行 sh render.sh
"""


@app.post("/api/articles/{article_id}/video/listening-scroll")
def video_listening_scroll_package(article_id: str, request: VideoRenderRequest) -> dict[str, Any]:
    """新增模式：16:9 滚动听力视频，保留原有精读卡片视频导出。"""
    article = find_article(article_id)
    items = listening_sentence_items(article)
    if not items:
        raise HTTPException(400, "无法切分出句子。")

    provider = _resolve_export_provider(article, article_id, items, request.provider)
    ctx = _build_aligned_context(article, article_id, items, provider)
    if not (ctx["audio_path"].exists() and ctx["align_path"].exists()):
        label = AUDIO_PROVIDER_LABELS.get(provider, provider)
        raise HTTPException(400, f"该文章尚未生成 {label} 的整篇对齐音频，请先在听力模式用 {label} 生成后再导出。")

    align_data = load_json(ctx["align_path"], {})
    alignments = {
        a["index"]: a for a in align_data.get("alignments", [])
        if isinstance(a, dict) and "index" in a
    }
    if not alignments:
        raise HTTPException(400, "对齐数据为空，请在听力模式重新生成整篇音频。")

    prep = listening_prepare(article_id)
    translations = {s["index"]: s.get("translation", "") for s in prep.get("sentences", [])}
    vocab_by_index = {s["index"]: s.get("vocab", []) for s in prep.get("sentences", [])}

    all_terms = sorted({
        (v.get("term") or "").strip()
        for vs in vocab_by_index.values() for v in vs
        if isinstance(v, dict) and (v.get("term") or "").strip()
    })
    phonetics = _enrich_vocab_phonetics(all_terms, article, article_id)
    title_meta = {
        "titleEn": article.get("title", ""),
        "titleCn": _video_title_cn(article, article_id),
        "author": "", "year": "",
    }

    ratio = "listen-scroll-16:9"
    spec = video_render.RATIO_SPEC[ratio]
    frames = _build_listen_scroll_frames(
        items, translations, vocab_by_index, phonetics, alignments,
        title_meta, AUDIO_PROVIDER_LABELS.get(provider, provider),
    )
    if not frames:
        raise HTTPException(400, "没有可渲染的滚动听力画面。")

    out_dir = VIDEO_EXPORT_DIR / article_id
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "render_listen_scroll_16x9"
    if frames_dir.exists():
        shutil.rmtree(frames_dir, ignore_errors=True)
    frames_dir.mkdir(parents=True, exist_ok=True)

    render = spec["render"]
    pal = video_render.get_palette(request.palette)
    rendered: list[dict[str, Any]] = []
    for idx, fr in enumerate(frames):
        (frames_dir / f"f{idx:04d}.html").write_text(render(fr, pal), encoding="utf-8")
        rendered.append({"png": f"f{idx:04d}.png", "dur_ms": fr["end_ms"] - fr["begin_ms"]})

    video_render.write_concat_list(rendered, frames_dir / "frames.txt")
    (frames_dir / "audio.wav").write_bytes(ctx["audio_path"].read_bytes())
    (frames_dir / "render.bat").write_text(video_render.render_bat(ratio), encoding="utf-8")
    (frames_dir / "render.sh").write_text(video_render.render_sh(ratio), encoding="utf-8", newline="\n")
    (frames_dir / "README.txt").write_text(_RENDER_LISTEN_SCROLL_README, encoding="utf-8")

    save_json(out_dir / "listen_scroll_meta.json", {
        "article_id": article_id, "title": article.get("title", ""),
        "provider": provider, "design": "listen-scroll", "frames": len(frames),
        "resolution": f'{spec["out_w"]}x{spec["out_h"]}',
    })
    return {
        "article_id": article_id,
        "provider": provider,
        "outputs": {
            "16:9": {
                "design": spec["design"],
                "frames": len(frames),
                "frames_dir": str(frames_dir),
                "resolution": f'{spec["out_w"]}x{spec["out_h"]}',
            }
        },
        "hint": "已生成滚动听力视频素材：解压后进入 render_listen_scroll_16x9 运行 render.bat。",
    }


@app.post("/api/articles/{article_id}/video/listening-scroll/download")
def download_video_listening_scroll_package(article_id: str, request: VideoRenderRequest) -> FileResponse:
    result = video_listening_scroll_package(article_id, request)
    title = str(find_article(article_id).get("title") or article_id)
    provider = str(result.get("provider") or "audio")
    zip_stem = _safe_download_stem(f"video_listen_scroll_{title}_{provider}", f"video_listen_scroll_{article_id}")
    zip_path = VIDEO_EXPORT_DIR / f"{article_id}_{provider}_listen_scroll.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for entry in result.get("outputs", {}).values():
            fdir = Path(entry["frames_dir"])
            for p in sorted(fdir.iterdir()):
                if p.is_file():
                    zf.write(p, arcname=f"{fdir.name}/{p.name}")
    return FileResponse(zip_path, media_type="application/zip", filename=f"{zip_stem}.zip")


@app.post("/api/articles/{article_id}/vocabulary/analyze")
def vocabulary_analysis(article_id: str, refresh: bool = False) -> dict[str, Any]:
    article = find_article(article_id)
    if article.get("vocabulary_analysis") and not refresh:
        return {"items": coerce_list(article["vocabulary_analysis"]), "meta": cached_meta("deepseek"), "article": article}
    fallback = {"items": vocabulary_fallback(article)}
    prompt = (
        "请从文章中筛选真正值得学习的词汇和表达，返回JSON字段items（数组，最多20项）。"
        "每项包含：term, layer, translation, context, collocations（字符串数组）, synonym_note, example, imitation_task。"
        "layer只能是：核心必会词、阅读理解词、写作可用词、学术表达词、熟词僻义词。不要列常见简单词。\n\n"
        + truncate_text(article_text(article), 8000)
    )
    result, meta = call_ai_json("deepseek", SYSTEM_TEACHER, prompt, fallback)
    items = coerce_list(result.get("items") if isinstance(result, dict) else result, fallback["items"])
    updated = save_article_fields(article_id, {"vocabulary_analysis": items})
    return {"items": items, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/vocabulary/sentence-feedback")
def vocab_sentence_feedback(article_id: str, request: VocabSentenceRequest) -> dict[str, Any]:
    fallback = vocab_sentence_feedback_fallback(request.term, request.sentence)
    prompt = (
        "请评价用户用目标词造句是否自然，返回JSON字段：naturalness, issue, improved_sentence, usage_tip。"
        f"\n目标词：{request.term}\n用户句子：{request.sentence}"
    )
    result, meta = call_ai_json("qwen", SYSTEM_TEACHER, prompt, fallback, prefer_primary=False)
    return {"feedback": result, "meta": meta}


@app.post("/api/articles/{article_id}/reading/questions")
def reading_questions(article_id: str, refresh: bool = False) -> dict[str, Any]:
    article = find_article(article_id)
    if article.get("reading_questions") and not refresh:
        questions = normalize_reading_questions(article["reading_questions"], article_id, reading_questions_fallback(article))
        return {"questions": questions, "meta": cached_meta("deepseek"), "article": article}
    fallback = {"questions": reading_questions_fallback(article)}
    prompt = (
        "请基于文章生成英文阅读理解输出题，返回JSON字段questions。每项包含id, question, focus, keywords, "
        "reference_answer。题目必须要求学生用英文回答，参考答案可以生成但前端会在提交后显示。\n\n"
        + truncate_text(article_text(article))
    )
    result, meta = call_ai_json("deepseek", SYSTEM_TEACHER, prompt, fallback)
    questions = normalize_reading_questions(
        result.get("questions") if isinstance(result, dict) else result,
        article_id,
        fallback["questions"],
    )
    updated = save_article_fields(article_id, {"reading_questions": questions})
    return {"questions": questions, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/reading/grade")
def grade_reading_answer(article_id: str, request: ReadingAnswerRequest) -> dict[str, Any]:
    article = find_article(article_id)
    fallback = grade_answer_fallback(request.question, request.answer)
    prompt = (
        "请像高级英语教师一样批改学生阅读题英文答案，返回JSON字段：score(0-20), content, logic, grammar, "
        "vocabulary, improved_answer, reference_answer。先判断内容是否准确，再优化英文表达。\n\n"
        f"文章：\n{truncate_text(article_text(article), 10000)}\n\n问题：{request.question}\n学生答案：{request.answer}"
    )
    result, meta = call_ai_json("qwen", SYSTEM_TEACHER, prompt, fallback, prefer_primary=False)
    save_output(SaveOutputRequest(article_id=article_id, kind="reading-answer", content=request.answer, feedback=result))
    responses = dict(article.get("reading_responses") or {})
    responses[request.question_id] = {
        "question_id": request.question_id,
        "question": request.question,
        "answer": request.answer,
        "feedback": result,
        "meta": meta,
        "updated_at": now_iso(),
    }
    updated = save_article_fields(article_id, {"reading_responses": responses})
    return {"feedback": result, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/dictation/feedback")
def dictation_feedback(article_id: str, request: DictationRequest) -> dict[str, Any]:
    article = find_article(article_id)
    fallback = dictation_feedback_fallback(request.source, request.answer)
    prompt = (
        "请批改听写答案，返回JSON字段：score, original, user_answer, missing_words, spelling_or_extra, "
        "listening_notes, why_difficult。解释弱读、连读、漏听和拼写问题。\n\n"
        f"原文：{request.source}\n学生听写：{request.answer}"
    )
    result, meta = call_ai_json("qwen", SYSTEM_TEACHER, prompt, fallback, prefer_primary=False)
    result = normalize_dictation_feedback(result, fallback)
    item_id = stable_id(article_id, "dictation", request.source)
    for item in article.get("dictation_items") or []:
        if item.get("source") == request.source:
            item_id = item.get("id") or item_id
            break
    responses = dict(article.get("dictation_responses") or {})
    responses[item_id] = {
        "item_id": item_id,
        "source": request.source,
        "answer": request.answer,
        "feedback": result,
        "meta": meta,
        "updated_at": now_iso(),
    }
    save_output(SaveOutputRequest(article_id=article_id, kind="dictation", content=request.answer, feedback=result))
    updated = save_article_fields(article_id, {"dictation_responses": responses})
    return {"feedback": result, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/writing/feedback")
def writing_feedback(article_id: str, request: WritingFeedbackRequest) -> dict[str, Any]:
    article = find_article(article_id)
    fallback = writing_feedback_fallback(request.task, request.content, article)
    prompt = (
        "请批改学生英文写作，返回JSON字段：score(0-20), content, structure, grammar, vocabulary, "
        "improved_version, next_step。重点看是否准确回应任务、逻辑是否清楚、是否使用文章表达。\n\n"
        f"文章标题：{article['title']}\n任务：{request.task}\n学生作文：{request.content}"
    )
    result, meta = call_ai_json("qwen", SYSTEM_TEACHER, prompt, fallback, prefer_primary=False)
    save_output(SaveOutputRequest(article_id=article_id, kind="writing", content=request.content, feedback=result))
    writing_record = {
        "task": request.task,
        "content": request.content,
        "feedback": result,
        "meta": meta,
        "updated_at": now_iso(),
    }
    updated = save_article_fields(article_id, {"last_writing_feedback": writing_record})
    return {"feedback": result, "meta": meta, "article": updated}


@app.post("/api/articles/{article_id}/pack")
def get_learning_pack(article_id: str, request: PackRequest) -> dict[str, Any]:
    article = find_article(article_id)
    if article.get("learning_pack") and not request.refresh:
        return {"pack": article["learning_pack"]}
    pack = make_pack(article)
    update_article(article_id, lambda a: a.update({"learning_pack": pack}))
    return {"pack": pack}


def translation_fallback_enabled(settings: dict[str, Any] | None = None) -> bool:
    settings = settings if settings is not None else load_settings()
    return str(settings.get("translation_fallback", "on")).strip().lower() != "off"


def translate_via_api(term: str) -> dict[str, str] | None:
    """Best-effort keyless translation for a single word, used only when there is
    no local entry and no language model available. Only the word itself is sent
    to the translation service — never the surrounding article text. Returns a
    dict with ``translation`` and ``provider``, or None on any failure."""
    term = term.strip()
    if not term or not translation_fallback_enabled():
        return None
    settings = load_settings()
    endpoint = (settings.get("translation_api_url") or TRANSLATION_API_DEFAULTS["endpoint"]).strip()
    langpair = (settings.get("translation_api_langpair") or TRANSLATION_API_DEFAULTS["langpair"]).strip()
    try:
        response = requests.get(endpoint, params={"q": term, "langpair": langpair}, timeout=8)
        response.raise_for_status()
        data = response.json()
    except (requests.RequestException, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    # MyMemory reports quota/errors with a non-200 responseStatus and puts the
    # notice text where the translation would be — don't surface those as a result.
    if str(data.get("responseStatus")) not in {"200", ""}:
        return None
    translated = clean_text(str((data.get("responseData") or {}).get("translatedText") or ""))
    upper = translated.upper()
    if not translated or "MYMEMORY WARNING" in upper or "INVALID" in upper:
        return None
    # The service echoes the query back (often upper-cased) when it has no match.
    if translated.strip().lower() == term.lower():
        return None
    return {"translation": translated, "provider": "translation-api"}


@app.get("/api/dictionary/status")
def dictionary_status(request: Request) -> dict[str, Any]:
    if not request.state.user:
        raise HTTPException(401, "请先登录。")
    return {
        "offline": dictionary_offline_stats(),
        "translation_fallback": translation_fallback_enabled(),
        "ai_configured": ai_available(task_provider("text"), prefer_primary=False),
    }


@app.post("/api/dictionary/lookup")
def dictionary_lookup(spec: DictionaryLookupRequest, request: Request) -> dict[str, Any]:
    user = request.state.user
    if not user:
        raise HTTPException(401, "请先登录。")
    term = clean_text(spec.term)[:200]
    if not re.search(r"[A-Za-z]", term):
        raise HTTPException(400, "请选择英文单词或短语。")
    local = vocabulary_lookup_payload(user["id"], term)
    item = local["item"]
    if local.get("found"):
        return {**local, "meta": {"used_ai": False, "provider": local.get("provider", "local")}}
    fallback = {
        "term": term,
        "normalized_term": re.sub(r"[^a-z'-]", "", term.lower()),
        "lemma": item.get("lemma") or term.lower(),
        "phonetic": "",
        "part_of_speech": "",
        "definition": "",
        "translation": "暂未查到本地释义，可直接加入生词本后编辑。",
        "context_note": "",
    }
    result, meta = call_ai_json(
        task_provider("text"),
        "你是英汉学习词典。只返回JSON，不要使用Markdown。",
        "请结合语境查询词语，返回字段 term, lemma, phonetic, part_of_speech, "
        "definition（简明英文释义）, translation（准确中文义）, context_note（本句中的含义或用法）。\n"
        f"词语：{term}\n语境：{clean_text(spec.context)[:1200]}",
        fallback,
    )
    if not isinstance(result, dict):
        result = fallback
    normalized = {
        "term": clean_text(str(result.get("term") or term)),
        "normalized_term": fallback["normalized_term"],
        "lemma": clean_text(str(result.get("lemma") or fallback["lemma"])),
        "phonetic": clean_text(str(result.get("phonetic") or "")),
        "part_of_speech": clean_text(str(result.get("part_of_speech") or "")),
        "definition": clean_text(str(result.get("definition") or "")),
        "translation": clean_text(str(result.get("translation") or fallback["translation"])),
        "context_note": clean_text(str(result.get("context_note") or "")),
    }
    if meta.get("used_ai"):
        cache_vocabulary_lookup(term, normalized, str(meta.get("provider") or "ai"))
        return {"found": True, "saved": False, "item": normalized, "meta": meta}
    # No local entry and no language model — fall back to a keyless translation API.
    translated = translate_via_api(term)
    if translated:
        normalized["translation"] = translated["translation"]
        cache_vocabulary_lookup(term, normalized, translated["provider"])
        return {
            "found": True, "saved": False, "item": normalized,
            "meta": {**meta, "provider": translated["provider"], "used_translation_api": True},
        }
    return {"found": False, "saved": False, "item": normalized, "meta": meta}


@app.post("/api/outputs")
def save_output(request: SaveOutputRequest) -> dict[str, Any]:
    outputs = load_json(OUTPUTS_PATH, [])
    item = {
        "id": stable_id(request.article_id, request.kind, str(time.time())),
        "article_id": request.article_id,
        "kind": request.kind,
        "content": request.content,
        "feedback": request.feedback or "",
        "created_at": now_iso(),
    }
    outputs.append(item)
    save_json(OUTPUTS_PATH, outputs)
    return {"item": item}


@app.get("/api/outputs")
def get_outputs() -> dict[str, Any]:
    return {"items": load_json(OUTPUTS_PATH, [])}


@app.post("/api/progress")
def save_progress(request: ProgressRequest) -> dict[str, Any]:
    progress = load_json(PROGRESS_PATH, {})
    item = progress.setdefault(request.article_id, {
        "article_id": request.article_id,
        "status": "new",
        "minutes": 0,
        "activities": [],
        "updated_at": now_iso(),
    })
    item["status"] = request.status
    item["minutes"] = int(item.get("minutes", 0)) + max(0, request.minutes)
    if request.activity:
        item.setdefault("activities", []).append({"name": request.activity, "minutes": request.minutes, "at": now_iso()})
    item["updated_at"] = now_iso()
    progress[request.article_id] = item
    save_json(PROGRESS_PATH, progress)
    return {"item": item, "progress": progress}


@app.get("/api/progress")
def get_progress() -> dict[str, Any]:
    return {"items": load_json(PROGRESS_PATH, {})}


@app.post("/api/articles/{article_id}/notes")
def save_notes(article_id: str, request: NotesRequest) -> dict[str, Any]:
    try:
        update_article(article_id, lambda a: a.update({"notes": request.notes}))
    except Exception:
        pass
    return {"ok": True}


@app.delete("/api/sources/{source_id}")
def delete_source(source_id: str) -> dict[str, Any]:
    def apply(library: dict[str, Any]) -> None:
        sources = library.get("sources", [])
        source = next((s for s in sources if s["id"] == source_id), None)
        if not source:
            raise HTTPException(status_code=404, detail="Source not found")
        stored_path = Path(source.get("stored_path", ""))
        safe_unlink(stored_path)
        cover_file = source.get("cover_file")
        if cover_file:
            safe_unlink(COVERS_DIR / cover_file)
        library["sources"] = [s for s in sources if s["id"] != source_id]
    mutate_library(apply)
    remove_source_links(source_id)
    lib = load_json(LIBRARY_PATH, {"sources": []})
    return {"library": slim_library(lib), "summary": library_summary(lib)}


AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
COVERS_DIR.mkdir(parents=True, exist_ok=True)
VIDEO_EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# Server extensions are kept in separate modules so the established reading
# routes remain compatible while authentication and the media library evolve.
from english_lab.auth import SessionAuthMiddleware, current_user, router as auth_router  # noqa: E402
from english_lab.health import router as health_router  # noqa: E402
from english_lab.media import import_audio_zip, router as media_router  # noqa: E402
from english_lab.content_links import (  # noqa: E402
    PairPreviewRequest,
    apply_content_transcripts,
    content_matching_source,
    content_verification_media_ids,
    linked_media_for_article,
    linked_media_source_for_article,
    linked_media_summaries,
    preview_content_pairing,
    remove_source_links,
    router as content_links_router,
)
from english_lab.listening_practice import router as listening_practice_router  # noqa: E402
from english_lab.vocabulary import (  # noqa: E402
    cache_lookup as cache_vocabulary_lookup,
    dictionary_stats as dictionary_offline_stats,
    lookup_payload as vocabulary_lookup_payload,
    router as vocabulary_router,
)
from english_lab.webdav import router as webdav_api_router, webdav_routes  # noqa: E402


CONTENT_MATCH_CACHE_DIR = AUDIO_CACHE_DIR / "content_match"
CONTENT_MATCH_CACHE_DIR.mkdir(parents=True, exist_ok=True)


def _content_match_media(media_id: str) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            """SELECT id, collection_id, title, original_name, storage_path, mime_type,
                      sha256, duration_ms, file_size
               FROM media_items WHERE id = ? AND deleted_at IS NULL""",
            (media_id,),
        ).fetchone()
    if not row:
        raise PipelineError("正文核验所需的音频不存在或已在回收站。")
    payload = dict(row)
    path = (server_config.media_root / payload.pop("storage_path")).resolve()
    if not path.is_relative_to(server_config.media_root) or not path.is_file():
        raise PipelineError(f"音频文件不存在：{payload.get('title') or media_id}")
    payload["path"] = path
    return payload


def _content_sample_windows(duration_ms: int | None) -> list[tuple[float, float]]:
    duration = max(0.0, float(duration_ms or 0) / 1000)
    if duration and duration <= 105:
        start = min(6.0, max(0.0, duration * 0.08))
        return [(round(start, 2), round(max(8.0, min(82.0, duration - start)), 2))]
    segment = 28.0
    if duration <= 0:
        return [(12.0, segment), (90.0, segment), (180.0, segment)]
    starts = [12.0, duration / 3, duration * 2 / 3]
    windows: list[tuple[float, float]] = []
    for start in starts:
        start = max(0.0, min(start, max(0.0, duration - segment - 2)))
        length = max(8.0, min(segment, duration - start))
        if not any(abs(start - existing[0]) < 6 for existing in windows):
            windows.append((round(start, 2), round(length, 2)))
    return windows


def _extract_content_sample(media: dict[str, Any], output: Path) -> list[tuple[float, float]]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise PipelineError("正文核验需要服务器安装 ffmpeg，用于抽取少量音频片段。")
    windows = _content_sample_windows(media.get("duration_ms"))
    filters: list[str] = []
    labels: list[str] = []
    for index, (start, duration) in enumerate(windows):
        label = f"a{index}"
        labels.append(f"[{label}]")
        filters.append(
            f"[0:a]atrim=start={start}:duration={duration},asetpts=PTS-STARTPTS[{label}]"
        )
    if len(labels) == 1:
        filters.append(f"{labels[0]}anull[out]")
    else:
        filters.append(f"{''.join(labels)}concat=n={len(labels)}:v=0:a=1[out]")
    command = [
        ffmpeg, "-nostdin", "-y", "-v", "error", "-i", str(media["path"]),
        "-filter_complex", ";".join(filters), "-map", "[out]",
        "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(output),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=120, check=False)
    if completed.returncode != 0 or not output.is_file() or output.stat().st_size < 1024:
        detail = (completed.stderr or "无法生成音频抽样").strip()[-500:]
        raise PipelineError(f"抽取音频片段失败：{detail}")
    return windows


def _content_cache_path(media: dict[str, Any], model: str) -> Path:
    key = hashlib.sha1(
        f"{media.get('sha256')}|{media.get('file_size')}|{model}|sample-v1".encode("utf-8")
    ).hexdigest()
    return CONTENT_MATCH_CACHE_DIR / f"{key}.json"


def _transcript_text(payload: dict[str, Any]) -> str:
    sentences = extract_asr_sentences(payload)
    if sentences:
        return clean_text(" ".join(item["text"] for item in sentences))
    values: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() in {"text", "transcript", "transcription"} and isinstance(value, str):
                    cleaned = clean_text(value)
                    if len(cleaned.split()) >= 5:
                        values.append(cleaned)
                elif isinstance(value, (dict, list)):
                    walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return clean_text(" ".join(dict.fromkeys(values)))


def _transcribe_content_sample(
    media: dict[str, Any],
    progress: Callable[[int, str], None] | None = None,
) -> tuple[str, bool]:
    asr = qwen_asr_config()
    cache_path = _content_cache_path(media, asr["model"])
    cached = load_json(cache_path, {})
    if len(str(cached.get("transcript") or "").split()) >= 8:
        return str(cached["transcript"]), True
    oss = oss_config()
    if not asr.get("api_key"):
        raise PipelineError(f"{asr['api_key_env']} 未配置，无法进行正文核验。")
    if not all(oss.get(key) for key in ("access_key_id", "access_key_secret", "bucket", "endpoint")):
        raise PipelineError("正文核验需要在设置中配置 OSS，用于向 ASR 提供临时音频片段。")

    sample_path = cache_path.with_suffix(".sample.wav")
    object_key = f"{oss['temp_prefix']}content-match-{cache_path.stem}.wav"
    bucket = None
    task_id = ""
    try:
        if progress:
            progress(8, "抽取开头和正文中段样本…")
        windows = _extract_content_sample(media, sample_path)
        if progress:
            progress(20, "上传临时音频样本…")
        bucket, signed_url = upload_temp_audio_file_to_oss(sample_path, object_key, "audio/wav", expires=1800)
        if progress:
            progress(30, "提交抽样语音识别…")
        task_id, meta = start_qwen_filetranscription(signed_url, enable_words=False)

        def tick(elapsed: int, status: str) -> None:
            if progress:
                progress(35 + min(50, int(elapsed / 180 * 50)), f"识别中 · {status}")

        task_result = poll_qwen_asr_task(
            task_id, asr["base_url"], asr["api_key"], timeout_seconds=240, on_tick=tick
        )
        transcript_payload = fetch_qwen_transcription(task_result)
        transcript = _transcript_text(transcript_payload)
        if len(transcript.split()) < 8:
            raise PipelineError("抽样识别没有返回足够的英文正文。")
        save_json(cache_path, {
            "media_id": media["id"],
            "sha256": media.get("sha256"),
            "transcript": transcript,
            "windows": windows,
            "meta": {**meta, "task_id": task_id, "algorithm": "sample-v1"},
            "created_at": now_iso(),
        })
        if progress:
            progress(95, "正文样本识别完成")
        return transcript, False
    finally:
        sample_path.unlink(missing_ok=True)
        if bucket is not None:
            try:
                bucket.delete_object(object_key)
            except Exception:
                pass


def _run_content_pairing(
    source_id: str,
    collection_id: str,
    progress_cb: Callable[[str, int, str], None],
) -> dict[str, Any]:
    base = preview_content_pairing(source_id, collection_id)
    source = content_matching_source(source_id)
    selected_ids = content_verification_media_ids(base)
    if not selected_ids:
        base["content_verification"] = {
            "requested": 0, "transcribed": 0, "cached": 0, "failed": 0,
            "message": "现有元数据匹配已经全部达到高置信度，无需调用 ASR。",
        }
        return base
    transcripts: dict[str, str] = {}
    failures: list[str] = []
    cached_count = 0
    total = len(selected_ids)
    for index, media_id in enumerate(selected_ids):
        media = _content_match_media(media_id)
        title = str(media.get("title") or media.get("original_name") or media_id)
        base_pct = int(index / total * 88)

        def item_progress(local_pct: int, message: str) -> None:
            overall = base_pct + int(local_pct / max(1, total) * 0.88)
            progress_cb("content_asr", min(90, overall), f"{index + 1}/{total} · {title} · {message}")

        try:
            transcript, cached = _transcribe_content_sample(media, item_progress)
            transcripts[media_id] = transcript
            cached_count += int(cached)
        except Exception as exc:
            failures.append(f"{title}: {str(exc)[:180]}")
    if not transcripts:
        raise PipelineError(failures[0] if failures else "没有得到可用于正文核验的音频文本。")
    progress_cb("content_compare", 94, "比较音频样本与候选文章正文…")
    result = apply_content_transcripts(base, source, transcripts)
    result["content_verification"] = {
        "requested": total,
        "transcribed": len(transcripts),
        "cached": cached_count,
        "failed": len(failures),
        "errors": failures[:10],
        "provider": "qwen",
        "method": "three-window-sample-v1",
    }
    return result


def _start_content_pairing_job(
    source_id: str,
    collection_id: str,
    *,
    requested: int | None = None,
) -> dict[str, Any]:
    """Start or reuse the background ASR verification for an imported issue."""
    key = stable_id("content-pairing", source_id, collection_id, "sample-v1")
    existing = find_job_by_key("content_pairing", key)
    if existing:
        return {
            "task_id": existing["task_id"],
            "status": "running",
            "existing": True,
            "requested": requested,
        }
    task_id = create_job("content_pairing", key=key)

    def worker() -> None:
        try:
            def progress(stage: str, pct: int, msg: str) -> None:
                update_job(task_id, stage=stage, pct=pct, msg=msg)
            result = _run_content_pairing(source_id, collection_id, progress)
            finish_job(task_id, result=result)
        except HTTPException as exc:
            finish_job(task_id, error=str(exc.detail))
        except Exception as exc:
            finish_job(task_id, error=str(exc))

    threading.Thread(target=worker, daemon=True, name=f"content-pair-{task_id[:8]}").start()
    return {
        "task_id": task_id,
        "status": "started",
        "existing": False,
        "requested": requested,
    }


@app.post("/api/v1/content-links/content-match/start")
def content_pairing_start(spec: PairPreviewRequest, request: Request) -> dict[str, Any]:
    current_user(request)
    # Validate both ids before creating a background task.
    preview_content_pairing(spec.source_id, spec.collection_id)
    started = _start_content_pairing_job(spec.source_id, spec.collection_id)
    return {**started, "cached": False}


@app.get("/api/v1/content-links/content-match/status/{task_id}")
def content_pairing_status(task_id: str, request: Request) -> dict[str, Any]:
    current_user(request)
    job = get_job(task_id)
    if not job or job.get("kind") != "content_pairing":
        raise HTTPException(404, "正文核验任务不存在或已经过期。")
    payload = {
        "task_id": task_id,
        "stage": job["stage"],
        "pct": job["pct"],
        "msg": job["msg"],
        "finished_at": job["finished_at"],
    }
    if job.get("error"):
        payload["error"] = job["error"]
    if job.get("result"):
        payload["result"] = job["result"]
    return payload

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(media_router)
app.include_router(content_links_router)
app.include_router(listening_practice_router)
app.include_router(vocabulary_router)
app.include_router(webdav_api_router)
app.router.routes.extend(webdav_routes)
app.add_middleware(SessionAuthMiddleware)

app.mount("/audio", StaticFiles(directory=AUDIO_CACHE_DIR), name="audio")
app.mount("/covers", StaticFiles(directory=COVERS_DIR), name="covers")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
