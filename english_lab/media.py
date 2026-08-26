from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import re
import shutil
import sqlite3
import subprocess
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .auth import current_user
from .config import config, ensure_server_dirs
from .database import connect, transaction, utc_now


router = APIRouter(prefix="/api/v1/media", tags=["media"])
_SCAN_CREATE_LOCK = threading.Lock()

SUPPORTED_AUDIO_EXTENSIONS = {
    ".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".opus", ".m4b",
}
SORT_COLUMNS = {
    "track": "CASE WHEN media_items.title GLOB '[0-9]*' THEN CAST(media_items.title AS INTEGER) ELSE 2147483647 END ASC, media_items.title COLLATE NOCASE ASC",
    "created": "media_items.created_at DESC",
    "title": "media_items.title COLLATE NOCASE ASC",
    "duration": "COALESCE(media_items.duration_ms, 0) DESC",
    "size": "media_items.file_size DESC",
}


class ImportJobCreate(BaseModel):
    total_files: int = Field(default=0, ge=0, le=100_000)
    source: str = Field(default="browser", max_length=500)


class ServerScanRequest(BaseModel):
    relative_path: str = Field(default="", max_length=1000)


class MediaUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = Field(default=None, max_length=10_000)
    difficulty: str | None = Field(default=None, max_length=32)
    tags: list[str] | None = None
    collection_id: str | None = None


class ProgressUpdate(BaseModel):
    position_ms: int = Field(default=0, ge=0)
    playback_rate: float = Field(default=1.0, ge=0.5, le=3.0)
    completed: bool = False


class UploadInit(BaseModel):
    relative_path: str = Field(min_length=1, max_length=2000)
    file_size: int = Field(gt=0)


