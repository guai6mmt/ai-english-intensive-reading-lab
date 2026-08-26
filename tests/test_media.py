from __future__ import annotations

import io
import wave


def wav_bytes(seconds: float = 0.12) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * int(8000 * seconds))
    return buffer.getvalue()


def test_media_import_stream_progress_and_recycle(authenticated_client):
    client, csrf = authenticated_client
    headers = {"X-CSRF-Token": csrf}
    created = client.post(
        "/api/v1/media/imports",
        headers=headers,
        json={"total_files": 1, "source": "pytest"},
    )
    assert created.status_code == 200, created.text
    job_id = created.json()["job_id"]

    uploaded = client.post(
        f"/api/v1/media/imports/{job_id}/file",
        headers=headers,
        files={"file": ("lesson.wav", wav_bytes(), "audio/wav")},
        data={"relative_path": "Course A/lesson.wav"},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["status"] == "imported"
    media_id = uploaded.json()["media_id"]

    complete = client.post(f"/api/v1/media/imports/{job_id}/complete", headers=headers)
    assert complete.status_code == 200
    assert complete.json()["job"]["imported_files"] == 1

    listing = client.get("/api/v1/media/items")
    assert listing.status_code == 200
    assert listing.json()["total"] == 1
    assert listing.json()["items"][0]["collection_name"] == "Course A"
    assert listing.json()["items"][0]["original_name"] == "lesson.wav"

    streamed = client.get(
        f"/api/v1/media/items/{media_id}/stream",
        headers={"Range": "bytes=0-15"},
    )
    assert streamed.status_code == 206
    assert streamed.headers["content-range"].startswith("bytes 0-15/")
    assert len(streamed.content) == 16

    progress = client.put(
        f"/api/v1/media/items/{media_id}/progress",
        headers=headers,
        json={"position_ms": 500, "playback_rate": 1.25, "completed": False},
    )
    assert progress.status_code == 200
    favorite = client.post(f"/api/v1/media/items/{media_id}/favorite", headers=headers)
    assert favorite.json()["favorite"] is True

    deleted = client.delete(f"/api/v1/media/items/{media_id}", headers=headers)
    assert deleted.status_code == 200
    assert client.get("/api/v1/media/items").json()["total"] == 0
    assert client.get("/api/v1/media/items?deleted=true").json()["total"] == 1
    restored = client.post(f"/api/v1/media/items/{media_id}/restore", headers=headers)
    assert restored.status_code == 200


def test_duplicate_upload_is_not_copied_twice(authenticated_client):
    client, csrf = authenticated_client
    headers = {"X-CSRF-Token": csrf}
    created = client.post("/api/v1/media/imports", headers=headers, json={"total_files": 1}).json()
    response = client.post(
        f"/api/v1/media/imports/{created['job_id']}/file",
        headers=headers,
        files={"file": ("copy.wav", wav_bytes(), "audio/wav")},
        data={"relative_path": "Another/copy.wav"},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "duplicate"


def test_chunked_upload(authenticated_client):
    client, csrf = authenticated_client
    headers = {"X-CSRF-Token": csrf}
    content = wav_bytes(0.23)
    job_id = client.post("/api/v1/media/imports", headers=headers, json={"total_files": 1}).json()["job_id"]
    initialized = client.post(
        f"/api/v1/media/imports/{job_id}/uploads",
        headers=headers,
        json={"relative_path": "Chunked/large-lesson.wav", "file_size": len(content)},
    )
    assert initialized.status_code == 200, initialized.text
    upload_id = initialized.json()["upload_id"]
    uploaded = client.put(
        f"/api/v1/media/imports/{job_id}/uploads/{upload_id}",
        headers={**headers, "Content-Range": f"bytes 0-{len(content) - 1}/{len(content)}", "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert uploaded.status_code == 200, uploaded.text
    completed = client.post(
        f"/api/v1/media/imports/{job_id}/uploads/{upload_id}/complete",
        headers=headers,
    )
    assert completed.status_code == 200, completed.text
    assert completed.json()["status"] == "imported"
