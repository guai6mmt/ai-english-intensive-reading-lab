from __future__ import annotations

import io
import uuid
import wave
import zipfile


def _wav_bytes(seconds: float = 0.18) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * int(8000 * seconds))
    return output.getvalue()


def _epub_bytes(title: str) -> bytes:
    paragraph = " ".join([
        "This carefully reported article explains why resilient institutions matter during periods of economic and political uncertainty."
        for _ in range(14)
    ])
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as book:
        book.writestr("mimetype", "application/epub+zip")
        book.writestr("feed_0/index.xhtml", "<html><body><h1>Leaders</h1></body></html>")
        book.writestr("feed_0/article_1.xhtml", f"<html><body><h1>{title}</h1><p>{paragraph}</p></body></html>")
    return output.getvalue()


def _audio_zip_bytes() -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("005 Leaders - Resilient institutions.wav", _wav_bytes())
    return output.getvalue()


def test_unified_issue_import_builds_pairing_preview(authenticated_client) -> None:
    client, csrf = authenticated_client
    suffix = uuid.uuid4().hex[:8]
    response = client.post(
        "/api/issues/import",
        headers={"X-CSRF-Token": csrf},
        files={
            "epub": (f"TE-2026-04-25-{suffix}.epub", _epub_bytes("Resilient institutions"), "application/epub+zip"),
            "audio_zip": (f"audio-{suffix}.zip", _audio_zip_bytes(), "application/zip"),
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["source"]["article_count"] == 1
    assert payload["audio"]["total_files"] == 1
    assert payload["pairing"]["summary"]["matched"] == 1
    candidate = payload["pairing"]["candidates"][0]
    assert candidate["media_id"]
    assert candidate["section"] == "Leaders"
    # Keep the module-scoped test database neutral for legacy media tests.
    deleted = client.delete(f"/api/v1/media/items/{candidate['media_id']}", headers={"X-CSRF-Token": csrf})
    assert deleted.status_code == 200
    from english_lab.database import transaction
    with transaction() as connection:
        connection.execute("DELETE FROM media_items WHERE id = ?", (candidate["media_id"],))


def test_issue_import_rejects_zip_traversal(authenticated_client) -> None:
    client, csrf = authenticated_client
    suffix = uuid.uuid4().hex[:8]
    bad_zip = io.BytesIO()
    with zipfile.ZipFile(bad_zip, "w") as archive:
        archive.writestr("../escape.mp3", b"not audio")
    response = client.post(
        "/api/issues/import",
        headers={"X-CSRF-Token": csrf},
        files={
            "epub": (f"TE-2026-05-02-{suffix}.epub", _epub_bytes("A safe archive"), "application/epub+zip"),
            "audio_zip": (f"unsafe-{suffix}.zip", bad_zip.getvalue(), "application/zip"),
        },
    )
    assert response.status_code == 400
    assert "不安全" in response.json()["detail"]
