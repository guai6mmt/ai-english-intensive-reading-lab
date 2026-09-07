from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote
from xml.etree import ElementTree as ET

import qrcode
import qrcode.image.svg
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.responses import FileResponse
from starlette.routing import Route

from .auth import current_user
from .config import config
from .database import connect, transaction, utc_now


router = APIRouter(prefix="/api/v1/app-passwords", tags=["remote-access"])
password_hasher = PasswordHasher()
DAV_METHODS = ["OPTIONS", "PROPFIND", "HEAD", "GET", "PUT", "POST", "DELETE", "MKCOL", "COPY", "MOVE"]
READ_METHODS = {"OPTIONS", "PROPFIND", "HEAD", "GET"}
_CACHE_TTL = 300
_CACHE_LOCK = threading.Lock()
_VERIFIED: dict[str, tuple[float, str, str]] = {}


class AppPasswordCreate(BaseModel):
    label: str = Field(min_length=1, max_length=80)


def _dav_enabled() -> bool:
    return config.cookie_secure


def _public_app_password(row: Any) -> dict[str, Any]:
    return {
        "id": row["id"],
        "label": row["label"],
        "scope": row["scope"],
        "created_at": row["created_at"],
        "last_used_at": row["last_used_at"],
        "revoked_at": row["revoked_at"],
    }


@router.get("")
def list_app_passwords(request: Request) -> dict[str, Any]:
    user = current_user(request)
    with connect() as connection:
        rows = connection.execute(
            """SELECT id, label, scope, created_at, last_used_at, revoked_at
               FROM app_passwords WHERE user_id = ? ORDER BY created_at DESC""",
            (user["id"],),
        ).fetchall()
    return {
        "enabled": _dav_enabled(),
        "path": "/dav/",
        "username": user["username"],
        "items": [_public_app_password(row) for row in rows],
    }


@router.post("")
def create_app_password(payload: AppPasswordCreate, request: Request) -> dict[str, Any]:
    if not _dav_enabled():
        raise HTTPException(409, "远程音频访问只允许在 HTTPS 部署中启用。")
    user = current_user(request)
    secret = "el_dav_" + secrets.token_urlsafe(24)
    row_id = uuid.uuid4().hex
    now = utc_now()
    with transaction() as connection:
        connection.execute(
            """INSERT INTO app_passwords(id, user_id, label, password_hash, scope, created_at)
               VALUES (?, ?, ?, ?, 'dav:read', ?)""",
            (row_id, user["id"], payload.label.strip(), password_hasher.hash(secret), now),
        )
    return {
        "item": {"id": row_id, "label": payload.label.strip(), "scope": "dav:read", "created_at": now,
                 "last_used_at": None, "revoked_at": None},
        "username": user["username"],
        "password": secret,
        "path": "/dav/",
        "message": "应用密码只显示这一次，请立即保存。",
    }


@router.delete("/{password_id}")
def revoke_app_password(password_id: str, request: Request) -> dict[str, bool]:
    user = current_user(request)
    with transaction() as connection:
        cursor = connection.execute(
            """UPDATE app_passwords SET revoked_at = ?
               WHERE id = ? AND user_id = ? AND revoked_at IS NULL""",
            (utc_now(), password_id, user["id"]),
        )
        if cursor.rowcount != 1:
            raise HTTPException(404, "应用密码不存在或已吊销。")
    with _CACHE_LOCK:
        for key, value in list(_VERIFIED.items()):
            if value[2] == password_id:
                _VERIFIED.pop(key, None)
    return {"ok": True}


@router.get("/qr")
def remote_url_qr(request: Request) -> Response:
    current_user(request)
    scheme = "https" if _dav_enabled() else request.url.scheme
    host = request.headers.get("host", "").strip()
    url = f"{scheme}://{host}/dav/"
    image = qrcode.make(url, image_factory=qrcode.image.svg.SvgPathImage)
    output = io.BytesIO()
    image.save(output)
    return Response(output.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})


def _unauthorized() -> Response:
    return Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="English Lab", charset="UTF-8"'})


def _authenticate(request: Request) -> dict[str, str] | None:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return None
    digest = hashlib.sha256(header.encode("utf-8")).hexdigest()
    now = time.time()
    with _CACHE_LOCK:
        cached = _VERIFIED.get(digest)
        if cached and cached[0] > now:
            return {"user_id": cached[1], "password_id": cached[2]}
        _VERIFIED.pop(digest, None)
    try:
        decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    username, separator, secret = decoded.partition(":")
    if not separator or not username or not secret or len(secret) > 256:
        return None
    with connect() as connection:
        rows = connection.execute(
            """SELECT app_passwords.id, app_passwords.password_hash, users.id AS user_id
               FROM app_passwords JOIN users ON users.id = app_passwords.user_id
               WHERE users.username = ? COLLATE NOCASE
                 AND app_passwords.scope = 'dav:read' AND app_passwords.revoked_at IS NULL""",
            (username,),
        ).fetchall()
    matched = None
    for row in rows:
        try:
            if password_hasher.verify(row["password_hash"], secret):
                matched = row
                break
        except (VerifyMismatchError, InvalidHashError):
            continue
    if not matched:
        return None
    with connect() as connection:
        connection.execute("UPDATE app_passwords SET last_used_at = ? WHERE id = ?", (utc_now(), matched["id"]))
        connection.commit()
    with _CACHE_LOCK:
        _VERIFIED[digest] = (now + _CACHE_TTL, matched["user_id"], matched["id"])
    return {"user_id": matched["user_id"], "password_id": matched["id"]}


