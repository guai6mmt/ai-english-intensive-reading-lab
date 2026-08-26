from __future__ import annotations

import json
import uuid

from english_lab.config import config
from english_lab.database import transaction, utc_now


def _seed_pairing_content() -> tuple[str, str, list[str], list[str]]:
    token = uuid.uuid4().hex[:8]
    source_id = f"source-{token}"
    collection_id = f"collection-{token}"
    article_ids = [f"article-{token}-{index}" for index in range(3)]
    media_ids = [f"media-{token}-{index}" for index in range(3)]
    library_path = config.data_root / "library.json"
    try:
        library = json.loads(library_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        library = {"sources": []}
    library.setdefault("sources", []).append({
        "id": source_id,
        "filename": "TE-2026-08-22-EPUB.epub",
        "uploaded_at": utc_now(),
        "articles": [
            {"id": article_ids[0], "source_id": source_id, "section": "Europe", "title": "First Europe story", "paragraphs": ["This is the first European story used for testing."]},
            {"id": article_ids[1], "source_id": source_id, "section": "Europe", "title": "Second Europe story", "paragraphs": ["This is the second European story used for testing."]},
            {"id": article_ids[2], "source_id": source_id, "section": "Unlabelled", "title": "Needs a manual match", "paragraphs": ["This article needs a manually selected original audio track."]},
        ],
    })
    library_path.write_text(json.dumps(library), encoding="utf-8")

    now = utc_now()
    with transaction() as connection:
        connection.execute(
            "INSERT INTO collections(id, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (collection_id, "The-Economist-Audio-Edition-August-22-2026", now, now),
        )
        for index, (media_id, title) in enumerate(zip(media_ids, [
            "041 Europe - First Europe story",
            "042 Europe - Second Europe story",
            "043 Misc - Manual candidate",
        ])):
            connection.execute(
                """INSERT INTO media_items(
                       id, collection_id, title, original_name, storage_path, extension, mime_type,
                       file_size, sha256, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, '.mp3', 'audio/mpeg', 100, ?, ?, ?)""",
                (media_id, collection_id, title, f"{title}.mp3", f"originals/test/{media_id}.mp3", f"sha-{token}-{index}", now, now),
            )
            stored = config.media_root / f"originals/test/{media_id}.mp3"
            stored.parent.mkdir(parents=True, exist_ok=True)
            stored.write_bytes(b"ID3")
    return source_id, collection_id, article_ids, media_ids


def _cleanup_pairing_content(source_id: str, collection_id: str, media_ids: list[str]) -> None:
    with transaction() as connection:
        connection.executemany("DELETE FROM media_items WHERE id = ?", [(item,) for item in media_ids])
        connection.execute("DELETE FROM collections WHERE id = ?", (collection_id,))
    library_path = config.data_root / "library.json"
    library = json.loads(library_path.read_text(encoding="utf-8"))
    library["sources"] = [item for item in library.get("sources", []) if item.get("id") != source_id]
    library_path.write_text(json.dumps(library), encoding="utf-8")


def test_preview_and_manual_pairing(authenticated_client):
    client, csrf = authenticated_client
    headers = {"X-CSRF-Token": csrf}
    source_id, collection_id, article_ids, media_ids = _seed_pairing_content()

    preview = client.post(
        "/api/v1/content-links/preview",
        headers=headers,
        json={"source_id": source_id, "collection_id": collection_id},
    )
    assert preview.status_code == 200, preview.text
    payload = preview.json()
    assert payload["summary"]["articles"] == 3
    assert payload["summary"]["unmatched"] == 1
    suggestions = {item["article_id"]: item for item in payload["candidates"]}
    assert suggestions[article_ids[0]]["media_id"] == media_ids[0]
    assert suggestions[article_ids[1]]["media_id"] == media_ids[1]
    assert suggestions[article_ids[2]]["media_id"] is None

    links = [
        {"article_id": article_ids[0], "media_id": media_ids[0], "match_method": "automatic", "confidence": 0.95},
        {"article_id": article_ids[1], "media_id": media_ids[1], "match_method": "automatic", "confidence": 0.95},
        {"article_id": article_ids[2], "media_id": media_ids[2], "match_method": "manual", "confidence": 1},
    ]
    confirmed = client.post(
        "/api/v1/content-links/confirm",
        headers=headers,
        json={"source_id": source_id, "collection_id": collection_id, "links": links},
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["summary"]["confirmed"] == 3

    article = client.get(f"/api/articles/{article_ids[2]}")
    assert article.status_code == 200, article.text
    assert article.json()["article"]["linked_media"]["id"] == media_ids[2]
    assert article.json()["article"]["linked_media"]["match_method"] == "manual"

    variants = client.get(f"/api/articles/{article_ids[2]}/listening/audio-variants")
    assert variants.status_code == 200, variants.text
    original = next(item for item in variants.json()["variants"] if item["provider"] == "original")
    assert original["media_id"] == media_ids[2]
    assert original["audio_url"].endswith(f"/{media_ids[2]}/stream")

    media = client.get("/api/v1/media/items?sort=track&limit=200").json()["items"]
    linked = next(item for item in media if item["id"] == media_ids[2])
    assert linked["linked_article_id"] == article_ids[2]
    _cleanup_pairing_content(source_id, collection_id, media_ids)
