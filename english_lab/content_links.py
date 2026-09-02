from __future__ import annotations

import difflib
import json
import re
import sqlite3
import uuid
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import current_user
from .config import config
from .database import connect, transaction, utc_now


router = APIRouter(prefix="/api/v1/content-links", tags=["content-links"])
LIBRARY_PATH = config.data_root / "library.json"


class PairPreviewRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)
    collection_id: str = Field(min_length=1, max_length=200)


class PairLink(BaseModel):
    article_id: str = Field(min_length=1, max_length=200)
    media_id: str = Field(min_length=1, max_length=200)
    match_method: str = Field(default="automatic", max_length=32)
    confidence: float = Field(default=0.0, ge=0, le=1)


class PairConfirmRequest(PairPreviewRequest):
    links: list[PairLink] = Field(default_factory=list, max_length=10_000)


class ManualLinkRequest(BaseModel):
    source_id: str = Field(min_length=1, max_length=200)
    media_id: str = Field(min_length=1, max_length=200)


def _load_library() -> dict[str, Any]:
    try:
        return json.loads(LIBRARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"sources": []}


def _source(source_id: str) -> dict[str, Any]:
    source = next((item for item in _load_library().get("sources", []) if item.get("id") == source_id), None)
    if not source:
        raise HTTPException(404, "文章来源不存在。")
    return source


def _issue_key(value: str) -> str:
    text = str(value or "")
    match = re.search(r"(20\d{2})[-_. ]?(0[1-9]|1[0-2])[-_. ]?([0-2]\d|3[01])", text)
    if match:
        return "-".join(match.groups())
    months = {
        name.lower(): index
        for index, name in enumerate(
            [
                "January", "February", "March", "April", "May", "June",
                "July", "August", "September", "October", "November", "December",
            ],
            1,
        )
    }
    named = re.search(
        r"\b(" + "|".join(months) + r")[\s._-]+([0-3]?\d)(?:st|nd|rd|th)?[\s,._-]+(20\d{2})\b",
        text,
        re.I,
    )
    if not named:
        return ""
    month, day, year = named.groups()
    return f"{year}-{months[month.lower()]:02d}-{int(day):02d}"


def _canonical_section(value: str) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _track_number(value: str) -> int:
    match = re.match(r"\s*(\d{1,4})", str(value or ""))
    return int(match.group(1)) if match else 1_000_000


def _media_parts(item: dict[str, Any], previous_section: str = "") -> tuple[str, str, bool]:
    title = str(item.get("title") or item.get("original_name") or "").strip()
    without_track = re.sub(r"^\s*\d{1,4}\s*", "", title, count=1)
    if " - " in without_track:
        section, label = without_track.split(" - ", 1)
        return section.strip(), label.strip(), False
    return previous_section, without_track.strip(), bool(previous_section)


def _title_score(article_title: str, audio_label: str) -> float:
    def normalized(value: str) -> str:
        value = value.lower().replace("’", "'")
        value = re.sub(r"[^a-z0-9]+", " ", value)
        return re.sub(r"\s+", " ", value).strip()

    left, right = normalized(article_title), normalized(audio_label)
    if not left or not right:
        return 0.0
    sequence = difflib.SequenceMatcher(None, left, right).ratio()
    stop = {"the", "a", "an", "and", "or", "to", "of", "for", "in", "on", "is", "are", "why", "how"}
    left_tokens = {token for token in left.split() if token not in stop}
    right_tokens = {token for token in right.split() if token not in stop}
    overlap = len(left_tokens & right_tokens) / max(1, len(left_tokens | right_tokens))
    return max(sequence, overlap)