def _dav_name(value: str) -> str:
    replacements = str.maketrans({
        "/": "／", "\\": "＼", ":": "：", "*": "＊", "?": "？",
        '"': "＂", "<": "＜", ">": "＞", "|": "｜",
    })
    return str(value or "").translate(replacements).strip(" .") or "未分类"


def _media_filename(row: Any, title: str | None = None, order: int | None = None) -> str:
    name = Path(row["original_name"] or row["title"] or "audio").name
    stem, suffix = Path(name).stem, Path(name).suffix
    display = _dav_name(title or stem)
    if suffix and display.lower().endswith(suffix.lower()):
        display = display[:-len(suffix)].rstrip()
    prefix = f"{order:02d} - " if order is not None else ""
    return f"{prefix}{display} [{row['id'][:8]}]{suffix}"


def _library_metadata() -> dict[str, dict[str, Any]]:
    path = config.data_root / "library.json"
    try:
        library = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    result: dict[str, dict[str, Any]] = {}
    for source in library.get("sources", []):
        source_name = str(source.get("filename") or "")
        section_orders: dict[str, int] = {}
        for article in source.get("articles", []):
            article_id = str(article.get("id") or "")
            if not article_id:
                continue
            section = str(article.get("section") or "未分类")
            section_orders[section] = section_orders.get(section, 0) + 1
            result[article_id] = {
                "source_name": source_name,
                "title": str(article.get("title") or ""),
                "section": section,
                "order": section_orders[section],
            }
    return result