def _safe_relative_path(value: str, fallback: str = "audio") -> str:
    value = str(value or fallback).replace("\\", "/").lstrip("/")
    parts = [p for p in value.split("/") if p not in {"", ".", ".."}]
    return "/".join(parts) or fallback


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _probe_audio(path: Path) -> dict[str, Any]:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return {"available": False, "tags": {}}
    command = [
        ffprobe,
        "-v", "error",
        "-show_entries", "format=duration,bit_rate,format_name:format_tags:stream=codec_type,sample_rate,channels",
        "-of", "json",
        str(path),
    ]
    try:
        completed = subprocess.run(command, capture_output=True, text=True, timeout=45, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"ffprobe 执行失败：{exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or "无法识别该音频文件").strip()[:500]
        raise ValueError(detail)
    try:
        payload = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError("ffprobe 返回了无效结果") from exc
    audio_stream = next((s for s in payload.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio_stream:
        raise ValueError("文件中没有检测到音频流")
    fmt = payload.get("format") or {}
    duration_ms = None
    try:
        duration_ms = max(0, round(float(fmt.get("duration")) * 1000))
    except (TypeError, ValueError):
        pass
    return {
        "available": True,
        "duration_ms": duration_ms,
        "bitrate": _optional_int(fmt.get("bit_rate")),
        "sample_rate": _optional_int(audio_stream.get("sample_rate")),
        "channels": _optional_int(audio_stream.get("channels")),
        "format_name": fmt.get("format_name", ""),
        "tags": {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()},
    }


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _storage_file(relative_storage: str) -> Path:
    target = (config.media_root / relative_storage).resolve()
    if not target.is_relative_to(config.media_root):
        raise HTTPException(404, "媒体文件不存在。")
    return target


def _ensure_collection(connection: sqlite3.Connection, folder: str) -> str | None:
    folder = folder.strip("/ ")
    if not folder:
        return None
    row = connection.execute("SELECT id FROM collections WHERE name = ?", (folder,)).fetchone()
    if row:
        return row["id"]
    collection_id = uuid.uuid4().hex
    now = utc_now()
    connection.execute(
        "INSERT INTO collections(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (collection_id, folder, now, now),
    )
    return collection_id


def _find_duplicate(sha256: str) -> sqlite3.Row | None:
    with connect() as connection:
        return connection.execute(
            "SELECT id, title, original_name, deleted_at FROM media_items WHERE sha256 = ?",
            (sha256,),
        ).fetchone()


def _import_local_file(source: Path, relative_path: str, *, move_source: bool) -> tuple[str, str, str]:
    """Return status, media id and message for one validated local file."""
    extension = source.suffix.lower()
    if extension not in SUPPORTED_AUDIO_EXTENSIONS:
        raise ValueError(f"不支持的音频格式：{extension or '无扩展名'}")
    if not source.is_file():
        raise ValueError("文件不存在")
    if source.stat().st_size > config.max_upload_bytes:
        raise ValueError("文件超过服务器允许的最大大小")

    digest = _sha256(source)
    duplicate = _find_duplicate(digest)
    if duplicate:
        if move_source:
            source.unlink(missing_ok=True)
        return "duplicate", duplicate["id"], f"与“{duplicate['title']}”内容相同"

    probe = _probe_audio(source)
    tags = probe.get("tags") or {}
    normalized_relative = _safe_relative_path(relative_path, source.name)
    original_name = Path(normalized_relative).name or source.name
    title = str(tags.get("title") or Path(normalized_relative).stem or source.stem).strip()
    parent = str(Path(normalized_relative).parent).replace("\\", "/")
    if parent == ".":
        parent = ""
    media_id = uuid.uuid4().hex
    storage_rel = f"originals/{digest[:2]}/{media_id}{extension}"
    destination = _storage_file(storage_rel)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if move_source:
        os.replace(source, destination)
    else:
        shutil.copy2(source, destination)

    now = utc_now()
    mime_type = mimetypes.guess_type(original_name)[0] or f"audio/{extension.lstrip('.')}"
    try:
        with transaction() as connection:
            collection_id = _ensure_collection(connection, parent)
            connection.execute(
                """INSERT INTO media_items(
                       id, collection_id, title, original_name, relative_path, storage_path,
                       extension, mime_type, file_size, sha256, duration_ms, bitrate,
                       sample_rate, channels, metadata_json, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    media_id,
                    collection_id,
                    title,
                    original_name,
                    normalized_relative,
                    storage_rel,
                    extension,
                    mime_type,
                    destination.stat().st_size,
                    digest,
                    probe.get("duration_ms"),
                    probe.get("bitrate"),
                    probe.get("sample_rate"),
                    probe.get("channels"),
                    json.dumps(probe, ensure_ascii=False),
                    now,
                    now,
                ),
            )
    except sqlite3.IntegrityError:
        destination.unlink(missing_ok=True)
        duplicate = _find_duplicate(digest)
        if duplicate:
            return "duplicate", duplicate["id"], f"与“{duplicate['title']}”内容相同"
        raise
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return "imported", media_id, "导入完成"


def _create_job(user_id: str, kind: str, source: str, total_files: int) -> str:
    job_id = uuid.uuid4().hex
    now = utc_now()
    with transaction() as connection:
        connection.execute(
            """INSERT INTO import_jobs(
                   id, user_id, kind, source, status, total_files, created_at, updated_at
               ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)""",
            (job_id, user_id, kind, source, total_files, now, now),
        )
    return job_id


def _record_job_item(job_id: str, relative_path: str, status: str, media_id: str | None, message: str) -> None:
    counter = {
        "imported": "imported_files",
        "duplicate": "duplicate_files",
        "failed": "failed_files",
    }.get(status)
    with transaction() as connection:
        existing = connection.execute(
            """SELECT id FROM import_job_items
               WHERE job_id = ? AND relative_path = ? AND status = 'pending'
               ORDER BY created_at LIMIT 1""",
            (job_id, relative_path),
        ).fetchone()
        if existing:
            connection.execute(
                """UPDATE import_job_items SET status = ?, media_id = ?, message = ? WHERE id = ?""",
                (status, media_id, message[:1000], existing["id"]),
            )
        else:
            connection.execute(
                """INSERT INTO import_job_items(id, job_id, relative_path, status, media_id, message, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (uuid.uuid4().hex, job_id, relative_path, status, media_id, message[:1000], utc_now()),
            )
        if counter:
            connection.execute(
                f"""UPDATE import_jobs
                    SET status = 'running', processed_files = processed_files + 1,
                        {counter} = {counter} + 1, updated_at = ?
                    WHERE id = ?""",
                (utc_now(), job_id),
            )


def _finish_job(job_id: str, message: str = "导入任务完成") -> None:
    now = utc_now()
    with transaction() as connection:
        connection.execute(
            """UPDATE import_jobs SET status = 'completed', message = ?, updated_at = ?, finished_at = ?
               WHERE id = ?""",
            (message, now, now, job_id),
        )


def _job_payload(job_id: str, user_id: str) -> dict[str, Any]:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM import_jobs WHERE id = ? AND user_id = ?",
            (job_id, user_id),
        ).fetchone()
        if not row:
            raise HTTPException(404, "导入任务不存在。")
        items = connection.execute(
            """SELECT relative_path, status, media_id, message, created_at
               FROM import_job_items WHERE job_id = ? ORDER BY created_at DESC LIMIT 100""",
            (job_id,),
        ).fetchall()
    return {"job": dict(row), "items": [dict(item) for item in items]}


def _media_query(where: str, params: list[Any], order: str, limit: int, offset: int) -> tuple[str, list[Any]]:
    sql = f"""
        SELECT media_items.*, collections.name AS collection_name,
               article_media_links.article_id AS linked_article_id,
               article_media_links.article_source_id AS linked_source_id,
               CASE WHEN favorites.media_id IS NULL THEN 0 ELSE 1 END AS favorite,
               COALESCE(play_progress.position_ms, 0) AS position_ms,
               COALESCE(play_progress.playback_rate, 1.0) AS playback_rate,
               COALESCE(play_progress.completed, 0) AS completed
        FROM media_items
        LEFT JOIN collections ON collections.id = media_items.collection_id
        LEFT JOIN article_media_links ON article_media_links.media_id = media_items.id
        LEFT JOIN favorites ON favorites.media_id = media_items.id AND favorites.user_id = ?
        LEFT JOIN play_progress ON play_progress.media_id = media_items.id AND play_progress.user_id = ?
        WHERE {where}
        ORDER BY {order}
        LIMIT ? OFFSET ?
    """
    return sql, params + [limit, offset]


def _row_payload(row: sqlite3.Row) -> dict[str, Any]:
    payload = dict(row)
    for name, fallback in (("tags_json", []), ("metadata_json", {})):
        try:
            payload[name.removesuffix("_json")] = json.loads(payload.pop(name) or json.dumps(fallback))
        except json.JSONDecodeError:
            payload[name.removesuffix("_json")] = fallback
    payload["favorite"] = bool(payload.get("favorite"))
    payload["completed"] = bool(payload.get("completed"))
    payload["stream_url"] = f"/api/v1/media/items/{payload['id']}/stream"
    return payload


@router.get("/status")
def media_status(request: Request) -> dict[str, Any]:
    current_user(request)
    with connect() as connection:
        counts = connection.execute(
            """SELECT COUNT(*) AS items, COALESCE(SUM(file_size), 0) AS bytes,
                      COALESCE(SUM(CASE WHEN deleted_at IS NOT NULL THEN 1 ELSE 0 END), 0) AS deleted
               FROM media_items"""
        ).fetchone()
    usage = shutil.disk_usage(config.media_root)
    return {
        "items": counts["items"],
        "bytes": counts["bytes"],
        "deleted": counts["deleted"],
        "disk_free": usage.free,
        "ffprobe_available": bool(shutil.which("ffprobe")),
        "import_root": str(config.import_root),
    }


@router.get("/items")
def list_media(
    request: Request,
    query: str = "",
    collection_id: str | None = None,
    favorite: bool = False,
    deleted: bool = False,
    sort: str = "track",
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    user = current_user(request)
    clauses = ["media_items.deleted_at IS NOT NULL" if deleted else "media_items.deleted_at IS NULL"]
    params: list[Any] = [user["id"], user["id"]]
    if query.strip():
        clauses.append("(media_items.title LIKE ? OR media_items.relative_path LIKE ? OR media_items.tags_json LIKE ?)")
        needle = f"%{query.strip()}%"
        params.extend([needle, needle, needle])
    if collection_id:
        clauses.append("media_items.collection_id = ?")
        params.append(collection_id)
    if favorite:
        clauses.append("favorites.media_id IS NOT NULL")
    order = SORT_COLUMNS.get(sort, SORT_COLUMNS["created"])
    sql, query_params = _media_query(" AND ".join(clauses), params, order, limit, offset)
    with connect() as connection:
        rows = connection.execute(sql, query_params).fetchall()
        count_params = params[2:]
        count_sql = f"""SELECT COUNT(*) FROM media_items
                         LEFT JOIN favorites ON favorites.media_id = media_items.id AND favorites.user_id = ?
                         WHERE {' AND '.join(clauses)}"""
        total = connection.execute(count_sql, [user["id"]] + count_params).fetchone()[0]
    return {"items": [_row_payload(row) for row in rows], "total": total, "limit": limit, "offset": offset}


@router.get("/items/{media_id}")
def get_media(media_id: str, request: Request) -> dict[str, Any]:
    user = current_user(request)
    sql, params = _media_query("media_items.id = ?", [user["id"], user["id"], media_id], SORT_COLUMNS["created"], 1, 0)
    with connect() as connection:
        row = connection.execute(sql, params).fetchone()
    if not row:
        raise HTTPException(404, "音频不存在。")
    return {"item": _row_payload(row)}


@router.patch("/items/{media_id}")
def update_media(media_id: str, update: MediaUpdate, request: Request) -> dict[str, Any]:
    current_user(request)
    incoming = update.model_dump(exclude_unset=True)
    fields: list[str] = []
    params: list[Any] = []
    for key in ("title", "description", "difficulty", "collection_id"):
        if key in incoming:
            value = incoming[key]
            if key == "title":
                value = str(value).strip()
            fields.append(f"{key} = ?")
            params.append(value)
    if "tags" in incoming:
        tags = sorted({str(tag).strip() for tag in incoming["tags"] if str(tag).strip()})[:100]
        fields.append("tags_json = ?")
        params.append(json.dumps(tags, ensure_ascii=False))
    if not fields:
        return get_media(media_id, request)
    fields.append("updated_at = ?")
    params.extend([utc_now(), media_id])
    with transaction() as connection:
        cursor = connection.execute(f"UPDATE media_items SET {', '.join(fields)} WHERE id = ?", params)
        if not cursor.rowcount:
            raise HTTPException(404, "音频不存在。")
    return get_media(media_id, request)


@router.delete("/items/{media_id}")
def delete_media(media_id: str, request: Request) -> dict[str, bool]:
    current_user(request)
    with transaction() as connection:
        cursor = connection.execute(
            "UPDATE media_items SET deleted_at = ?, updated_at = ? WHERE id = ? AND deleted_at IS NULL",
            (utc_now(), utc_now(), media_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "音频不存在或已经在回收站。")
    return {"ok": True}


@router.post("/items/{media_id}/restore")
def restore_media(media_id: str, request: Request) -> dict[str, bool]:
    current_user(request)
    with transaction() as connection:
        cursor = connection.execute(
            "UPDATE media_items SET deleted_at = NULL, updated_at = ? WHERE id = ? AND deleted_at IS NOT NULL",
            (utc_now(), media_id),
        )
        if not cursor.rowcount:
            raise HTTPException(404, "回收站中没有该音频。")
    return {"ok": True}


@router.post("/items/{media_id}/favorite")
def toggle_favorite(media_id: str, request: Request) -> dict[str, bool]:
    user = current_user(request)
    with transaction() as connection:
        exists = connection.execute(
            "SELECT 1 FROM favorites WHERE user_id = ? AND media_id = ?",
            (user["id"], media_id),
        ).fetchone()
        if exists:
            connection.execute("DELETE FROM favorites WHERE user_id = ? AND media_id = ?", (user["id"], media_id))
            value = False
        else:
            try:
                connection.execute(
                    "INSERT INTO favorites(user_id, media_id, created_at) VALUES (?, ?, ?)",
                    (user["id"], media_id, utc_now()),
                )
            except sqlite3.IntegrityError as exc:
                raise HTTPException(404, "音频不存在。") from exc
            value = True
    return {"favorite": value}


@router.put("/items/{media_id}/progress")
def save_media_progress(media_id: str, progress: ProgressUpdate, request: Request) -> dict[str, Any]:
    user = current_user(request)
    now = utc_now()
    try:
        with transaction() as connection:
            connection.execute(
                """INSERT INTO play_progress(user_id, media_id, position_ms, playback_rate, completed, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id, media_id) DO UPDATE SET
                       position_ms = excluded.position_ms,
                       playback_rate = excluded.playback_rate,
                       completed = excluded.completed,
                       updated_at = excluded.updated_at""",
                (user["id"], media_id, progress.position_ms, progress.playback_rate, int(progress.completed), now),
            )
    except sqlite3.IntegrityError as exc:
        raise HTTPException(404, "音频不存在。") from exc
    return {"ok": True, "updated_at": now}


@router.api_route("/items/{media_id}/stream", methods=["GET", "HEAD"])
def stream_media(media_id: str, request: Request) -> FileResponse:
    current_user(request)
    with connect() as connection:
        row = connection.execute(
            "SELECT title, original_name, storage_path, mime_type FROM media_items WHERE id = ? AND deleted_at IS NULL",
            (media_id,),
        ).fetchone()
    if not row:
        raise HTTPException(404, "音频不存在。")
    path = _storage_file(row["storage_path"])
    if not path.is_file():
        raise HTTPException(404, "音频文件已经丢失。")
    safe_title = re.sub(r"[\r\n\"\\/]", "_", row["title"]).strip() or row["original_name"]
    response = FileResponse(path, media_type=row["mime_type"], filename=f"{safe_title}{path.suffix}", content_disposition_type="inline")
    response.headers["Cache-Control"] = "private, max-age=3600"
    return response


@router.get("/collections")
def list_collections(request: Request) -> dict[str, Any]:
    current_user(request)
    with connect() as connection:
        rows = connection.execute(
            """SELECT collections.*, COUNT(media_items.id) AS item_count
               FROM collections
               LEFT JOIN media_items ON media_items.collection_id = collections.id AND media_items.deleted_at IS NULL
               GROUP BY collections.id
               ORDER BY collections.sort_order, collections.name COLLATE NOCASE"""
        ).fetchall()
    return {"items": [dict(row) for row in rows]}


@router.post("/imports")
def create_browser_import(request: Request, spec: ImportJobCreate) -> dict[str, str]:
    user = current_user(request)
    return {"job_id": _create_job(user["id"], "browser", spec.source, spec.total_files)}


@router.post("/imports/{job_id}/file")
async def upload_import_file(
    job_id: str,
    request: Request,
    file: UploadFile = File(...),
    relative_path: str = Form(default=""),
) -> dict[str, Any]:
    user = current_user(request)
    _job_payload(job_id, user["id"])
    original_name = Path(file.filename or "audio").name
    extension = Path(original_name).suffix.lower()
    normalized_relative = _safe_relative_path(relative_path or original_name, original_name)
    staging = config.media_root / "staging" / f"{uuid.uuid4().hex}{extension}"
    total = 0
    try:
        with staging.open("wb") as destination:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > config.max_upload_bytes:
                    raise HTTPException(413, "文件超过服务器允许的最大大小。")
                destination.write(chunk)
        status, media_id, message = _import_local_file(staging, normalized_relative, move_source=True)
        _record_job_item(job_id, normalized_relative, status, media_id, message)
        return {"status": status, "media_id": media_id, "message": message}
    except HTTPException:
        staging.unlink(missing_ok=True)
        _record_job_item(job_id, normalized_relative, "failed", None, "上传失败")
        raise
    except Exception as exc:
        staging.unlink(missing_ok=True)
        message = str(exc)[:1000]
        _record_job_item(job_id, normalized_relative, "failed", None, message)
        raise HTTPException(400, f"导入失败：{message}") from exc
    finally:
        await file.close()


@router.post("/imports/{job_id}/uploads")
def initialize_chunked_upload(job_id: str, spec: UploadInit, request: Request) -> dict[str, Any]:
    user = current_user(request)
    _job_payload(job_id, user["id"])
    if spec.file_size > config.max_upload_bytes:
        raise HTTPException(413, "文件超过服务器允许的最大大小。")
    normalized = _safe_relative_path(spec.relative_path)
    extension = Path(normalized).suffix.lower()
    if extension not in SUPPORTED_AUDIO_EXTENSIONS:
        raise HTTPException(400, f"不支持的音频格式：{extension or '无扩展名'}")
    upload_id = uuid.uuid4().hex
    staging_rel = f"staging/{upload_id}{extension}"
    staging = _storage_file(staging_rel)
    staging.parent.mkdir(parents=True, exist_ok=True)
    staging.touch(exist_ok=False)
    now = utc_now()
    with transaction() as connection:
        connection.execute(
            """INSERT INTO upload_sessions(
                   id, job_id, user_id, original_name, relative_path, staging_path,
                   total_bytes, received_bytes, created_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (upload_id, job_id, user["id"], Path(normalized).name, normalized, staging_rel, spec.file_size, now, now),
        )
    return {"upload_id": upload_id, "received_bytes": 0, "chunk_size": 8 * 1024 * 1024}


def _upload_session(upload_id: str, job_id: str, user_id: str) -> sqlite3.Row:
    with connect() as connection:
        row = connection.execute(
            "SELECT * FROM upload_sessions WHERE id = ? AND job_id = ? AND user_id = ?",
            (upload_id, job_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(404, "分片上传会话不存在或已过期。")
    return row


@router.put("/imports/{job_id}/uploads/{upload_id}")
async def upload_chunk(job_id: str, upload_id: str, request: Request) -> dict[str, int]:
    user = current_user(request)
    session = _upload_session(upload_id, job_id, user["id"])
    match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", request.headers.get("content-range", ""))
    if not match:
        raise HTTPException(400, "缺少有效的 Content-Range。")
    start, end, total = (int(value) for value in match.groups())
    if total != session["total_bytes"] or start != session["received_bytes"]:
        raise HTTPException(409, f"分片位置不匹配，服务器已接收 {session['received_bytes']} 字节。")
    chunk = await request.body()
    if not chunk or len(chunk) > 8 * 1024 * 1024 or end - start + 1 != len(chunk):
        raise HTTPException(400, "分片大小无效。")
    staging = _storage_file(session["staging_path"])
    with staging.open("ab") as output:
        output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    received = start + len(chunk)
    with transaction() as connection:
        connection.execute(
            "UPDATE upload_sessions SET received_bytes = ?, updated_at = ? WHERE id = ?",
            (received, utc_now(), upload_id),
        )
    return {"received_bytes": received, "total_bytes": total}


@router.post("/imports/{job_id}/uploads/{upload_id}/complete")
def complete_chunked_upload(job_id: str, upload_id: str, request: Request) -> dict[str, Any]:
    user = current_user(request)
    session = _upload_session(upload_id, job_id, user["id"])
    if session["received_bytes"] != session["total_bytes"]:
        raise HTTPException(409, "文件尚未上传完成。")
    staging = _storage_file(session["staging_path"])
    try:
        status, media_id, message = _import_local_file(staging, session["relative_path"], move_source=True)
        _record_job_item(job_id, session["relative_path"], status, media_id, message)
        return {"status": status, "media_id": media_id, "message": message}
    except Exception as exc:
        staging.unlink(missing_ok=True)
        message = str(exc)[:1000]
        _record_job_item(job_id, session["relative_path"], "failed", None, message)
        raise HTTPException(400, f"导入失败：{message}") from exc
    finally:
        with transaction() as connection:
            connection.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))


@router.post("/imports/{job_id}/complete")
def complete_browser_import(job_id: str, request: Request) -> dict[str, Any]:
    user = current_user(request)
    _job_payload(job_id, user["id"])
    _finish_job(job_id)
    return _job_payload(job_id, user["id"])


@router.get("/imports/{job_id}")
def get_import_job(job_id: str, request: Request) -> dict[str, Any]:
    user = current_user(request)
    return _job_payload(job_id, user["id"])


def _run_server_scan(job_id: str, root: Path, files: list[Path]) -> None:
    for source in files:
        relative = source.relative_to(config.import_root).as_posix()
        try:
            status, media_id, message = _import_local_file(source, relative, move_source=False)
            _record_job_item(job_id, relative, status, media_id, message)
        except Exception as exc:
            _record_job_item(job_id, relative, "failed", None, str(exc)[:1000])
    _finish_job(job_id, "服务器目录扫描完成")


@router.post("/imports/scan")
def scan_server_directory(spec: ServerScanRequest, request: Request) -> dict[str, Any]:
    user = current_user(request)
    relative = Path(spec.relative_path or ".")
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(400, "只能扫描服务器配置的导入目录。")
    root = (config.import_root / relative).resolve()
    if not root.is_relative_to(config.import_root) or not root.is_dir():
        raise HTTPException(404, "导入目录不存在。")
    files = [
        path for path in root.rglob("*")
        if path.is_file() and not path.is_symlink() and path.suffix.lower() in SUPPORTED_AUDIO_EXTENSIONS
        and path.resolve().is_relative_to(config.import_root)
    ]
    with _SCAN_CREATE_LOCK:
        with connect() as connection:
            active = connection.execute(
                """SELECT id FROM import_jobs
                   WHERE kind = 'server_scan' AND status IN ('pending', 'running') LIMIT 1"""
            ).fetchone()
        if active:
            raise HTTPException(409, "已有服务器扫描任务正在运行，请等待它完成。")
        job_id = _create_job(user["id"], "server_scan", str(relative), len(files))
        now = utc_now()
        with transaction() as connection:
            connection.executemany(
                """INSERT INTO import_job_items(id, job_id, relative_path, status, media_id, message, created_at)
                   VALUES (?, ?, ?, 'pending', NULL, '', ?)""",
                [(uuid.uuid4().hex, job_id, path.relative_to(config.import_root).as_posix(), now) for path in files],
            )
        worker = threading.Thread(target=_run_server_scan, args=(job_id, root, files), daemon=True)
        worker.start()
    return {"job_id": job_id, "total_files": len(files)}