def _duration_score(article: dict[str, Any], media: dict[str, Any]) -> float:
    """Return plausibility of the spoken duration for the article word count."""
    stats = article.get("cleaned_stats") or article.get("stats") or {}
    try:
        word_count = int(stats.get("word_count") or 0)
        minutes = float(media.get("duration_ms") or 0) / 60_000
    except (TypeError, ValueError):
        return 0.0
    if word_count < 80 or minutes <= 0:
        return 0.0
    words_per_minute = word_count / minutes
    if 125 <= words_per_minute <= 205:
        return 1.0
    if 95 <= words_per_minute < 125 or 205 < words_per_minute <= 235:
        return 0.65
    if 70 <= words_per_minute < 95 or 235 < words_per_minute <= 270:
        return 0.3
    return 0.0


def _alignment_score(
    article: dict[str, Any],
    media: dict[str, Any],
    article_index: int,
    media_index: int,
) -> float:
    """Score a monotonic article/audio pair, including weak editorial labels."""
    title = _title_score(str(article.get("title") or ""), str(media.get("audio_label") or ""))
    duration = _duration_score(article, media)
    distance = abs(article_index - media_index)
    order = max(0.0, 0.12 - distance * 0.05)
    return title * 0.65 + duration * 0.23 + order


def _monotonic_section_alignment(
    articles: list[dict[str, Any]],
    media: list[dict[str, Any]],
) -> dict[int, tuple[int, float]]:
    """Align a section in order while allowing omitted print or audio items.

    Audio-edition labels are often rubrics (for example ``Our cover``) rather
    than article headlines. A sequence alignment lets order and duration carry
    those weak-title cases without shifting the rest of a section when one
    visual-only print article has no audio.
    """
    article_count, media_count = len(articles), len(media)
    if not article_count or not media_count:
        return {}
    negative = float("-inf")
    scores = [[negative] * (media_count + 1) for _ in range(article_count + 1)]
    previous: list[list[tuple[int, int, str] | None]] = [
        [None] * (media_count + 1) for _ in range(article_count + 1)
    ]
    scores[0][0] = 0.0
    for article_index in range(article_count + 1):
        for media_index in range(media_count + 1):
            current = scores[article_index][media_index]
            if current == negative:
                continue
            if article_index < article_count:
                candidate = current - 0.04
                if candidate > scores[article_index + 1][media_index]:
                    scores[article_index + 1][media_index] = candidate
                    previous[article_index + 1][media_index] = (article_index, media_index, "article-gap")
            if media_index < media_count:
                candidate = current - 0.08
                if candidate > scores[article_index][media_index + 1]:
                    scores[article_index][media_index + 1] = candidate
                    previous[article_index][media_index + 1] = (article_index, media_index, "media-gap")
            if article_index < article_count and media_index < media_count:
                quality = _alignment_score(
                    articles[article_index], media[media_index], article_index, media_index
                )
                candidate = current + 0.24 + quality
                if candidate > scores[article_index + 1][media_index + 1]:
                    scores[article_index + 1][media_index + 1] = candidate
                    previous[article_index + 1][media_index + 1] = (article_index, media_index, "match")

    aligned: dict[int, tuple[int, float]] = {}
    article_index, media_index = article_count, media_count
    while article_index or media_index:
        step = previous[article_index][media_index]
        if step is None:
            break
        old_article, old_media, action = step
        if action == "match":
            aligned[old_article] = (
                old_media,
                _alignment_score(articles[old_article], media[old_media], old_article, old_media),
            )
        article_index, media_index = old_article, old_media
    return aligned