def _issue_label(value: str) -> str:
    text = Path(str(value or "")).stem
    numeric = re.search(r"(20\d{2})[-_. ]?(0[1-9]|1[0-2])[-_. ]?([0-2]\d|3[01])", text)
    if numeric:
        return "-".join(numeric.groups())
    months = {
        name.lower(): index for index, name in enumerate((
            "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ), 1)
    }
    named = re.search(
        r"\b(" + "|".join(months) + r")[\s._-]+([0-3]?\d)(?:st|nd|rd|th)?[\s,._-]+(20\d{2})\b",
        text, re.I,
    )
    if named:
        month, day, year = named.groups()
        return f"{year}-{months[month.lower()]:02d}-{int(day):02d}"
    return "未标注期号"


def _publication_label(value: str) -> str:
    text = Path(str(value or "")).stem
    text = re.sub(r"20\d{2}[-_. ]?(?:0[1-9]|1[0-2])[-_. ]?(?:[0-2]\d|3[01])", " ", text)
    text = re.sub(
        r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)"
        r"[\s._-]+[0-3]?\d(?:st|nd|rd|th)?[\s,._-]+20\d{2}\b",
        " ", text, flags=re.I,
    )
    text = re.sub(r"\b(?:audio\s+edition|audio|edition|issue)\b", " ", text, flags=re.I)
    text = re.sub(r"[_\-.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    if "economist" in text.lower():
        return "The Economist"
    return _dav_name(text)


def _media_rows() -> list[Any]:
    with connect() as connection:
        return connection.execute(
            """SELECT media_items.id, media_items.title, media_items.original_name,
                      media_items.storage_path, media_items.mime_type, media_items.file_size,
                      media_items.updated_at, collections.name AS collection_name,
                      article_media_links.article_id
               FROM media_items
               LEFT JOIN collections ON collections.id = media_items.collection_id
               LEFT JOIN article_media_links ON article_media_links.media_id = media_items.id
               WHERE media_items.deleted_at IS NULL
               ORDER BY collections.name COLLATE NOCASE, media_items.title COLLATE NOCASE"""
        ).fetchall()


def _directory(name: str) -> dict[str, Any]:
    return {"name": name, "directory": True, "children": {}, "updated_at": None}


def _catalog_tree() -> dict[str, Any]:
    root = _directory("English Lab")
    metadata = _library_metadata()
    for row in _media_rows():
        article = metadata.get(str(row["article_id"] or ""))
        if article:
            source_name = article["source_name"] or row["collection_name"] or "未分类"
            parts = [
                _publication_label(source_name),
                _issue_label(source_name),
                _dav_name(article["section"]),
            ]
            filename = _media_filename(row, article["title"] or row["title"], article["order"])
        else:
            parts = ["未配套", _dav_name(row["collection_name"] or "未分类")]
            filename = _media_filename(row)
        node = root
        for part in parts:
            child = node["children"].setdefault(part, _directory(part))
            node = child
            if not node["updated_at"] or str(row["updated_at"] or "") > node["updated_at"]:
                node["updated_at"] = str(row["updated_at"] or "")
        node["children"][filename] = {
            "name": filename, "directory": False, "row": row,
            "updated_at": row["updated_at"],
        }
    return root


def _resolve_node(path_parts: list[str]) -> dict[str, Any] | None:
    node = _catalog_tree()
    for part in path_parts:
        node = node.get("children", {}).get(part)
        if not node:
            return None
    return node


def _http_date(value: str | None) -> str:
    try:
        parsed = datetime.fromisoformat(value or "").astimezone(timezone.utc)
    except ValueError:
        parsed = datetime.now(timezone.utc)
    return format_datetime(parsed, usegmt=True)


def _response_node(multistatus: ET.Element, href: str, name: str, *, directory: bool,
                   size: int | None = None, mime: str | None = None, updated_at: str | None = None) -> None:
    dav = "DAV:"
    response = ET.SubElement(multistatus, f"{{{dav}}}response")
    ET.SubElement(response, f"{{{dav}}}href").text = href
    propstat = ET.SubElement(response, f"{{{dav}}}propstat")
    prop = ET.SubElement(propstat, f"{{{dav}}}prop")
    ET.SubElement(prop, f"{{{dav}}}displayname").text = name
    resource_type = ET.SubElement(prop, f"{{{dav}}}resourcetype")
    if directory:
        ET.SubElement(resource_type, f"{{{dav}}}collection")
    else:
        ET.SubElement(prop, f"{{{dav}}}getcontentlength").text = str(size or 0)
        ET.SubElement(prop, f"{{{dav}}}getcontenttype").text = mime or "application/octet-stream"
        ET.SubElement(prop, f"{{{dav}}}getlastmodified").text = _http_date(updated_at)
    ET.SubElement(propstat, f"{{{dav}}}status").text = "HTTP/1.1 200 OK"


def _multistatus(path_parts: list[str], depth: str) -> Response:
    ET.register_namespace("D", "DAV:")
    root = ET.Element("{DAV:}multistatus")
    node = _resolve_node(path_parts)
    if not node:
        return Response(status_code=404)
    base = "/dav/" + "/".join(quote(part, safe="") for part in path_parts)
    if node["directory"]:
        href = base.rstrip("/") + "/"
        _response_node(root, href, node["name"], directory=True, updated_at=node.get("updated_at"))
        if depth != "0":
            for child in sorted(node["children"].values(), key=lambda item: item["name"].casefold()):
                child_href = href + quote(child["name"], safe="") + ("/" if child["directory"] else "")
                row = child.get("row")
                _response_node(
                    root, child_href, child["name"], directory=child["directory"],
                    size=row["file_size"] if row else None,
                    mime=row["mime_type"] if row else None,
                    updated_at=child.get("updated_at"),
                )
    else:
        row = node["row"]
        _response_node(root, base, node["name"], directory=False, size=row["file_size"],
                       mime=row["mime_type"], updated_at=row["updated_at"])
    return Response(ET.tostring(root, encoding="utf-8", xml_declaration=True), status_code=207,
                    media_type="application/xml; charset=utf-8")


async def dav_endpoint(request: Request) -> Response:
    if not _dav_enabled():
        return Response(status_code=404)
    if not _authenticate(request):
        return _unauthorized()
    if request.method not in READ_METHODS:
        return Response(status_code=405, headers={"Allow": ", ".join(sorted(READ_METHODS))})
    if request.method == "OPTIONS":
        return Response(status_code=200, headers={"DAV": "1", "MS-Author-Via": "DAV",
                                                  "Allow": ", ".join(sorted(READ_METHODS))})
    raw_path = request.path_params.get("path", "")
    parts = [unquote(part) for part in raw_path.strip("/").split("/") if part]
    if request.method == "PROPFIND":
        depth = request.headers.get("depth", "1")
        if depth not in {"0", "1"}:
            return Response(status_code=403)
        return _multistatus(parts, depth)
    node = _resolve_node(parts)
    if not node or node["directory"]:
        return Response(status_code=404)
    row = node["row"]
    target = (config.media_root / row["storage_path"]).resolve()
    try:
        target.relative_to(config.media_root)
    except ValueError:
        return Response(status_code=404)
    if not target.is_file():
        return Response(status_code=404)
    return FileResponse(target, media_type=row["mime_type"])


webdav_routes = [
    Route("/dav", dav_endpoint, methods=DAV_METHODS),
    Route("/dav/{path:path}", dav_endpoint, methods=DAV_METHODS),
]
