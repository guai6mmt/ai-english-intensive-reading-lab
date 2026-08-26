from __future__ import annotations

import io
import wave

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

        root = client.request("PROPFIND", "/dav/", auth=auth, headers={"Depth": "1"})
        assert root.status_code == 207, root.text
        assert "WebDAV Course" in root.text

        collection = client.request("PROPFIND", "/dav/WebDAV%20Course/", auth=auth, headers={"Depth": "1"})
        assert collection.status_code == 207, collection.text
        assert "webdav-lesson" in collection.text

        import re

        filename = re.search(r"webdav-lesson \[[0-9a-f]{8}\]\.wav", collection.text).group(0)
        streamed = client.get(
            f"/dav/WebDAV%20Course/{filename.replace(' ', '%20').replace('[', '%5B').replace(']', '%5D')}",
            auth=auth,
            headers={"Range": "bytes=0-15"},
        )
        assert streamed.status_code == 206
        assert len(streamed.content) == 16
        assert client.put("/dav/WebDAV%20Course/blocked.wav", auth=auth, content=b"no").status_code == 405

        qr = client.get("/api/v1/app-passwords/qr")
        assert qr.status_code == 200
        assert qr.headers["content-type"].startswith("image/svg+xml")

        revoked = client.delete(f"/api/v1/app-passwords/{credentials['item']['id']}", headers=csrf_headers)
        assert revoked.status_code == 200
        assert client.request("PROPFIND", "/dav/", auth=auth).status_code == 401
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