def _collection_items(collection_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    with connect() as connection:
        collection = connection.execute("SELECT * FROM collections WHERE id = ?", (collection_id,)).fetchone()
        if not collection:
            raise HTTPException(404, "音频集合不存在。")
        rows = connection.execute(
            """SELECT id, collection_id, title, original_name, relative_path, duration_ms
               FROM media_items
               WHERE collection_id = ? AND deleted_at IS NULL""",
            (collection_id,),
        ).fetchall()
    items = [dict(row) for row in rows]
    items.sort(key=lambda item: (_track_number(item.get("title") or item.get("original_name")), str(item.get("title") or "").lower()))
    return dict(collection), items


def _saved_links(source_id: str) -> dict[str, dict[str, Any]]:
    with connect() as connection:
        rows = connection.execute(
            """SELECT links.*, media_items.title AS media_title,
                      media_items.original_name, media_items.collection_id
               FROM article_media_links AS links
               JOIN media_items ON media_items.id = links.media_id
               WHERE links.article_source_id = ? AND media_items.deleted_at IS NULL""",
            (source_id,),
        ).fetchall()
    return {row["article_id"]: dict(row) for row in rows}


def _automatic_candidates(source: dict[str, Any], collection: dict[str, Any], media: list[dict[str, Any]]) -> list[dict[str, Any]]:
    article_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    section_labels: dict[str, str] = {}
    for article in source.get("articles", []):
        section = str(article.get("section") or "Articles")
        key = _canonical_section(section)
        section_labels[key] = section
        article_groups[key].append(article)

    media_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    previous_section = ""
    for item in media:
        section, label, inferred = _media_parts(item, previous_section)
        if section:
            previous_section = section
        enriched = {**item, "parsed_section": section, "audio_label": label, "section_inferred": inferred}
        media_groups[_canonical_section(section)].append(enriched)

    issue_matches = bool(_issue_key(source.get("filename", ""))) and _issue_key(source.get("filename", "")) == _issue_key(collection.get("name", ""))
    result: list[dict[str, Any]] = []
    for section_key, articles in article_groups.items():
        available = media_groups.get(section_key, [])
        equal_counts = bool(available) and len(articles) == len(available)
        alignment = (
            {index: (index, _alignment_score(article, available[index], index, index))
             for index, article in enumerate(articles)}
            if equal_counts
            else _monotonic_section_alignment(articles, available)
        )
        for index, article in enumerate(articles):
            aligned = alignment.get(index)
            chosen_index = aligned[0] if aligned else None
            alignment_score = aligned[1] if aligned else 0.0
            if chosen_index is None or chosen_index >= len(available):
                result.append({
                    "article_id": article.get("id"), "article_title": article.get("title"),
                    "section": section_labels.get(section_key, "Articles"), "media_id": None,
                    "media_title": None, "confidence": 0.0, "match_method": "unmatched",
                    "status": "unmatched", "confirmed": False,
                    "reason": "没有找到同栏目且足够可靠的音频，请手动选择。",
                })
                continue
            item = available[chosen_index]
            title_score = _title_score(str(article.get("title") or ""), str(item.get("audio_label") or ""))
            confidence = 0.78 if equal_counts else 0.7
            reason = "同栏目内按顺序匹配" if equal_counts else "同栏目内按顺序对齐，栏目数量不同"
            if equal_counts:
                confidence += 0.08
                reason += "，栏目数量一致"
            elif alignment_score >= 0.3:
                confidence += 0.05
                reason += "，标题/时长支持该位置"
            else:
                reason += "，建议人工复核"
            if issue_matches:
                confidence += 0.04
            if title_score >= 0.2:
                confidence += 0.04
                reason += "，标题关键词相符"
            duration_score = _duration_score(article, item)
            if duration_score >= 0.65:
                confidence += 0.03
                reason += "，篇幅与时长相符"
            elif duration_score == 0 and (article.get("stats") or {}).get("word_count") and item.get("duration_ms"):
                confidence -= 0.08
                reason += "；篇幅与时长需要复核"
            if item.get("section_inferred"):
                confidence -= 0.22
                reason += "；音频栏目由相邻曲目推断"
            if not equal_counts and title_score < 0.45:
                confidence = min(confidence, 0.79)
                reason += "；栏目数量或标题不完全一致，请确认"
            confidence = max(0.0, min(0.99, confidence))
            result.append({
                "article_id": article.get("id"), "article_title": article.get("title"),
                "section": section_labels.get(section_key, "Articles"), "media_id": item.get("id"),
                "media_title": item.get("title"), "confidence": round(confidence, 2),
                "metadata_title_score": round(title_score, 3),
                "match_method": "automatic", "status": "matched" if confidence >= 0.8 else "review",
                "confirmed": False, "reason": reason,
            })
    return result


_CONTENT_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "for", "from", "had", "has", "have", "he", "her", "his", "in", "is", "it",
    "its", "of", "on", "or", "she", "that", "the", "their", "them", "they", "this",
    "to", "was", "were", "will", "with", "would", "you",
}


