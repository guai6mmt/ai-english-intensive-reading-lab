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
    return "-".join(match.groups()) if match else ""


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
        unused = set(range(len(available)))
        for index, article in enumerate(articles):
            chosen_index: int | None = index if equal_counts else None
            score = 0.0
            if chosen_index is None and available:
                ranked = sorted(
                    ((_title_score(str(article.get("title") or ""), str(item.get("audio_label") or "")), candidate_index)
                     for candidate_index, item in enumerate(available) if candidate_index in unused),
                    reverse=True,
                )
                if ranked and ranked[0][0] >= 0.42:
                    score, chosen_index = ranked[0]
            if chosen_index is None or chosen_index >= len(available):
                result.append({
                    "article_id": article.get("id"), "article_title": article.get("title"),
                    "section": section_labels.get(section_key, "Articles"), "media_id": None,
                    "media_title": None, "confidence": 0.0, "match_method": "unmatched",
                    "status": "unmatched", "confirmed": False,
                    "reason": "没有找到同栏目且足够可靠的音频，请手动选择。",
                })
                continue
            unused.discard(chosen_index)
            item = available[chosen_index]
            score = score or _title_score(str(article.get("title") or ""), str(item.get("audio_label") or ""))
            confidence = 0.78
            reason = "同栏目内按顺序匹配"
            if equal_counts:
                confidence += 0.08
                reason += "，栏目数量一致"
            if issue_matches:
                confidence += 0.04
            if score >= 0.2:
                confidence += 0.04
                reason += "，标题关键词相符"
            if item.get("section_inferred"):
                confidence -= 0.22
                reason += "；音频栏目由相邻曲目推断"
            confidence = max(0.0, min(0.99, confidence))
            result.append({
                "article_id": article.get("id"), "article_title": article.get("title"),
                "section": section_labels.get(section_key, "Articles"), "media_id": item.get("id"),
                "media_title": item.get("title"), "confidence": round(confidence, 2),
                "match_method": "automatic", "status": "matched" if confidence >= 0.8 else "review",
                "confirmed": False, "reason": reason,
            })
    return result


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
                "reason": "已保存的人工确认关系" if stored["match_method"] == "manual" else "已确认的自动匹配关系",
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
            method = "manual" if item.match_method == "manual" else "automatic"
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
