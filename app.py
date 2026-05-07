# -*- coding: utf-8 -*-
from __future__ import annotations

import difflib
import hashlib
import html
import json
import os
import re
import requests
import shutil
import time
import uuid
import zipfile
from collections import Counter
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from bs4 import BeautifulSoup
from docx import Document
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

try:
    from openai import OpenAI
except Exception:  # pragma: no cover - fallback keeps local mode usable.
    OpenAI = None


ROOT = Path(__file__).resolve().parent
DATA_DIR = ROOT / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
STATIC_DIR = ROOT / "static"
LIBRARY_PATH = DATA_DIR / "library.json"
VOCAB_PATH = DATA_DIR / "vocabulary.json"
OUTPUTS_PATH = DATA_DIR / "outputs.json"
PROGRESS_PATH = DATA_DIR / "progress.json"
SETTINGS_PATH = DATA_DIR / "settings.json"

SUPPORTED_EXTENSIONS = {".epub", ".docx", ".txt"}
MAX_AI_CHARS = 10000

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
QWEN_TTS_DEFAULTS = {
    "base_url": os.getenv("QWEN_TTS_BASE_URL", os.getenv("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/api/v1")),
    "model": os.getenv("QWEN_TTS_MODEL", "qwen3-tts-flash"),
    "voice": os.getenv("QWEN_TTS_VOICE", "Ethan"),
    "language_type": os.getenv("QWEN_TTS_LANGUAGE_TYPE", "English"),
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
    yield

app = FastAPI(title="AI English Intensive Reading Lab", lifespan=lifespan)


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


class VocabRequest(BaseModel):
    article_id: str | None = None
    term: str
    translation: str | None = None
    context: str | None = None
    kind: str = "word"
    layer: str | None = None


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


class SpeechRequest(BaseModel):
    text: str
    voice: str | None = None
    language_type: str | None = None


class DictationItemsRequest(BaseModel):
    count: int = 6


class NotesRequest(BaseModel):
    notes: str


def ensure_dirs() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    UPLOAD_DIR.mkdir(exist_ok=True)
    STATIC_DIR.mkdir(exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def save_json(path: Path, data: Any) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


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


def load_settings() -> dict[str, Any]:
    return load_json(SETTINGS_PATH, {})


def save_settings(settings: dict[str, Any]) -> None:
    save_json(SETTINGS_PATH, settings)


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
    return provider if provider in AI_PROVIDERS else default


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


def clean_text(text: str) -> str:
    text = html.unescape(text or "")
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
        article_names = [n for n in names if n.lower().endswith((".html", ".xhtml")) and "/article_" in n.lower()]
        html_names = article_names
        require_article_path = True
        if not article_names:
            html_names = [n for n in names if n.lower().endswith((".html", ".xhtml"))]
            require_article_path = False
        for index, name in enumerate(html_names, 1):
            raw = book.read(name).decode("utf-8", errors="ignore")
            soup = BeautifulSoup(raw, "html.parser")
            for tag in soup(["script", "style", "nav", "footer"]):
                tag.decompose()
            headings = [clean_text(h.get_text(" ")) for h in soup.find_all(["h1", "h2", "h3"])]
            headings = [h for h in headings if h and h.lower() not in {"unknown", "未知"}]
            title = headings[0] if headings else ""
            subtitle = headings[1] if len(headings) > 1 else ""
            section = headings[2] if len(headings) > 2 else infer_section(name)
            paragraphs = filter_article_paragraphs([clean_text(p.get_text(" ")) for p in soup.find_all(["p", "li"])])
            if len(" ".join(paragraphs).split()) < 80:
                paragraphs = infer_paragraphs_from_text(clean_text(soup.get_text(" | ")))
            if len(" ".join(paragraphs).split()) < 80:
                continue
            if not is_valid_article_page(name, title, paragraphs, require_article_path=require_article_path):
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
        settings = load_settings()
        result["image_model"] = settings.get("qwen_image_model", "qwen-vl-plus")
        result["tts"] = {
            "configured": bool(tts["api_key"]),
            "model": tts["model"],
            "voice": tts["voice"],
            "language_type": tts["language_type"],
            "base_url": tts["base_url"],
        }
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
            if value in AI_PROVIDERS:
                settings[key] = value
            continue
        if key.endswith("_api_key") and not value:
            continue
        if key.endswith("_base_url") and value:
            value = normalize_dashscope_api_url(value) if key == "qwen_tts_base_url" else normalize_base_url(value)
        if value:
            settings[key] = value
        elif key in settings:
            del settings[key]
    save_settings(settings)
    return settings_response()


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


@app.post("/api/audio/speech")
def audio_speech(request: SpeechRequest) -> dict[str, Any]:
    settings = load_settings()
    provider = task_provider("audio", settings=settings)
    if provider != "qwen":
        raise HTTPException(400, "当前仅 Qwen 配置了 AI 朗读模型。")
    return synthesize_qwen_speech(request.text, request.voice, request.language_type)


@app.get("/api/library")
def get_library() -> dict[str, Any]:
    library = load_json(LIBRARY_PATH, {"sources": []})
    for source in library.get("sources", []):
        source["articles"] = [enrich_article(a) for a in source.get("articles", [])]
    return {"library": library, "summary": library_summary(library)}


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
    source = {
        "id": source_id,
        "filename": file.filename,
        "stored_path": str(saved_path),
        "content_hash": content_hash,
        "uploaded_at": now_iso(),
        "article_count": len(articles),
        "articles": articles,
    }
    library.setdefault("sources", []).append(source)
    save_json(LIBRARY_PATH, library)
    return {"source": source, "summary": library_summary(library)}


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
    save_json(LIBRARY_PATH, new_library)
    return {"library": new_library, "summary": library_summary(new_library)}


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


@app.get("/api/vocabulary")
def get_vocabulary() -> dict[str, Any]:
    return {"items": load_json(VOCAB_PATH, [])}


@app.post("/api/vocabulary")
def add_vocabulary(item: VocabRequest) -> dict[str, Any]:
    items = load_json(VOCAB_PATH, [])
    key = item.term.strip().lower()
    existing = next((v for v in items if v["term"].lower() == key), None)
    if existing:
        existing["updated_at"] = now_iso()
        if item.context:
            existing.setdefault("contexts", []).append(item.context)
    else:
        existing = {
            "id": stable_id(key, str(time.time())),
            "term": item.term.strip(),
            "translation": item.translation or MINI_GLOSSARY.get(key, ""),
            "kind": item.kind,
            "layer": item.layer or "未分层",
            "article_id": item.article_id,
            "contexts": [item.context] if item.context else [],
            "recognise": False,
            "hear": False,
            "speak": False,
            "write": False,
            "review_count": 0,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
        items.append(existing)
    save_json(VOCAB_PATH, items)
    return {"item": existing, "items": items}


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
        library["sources"] = [s for s in sources if s["id"] != source_id]
    mutate_library(apply)
    lib = load_json(LIBRARY_PATH, {"sources": []})
    return {"library": lib, "summary": library_summary(lib)}


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