def _content_tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+(?:'[a-z]+)?", str(value or "").lower().replace("’", "'"))


def _ngrams(tokens: list[str], size: int) -> set[tuple[str, ...]]:
    return {tuple(tokens[index:index + size]) for index in range(max(0, len(tokens) - size + 1))}


def content_similarity(transcript: str, article_text: str) -> float:
    """Measure whether a noisy ASR excerpt occurs in an article's body."""
    heard = _content_tokens(transcript)
    article = _content_tokens(article_text)
    if len(heard) < 8 or len(article) < 20:
        return 0.0
    heard_bigrams, article_bigrams = _ngrams(heard, 2), _ngrams(article, 2)
    heard_trigrams, article_trigrams = _ngrams(heard, 3), _ngrams(article, 3)
    bigram_precision = len(heard_bigrams & article_bigrams) / max(1, len(heard_bigrams))
    trigram_precision = len(heard_trigrams & article_trigrams) / max(1, len(heard_trigrams))
    heard_keywords = {token for token in heard if token not in _CONTENT_STOPWORDS and len(token) > 2}
    article_keywords = set(article)
    keyword_coverage = len(heard_keywords & article_keywords) / max(1, len(heard_keywords))

    # ASR commonly corrupts an isolated word. Bigrams and keyword coverage keep
    # the signal useful while trigrams provide strong evidence for verbatim narration.
    score = max(
        trigram_precision,
        bigram_precision * 0.72 + keyword_coverage * 0.28,
        trigram_precision * 0.7 + bigram_precision * 0.2 + keyword_coverage * 0.1,
    )
    return max(0.0, min(1.0, round(score, 4)))


def media_sections(media: list[dict[str, Any]]) -> dict[str, str]:
    """Return media id -> canonical section, preserving inherited track sections."""
    result: dict[str, str] = {}
    previous_section = ""
    ordered = sorted(
        media,
        key=lambda item: (
            _track_number(item.get("title") or item.get("original_name")),
            str(item.get("title") or "").lower(),
        ),
    )
    for item in ordered:
        section, _label, _inferred = _media_parts(item, previous_section)
        if section:
            previous_section = section
        result[str(item.get("id"))] = _canonical_section(section)
    return result


def content_verification_media_ids(preview: dict[str, Any]) -> list[str]:
    """Select only media in sections that metadata matching could not settle."""
    unmatched_sections = {
        _canonical_section(item.get("section") or "")
        for item in preview.get("candidates", [])
        if item.get("status") == "unmatched" and not item.get("confirmed")
    }
    options = preview.get("media_options", [])
    sections = media_sections(options)
    selected_ids = {
        str(item.get("media_id"))
        for item in preview.get("candidates", [])
        if item.get("media_id")
        and not item.get("confirmed")
        and (
            item.get("status") == "review"
            or float(item.get("metadata_title_score") or 0) < 0.35
        )
    }
    selected = []
    for item in options:
        media_id = str(item.get("id"))
        if media_id in selected_ids or sections.get(media_id) in unmatched_sections:
            selected.append(media_id)
    # Keep bounded: sampling is a paid/remote ASR operation when not cached.
    return selected[:30]


def apply_content_transcripts(
    preview: dict[str, Any],
    source: dict[str, Any],
    transcripts: dict[str, str],
) -> dict[str, Any]:
    """Refine a metadata preview with cached/sample ASR body evidence."""
    candidates = preview.get("candidates", [])
    options = preview.get("media_options", [])
    option_by_id = {str(item.get("id")): item for item in options}
    section_by_media = media_sections(options)
    candidate_by_article = {str(item.get("article_id")): item for item in candidates}
    articles = {str(item.get("id")): item for item in source.get("articles", [])}
    confirmed_articles = {
        article_id for article_id, item in candidate_by_article.items() if item.get("confirmed")
    }
    confirmed_media = {
        str(item.get("media_id")) for item in candidates if item.get("confirmed") and item.get("media_id")
    }

    pair_scores: list[tuple[float, float, str, str]] = []
    for media_id, transcript in transcripts.items():
        media_id = str(media_id)
        if media_id in confirmed_media or media_id not in option_by_id:
            continue
        media_section = section_by_media.get(media_id, "")
        ranked: list[tuple[float, str]] = []
        for article_id, article in articles.items():
            if article_id in confirmed_articles:
                continue
            article_section = _canonical_section(article.get("section") or "")
            if media_section and article_section != media_section:
                continue
            body = "\n".join(article.get("cleaned_paragraphs") or article.get("paragraphs") or [])
            score = content_similarity(transcript, body)
            if score > 0:
                ranked.append((score, article_id))
        ranked.sort(reverse=True)
        for rank, (score, article_id) in enumerate(ranked):
            second = ranked[1][0] if len(ranked) > 1 else 0.0
            margin = score - second if rank == 0 else 0.0
            pair_scores.append((score, margin, article_id, media_id))

    assigned_articles = set(confirmed_articles)
    assigned_media = set(confirmed_media)
    accepted: list[tuple[float, float, str, str]] = []
    for score, margin, article_id, media_id in sorted(pair_scores, reverse=True):
        if article_id in assigned_articles or media_id in assigned_media:
            continue
        if score < 0.14 or (margin < 0.025 and score < 0.45):
            continue
        assigned_articles.add(article_id)
        assigned_media.add(media_id)
        accepted.append((score, margin, article_id, media_id))

    accepted_media = {media_id for _score, _margin, _article, media_id in accepted}
    for candidate in candidates:
        if candidate.get("confirmed"):
            continue
        if str(candidate.get("media_id") or "") in accepted_media:
            candidate.update({
                "media_id": None,
                "media_title": None,
                "confidence": 0.0,
                "match_method": "unmatched",
                "status": "unmatched",
                "reason": "正文核验后，该音频与另一篇文章更吻合。",
            })

    for score, margin, article_id, media_id in accepted:
        candidate = candidate_by_article.get(article_id)
        media_item = option_by_id[media_id]
        if not candidate:
            continue
        if score >= 0.55:
            confidence = 0.97
        elif score >= 0.35:
            confidence = 0.92
        elif score >= 0.22:
            confidence = 0.86
        else:
            confidence = 0.79
        candidate.update({
            "media_id": media_id,
            "media_title": media_item.get("title"),
            "confidence": confidence,
            "match_method": "content",
            "status": "matched" if confidence >= 0.82 else "review",
            "confirmed": False,
            "content_score": round(score, 3),
            "reason": (
                f"音频抽样正文与文章匹配（内容 {round(score * 100)}%，"
                f"领先下一候选 {round(max(0.0, margin) * 100)}%）"
            ),
        })

    preview["summary"] = {
        "articles": len(candidates),
        "audio": len(options),
        "matched": sum(item.get("status") in {"matched", "confirmed"} for item in candidates),
        "review": sum(item.get("status") == "review" for item in candidates),
        "unmatched": sum(not item.get("media_id") for item in candidates),
        "confirmed": sum(bool(item.get("confirmed")) for item in candidates),
        "content_verified": len(accepted),
    }
    return preview


def _preview(source_id: str, collection_id: str) -> dict[str, Any]:
    source = _source(source_id)
    collection, media = _collection_items(collection_id)
    candidates = _automatic_candidates(source, collection, media)
    saved = _saved_links(source_id)
    media_by_id = {item["id"]: item for item in media}
    for candidate in candidates:
        stored = saved.get(str(candidate["article_id"]))
        if stored and stored.get("media_id") in media_by_id:
            candidate.update({
                "media_id": stored["media_id"], "media_title": stored["media_title"],
                "confidence": float(stored["confidence"]), "match_method": stored["match_method"],
                "status": "confirmed", "confirmed": bool(stored["confirmed"]),
                "reason": (
                    "已保存的人工确认关系" if stored["match_method"] == "manual"
                    else "已确认的正文核验关系" if stored["match_method"] == "content"
                    else "已确认的自动匹配关系"
                ),
            })

    # Saved/manual relationships win. Remove duplicate automatic suggestions so
    # the UI never silently assigns one audio file to two articles.
    occupied: set[str] = set()
    ordered = sorted(candidates, key=lambda item: (not item.get("confirmed"), -float(item.get("confidence") or 0)))
    for candidate in ordered:
        media_id = candidate.get("media_id")
        if not media_id:
            continue
        if media_id in occupied:
            candidate.update({"media_id": None, "media_title": None, "confidence": 0.0, "match_method": "unmatched", "status": "unmatched", "reason": "候选音频已被其他文章占用，请手动选择。"})
        else:
            occupied.add(media_id)

    media_options = [{
        **item,
        "track_number": _track_number(item.get("title") or item.get("original_name")),
        "stream_url": f"/api/v1/media/items/{item['id']}/stream",
    } for item in media]
    summary = {
        "articles": len(candidates), "audio": len(media),
        "matched": sum(item["status"] in {"matched", "confirmed"} for item in candidates),
        "review": sum(item["status"] == "review" for item in candidates),
        "unmatched": sum(not item.get("media_id") for item in candidates),
        "confirmed": sum(bool(item.get("confirmed")) for item in candidates),
    }
    return {
        "source": {"id": source_id, "filename": source.get("filename"), "issue_key": _issue_key(source.get("filename", ""))},
        "collection": {"id": collection_id, "name": collection.get("name"), "issue_key": _issue_key(collection.get("name", ""))},
        "candidates": candidates, "media_options": media_options, "summary": summary,
    }


def preview_content_pairing(source_id: str, collection_id: str) -> dict[str, Any]:
    """Public service entry point used by the unified issue importer."""
    return _preview(source_id, collection_id)


def content_matching_source(source_id: str) -> dict[str, Any]:
    """Return the full article source for the content-verification pipeline."""
    return _source(source_id)


@router.get("/options")
def pairing_options(request: Request) -> dict[str, Any]:
    current_user(request)
    library = _load_library()
    with connect() as connection:
        rows = connection.execute(
            """SELECT collections.id, collections.name, COUNT(media_items.id) AS item_count
               FROM collections
               LEFT JOIN media_items ON media_items.collection_id = collections.id AND media_items.deleted_at IS NULL
               GROUP BY collections.id
               ORDER BY collections.name COLLATE NOCASE"""
        ).fetchall()
        bundles = connection.execute("SELECT * FROM content_bundle_links").fetchall()
    collections = [dict(row) for row in rows]
    bundle_by_source = {row["article_source_id"]: dict(row) for row in bundles}
    sources = []
    for source in library.get("sources", []):
        issue = _issue_key(source.get("filename", ""))
        suggested = next((item["id"] for item in collections if issue and _issue_key(item["name"]) == issue), None)
        saved = bundle_by_source.get(source.get("id"))
        sources.append({
            "id": source.get("id"), "filename": source.get("filename"),
            "article_count": len(source.get("articles", [])), "issue_key": issue,
            "suggested_collection_id": saved.get("media_collection_id") if saved else suggested,
        })
    return {"sources": sources, "collections": collections}


@router.post("/preview")
def preview_pairing(spec: PairPreviewRequest, request: Request) -> dict[str, Any]:
    current_user(request)
    return _preview(spec.source_id, spec.collection_id)


@router.post("/confirm")
def confirm_pairing(spec: PairConfirmRequest, request: Request) -> dict[str, Any]:
    current_user(request)
    source = _source(spec.source_id)
    collection, media = _collection_items(spec.collection_id)
    valid_articles = {str(item.get("id")) for item in source.get("articles", [])}
    valid_media = {str(item.get("id")) for item in media}
    article_ids = [item.article_id for item in spec.links]
    media_ids = [item.media_id for item in spec.links]
    if len(article_ids) != len(set(article_ids)) or len(media_ids) != len(set(media_ids)):
        raise HTTPException(400, "一篇文章和一条音频都只能出现一次。")
    if any(item not in valid_articles for item in article_ids):
        raise HTTPException(400, "提交中包含不属于所选来源的文章。")
    if any(item not in valid_media for item in media_ids):
        raise HTTPException(400, "提交中包含不属于所选集合的音频。")

    now = utc_now()
    issue = _issue_key(source.get("filename", "")) or _issue_key(collection.get("name", ""))
    confidence = sum(item.confidence for item in spec.links) / max(1, len(spec.links))
    with transaction() as connection:
        conflict = connection.execute(
            "SELECT article_source_id FROM content_bundle_links WHERE media_collection_id = ? AND article_source_id <> ?",
            (spec.collection_id, spec.source_id),
        ).fetchone()
        if conflict:
            raise HTTPException(409, "该音频集合已经与另一个文章来源配套。")
        if media_ids:
            placeholders = ",".join("?" for _ in media_ids)
            occupied = connection.execute(
                f"""SELECT article_id FROM article_media_links
                    WHERE media_id IN ({placeholders}) AND article_source_id <> ? LIMIT 1""",
                [*media_ids, spec.source_id],
            ).fetchone()
            if occupied:
                raise HTTPException(409, "提交中的音频已经与另一个文章来源配套。")
        existing = connection.execute("SELECT id, created_at FROM content_bundle_links WHERE article_source_id = ?", (spec.source_id,)).fetchone()
        if existing:
            connection.execute(
                """UPDATE content_bundle_links SET media_collection_id = ?, issue_key = ?,
                          match_method = ?, confidence = ?, updated_at = ? WHERE article_source_id = ?""",
                (spec.collection_id, issue, "manual-review", confidence, now, spec.source_id),
            )
        else:
            connection.execute(
                """INSERT INTO content_bundle_links(id, article_source_id, media_collection_id, issue_key,
                          match_method, confidence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, spec.source_id, spec.collection_id, issue, "manual-review", confidence, now, now),
            )
        connection.execute("DELETE FROM article_media_links WHERE article_source_id = ?", (spec.source_id,))
        for item in spec.links:
            method = item.match_method if item.match_method in {"manual", "content"} else "automatic"
            connection.execute(
                """INSERT INTO article_media_links(article_id, article_source_id, media_id, match_method,
                          confidence, confirmed, created_at, updated_at) VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
                (item.article_id, spec.source_id, item.media_id, method, item.confidence, now, now),
            )
    result = _preview(spec.source_id, spec.collection_id)
    result["message"] = f"已保存 {len(spec.links)} 组文章与音频关系。"
    return result


@router.put("/articles/{article_id}")
def set_manual_link(article_id: str, spec: ManualLinkRequest, request: Request) -> dict[str, Any]:
    current_user(request)
    source = _source(spec.source_id)
    if article_id not in {str(item.get("id")) for item in source.get("articles", [])}:
        raise HTTPException(404, "文章不属于所选来源。")
    with connect() as connection:
        media = connection.execute(
            "SELECT id, collection_id FROM media_items WHERE id = ? AND deleted_at IS NULL", (spec.media_id,),
        ).fetchone()
    if not media:
        raise HTTPException(404, "音频不存在。")
    now = utc_now()
    try:
        with transaction() as connection:
            connection.execute("DELETE FROM article_media_links WHERE article_id = ?", (article_id,))
            connection.execute(
                """INSERT INTO article_media_links(article_id, article_source_id, media_id, match_method,
                          confidence, confirmed, created_at, updated_at) VALUES (?, ?, ?, 'manual', 1, 1, ?, ?)""",
                (article_id, spec.source_id, spec.media_id, now, now),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(409, "该音频已经与另一篇文章配套。") from exc
    return {"ok": True, "linked_media": linked_media_for_article(article_id)}


@router.delete("/articles/{article_id}")
def remove_article_link(article_id: str, request: Request) -> dict[str, bool]:
    current_user(request)
    with transaction() as connection:
        connection.execute("DELETE FROM article_media_links WHERE article_id = ?", (article_id,))
    return {"ok": True}


def linked_media_for_article(article_id: str) -> dict[str, Any] | None:
    with connect() as connection:
        row = connection.execute(
            """SELECT media_items.id, media_items.title, media_items.original_name,
                      media_items.duration_ms, collections.name AS collection_name,
                      article_media_links.match_method, article_media_links.confidence,
                      article_media_links.confirmed
               FROM article_media_links
               JOIN media_items ON media_items.id = article_media_links.media_id
               LEFT JOIN collections ON collections.id = media_items.collection_id
               WHERE article_media_links.article_id = ? AND media_items.deleted_at IS NULL""",
            (article_id,),
        ).fetchone()
    if not row:
        return None
    payload = dict(row)
    payload["confirmed"] = bool(payload["confirmed"])
    payload["stream_url"] = f"/api/v1/media/items/{payload['id']}/stream"
    return payload


def linked_media_source_for_article(article_id: str) -> dict[str, Any] | None:
    """Return the linked original audio plus its validated server-side path."""
    with connect() as connection:
        row = connection.execute(
            """SELECT media_items.id, media_items.title, media_items.original_name,
                      media_items.storage_path, media_items.mime_type, media_items.sha256,
                      media_items.duration_ms, media_items.file_size
               FROM article_media_links
               JOIN media_items ON media_items.id = article_media_links.media_id
               WHERE article_media_links.article_id = ? AND media_items.deleted_at IS NULL""",
            (article_id,),
        ).fetchone()
    if not row:
        return None
    payload = dict(row)
    path = (config.media_root / payload.pop("storage_path")).resolve()
    if not path.is_relative_to(config.media_root) or not path.is_file():
        raise HTTPException(404, "配套原版音频文件不存在。")
    payload["path"] = path
    payload["stream_url"] = f"/api/v1/media/items/{payload['id']}/stream"
    return payload


def linked_media_summaries(article_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not article_ids:
        return {}
    placeholders = ",".join("?" for _ in article_ids)
    with connect() as connection:
        rows = connection.execute(
            f"""SELECT article_media_links.article_id, media_items.id, media_items.title,
                       media_items.duration_ms
                FROM article_media_links
                JOIN media_items ON media_items.id = article_media_links.media_id
                WHERE article_media_links.article_id IN ({placeholders}) AND media_items.deleted_at IS NULL""",
            article_ids,
        ).fetchall()
    return {
        row["article_id"]: {
            "id": row["id"], "title": row["title"], "duration_ms": row["duration_ms"],
            "stream_url": f"/api/v1/media/items/{row['id']}/stream",
        }
        for row in rows
    }


def remove_source_links(source_id: str) -> None:
    with transaction() as connection:
        connection.execute("DELETE FROM article_media_links WHERE article_source_id = ?", (source_id,))
        connection.execute("DELETE FROM content_bundle_links WHERE article_source_id = ?", (source_id,))
