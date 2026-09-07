from __future__ import annotations

import io
import json
import re
import wave
from urllib.parse import quote

from english_lab.config import config
from english_lab.database import connect


def wav_bytes(seconds: float = 0.12) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(8000)
        output.writeframes(b"\x00\x00" * int(8000 * seconds))
    return buffer.getvalue()


def test_read_only_webdav_with_revocable_app_password(authenticated_client):
    client, csrf = authenticated_client
    csrf_headers = {"X-CSRF-Token": csrf}
    previous_secure = config.cookie_secure
    object.__setattr__(config, "cookie_secure", True)
    try:
        job_id = client.post(
            "/api/v1/media/imports", headers=csrf_headers, json={"total_files": 1, "source": "webdav-test"}
        ).json()["job_id"]
        uploaded = client.post(
            f"/api/v1/media/imports/{job_id}/file",
            headers=csrf_headers,
            files={"file": ("webdav-lesson.wav", wav_bytes(0.37), "audio/wav")},
            data={"relative_path": "WebDAV Course/webdav-lesson.wav"},
        )
        assert uploaded.status_code == 200, uploaded.text

        created = client.post(
            "/api/v1/app-passwords", headers=csrf_headers, json={"label": "pytest phone"}
        )
        assert created.status_code == 200, created.text
        credentials = created.json()
        auth = (credentials["username"], credentials["password"])

        with connect() as connection:
            row = connection.execute(
                "SELECT password_hash FROM app_passwords WHERE id = ?", (credentials["item"]["id"],)
            ).fetchone()
        assert row is not None
        assert credentials["password"] not in row["password_hash"]

        listed = client.get("/api/v1/app-passwords")
        assert listed.json()["username"] == credentials["username"]

        root = client.request("PROPFIND", "/dav/", auth=auth, headers={"Depth": "1"})
        assert root.status_code == 207, root.text
        assert "未配套" in root.text

        unpaired = client.request("PROPFIND", f"/dav/{quote('未配套')}/", auth=auth, headers={"Depth": "1"})
        assert unpaired.status_code == 207, unpaired.text
        assert "WebDAV Course" in unpaired.text

        collection = client.request(
            "PROPFIND", f"/dav/{quote('未配套')}/WebDAV%20Course/", auth=auth, headers={"Depth": "1"},
        )
        assert collection.status_code == 207, collection.text
        assert "webdav-lesson" in collection.text

        filename = re.search(r"webdav-lesson \[[0-9a-f]{8}\]\.wav", collection.text).group(0)
        streamed = client.get(
            f"/dav/{quote('未配套')}/WebDAV%20Course/{quote(filename)}",
            auth=auth,
            headers={"Range": "bytes=0-15"},
        )
        assert streamed.status_code == 206
        assert len(streamed.content) == 16
        assert client.put(f"/dav/{quote('未配套')}/WebDAV%20Course/blocked.wav", auth=auth, content=b"no").status_code == 405

        qr = client.get("/api/v1/app-passwords/qr")
        assert qr.status_code == 200
        assert qr.headers["content-type"].startswith("image/svg+xml")

        revoked = client.delete(f"/api/v1/app-passwords/{credentials['item']['id']}", headers=csrf_headers)
        assert revoked.status_code == 200
        assert client.request("PROPFIND", "/dav/", auth=auth).status_code == 401
    finally:
        object.__setattr__(config, "cookie_secure", previous_secure)


def test_webdav_uses_article_issue_and_section_hierarchy(authenticated_client):
    client, csrf = authenticated_client
    headers = {"X-CSRF-Token": csrf}
    previous_secure = config.cookie_secure
    object.__setattr__(config, "cookie_secure", True)
    try:
        job_id = client.post(
            "/api/v1/media/imports", headers=headers, json={"total_files": 1, "source": "paired-webdav"},
        ).json()["job_id"]
        uploaded = client.post(
            f"/api/v1/media/imports/{job_id}/file", headers=headers,
            files={"file": ("leaders-intro.wav", wav_bytes(0.43), "audio/wav")},
            data={"relative_path": "The Economist September 5 2026/leaders-intro.wav"},
        )
        media_id = uploaded.json()["media_id"]
        source_id, article_id = "economist-2026-09-05", "leaders-article"
        (config.data_root / "library.json").write_text(json.dumps({"sources": [{
            "id": source_id, "filename": "The Economist September 5 2026.epub",
            "articles": [{"id": article_id, "title": "The world this week", "section": "Leaders"}],
        }]}), encoding="utf-8")
        with connect() as connection:
            connection.execute(
                """INSERT INTO article_media_links(article_id, article_source_id, media_id, match_method,
                          confidence, confirmed, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,datetime('now'),datetime('now'))""",
                (article_id, source_id, media_id, "manual", 1, 1),
            )
            connection.commit()

        credentials = client.post(
            "/api/v1/app-passwords", headers=headers, json={"label": "paired phone"},
        ).json()
        auth = (credentials["username"], credentials["password"])
        paths = [
            ("/dav/", "The Economist"),
            (f"/dav/{quote('The Economist')}/", "2026-09-05"),
            (f"/dav/{quote('The Economist')}/2026-09-05/", "Leaders"),
            (f"/dav/{quote('The Economist')}/2026-09-05/Leaders/", "01 - The world this week"),
        ]
        for path, expected in paths:
            response = client.request("PROPFIND", path, auth=auth, headers={"Depth": "1"})
            assert response.status_code == 207, response.text
            assert expected in response.text
        filename = f"01 - The world this week [{media_id[:8]}].wav"
        streamed = client.get(
            f"/dav/{quote('The Economist')}/2026-09-05/Leaders/{quote(filename)}",
            auth=auth, headers={"Range": "bytes=0-11"},
        )
        assert streamed.status_code == 206
        assert len(streamed.content) == 12
    finally:
        object.__setattr__(config, "cookie_secure", previous_secure)


def test_webdav_is_hidden_without_https(authenticated_client):
    client, csrf = authenticated_client
    assert client.post(
        "/api/v1/app-passwords",
        headers={"X-CSRF-Token": csrf},
        json={"label": "unsafe phone"},
    ).status_code == 409
    assert client.request("PROPFIND", "/dav/").status_code == 404
