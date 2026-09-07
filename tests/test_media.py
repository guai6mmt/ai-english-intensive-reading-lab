from __future__ import annotations

import io
import wave


def test_dedicated_asr_key_overrides_qwen_text_gateway(monkeypatch):
    import app as application
    monkeypatch.setattr(application, 'load_settings', lambda: {
        'qwen_api_key': 'text-gateway-key',
        'qwen_base_url': 'https://text-gateway.example/v1',
        'dashscope_api_key': 'dedicated-asr-key',
    })
    assert application.qwen_asr_config()['api_key'] == 'dedicated-asr-key'


def test_original_audio_alignment_fills_unmatched_sentence_ranges():
    import app as application
    sentences = [
        {'index': 0, 'para': 0, 'text': 'The first complete sentence is here.'},
        {'index': 1, 'para': 0, 'text': 'The middle sentence needs an estimated range.'},
        {'index': 2, 'para': 0, 'text': 'The final complete sentence is here.'},
    ]
    raw = [
        {**sentences[0], 'begin_ms': 0, 'end_ms': 1000},
        {**sentences[2], 'begin_ms': 3000, 'end_ms': 4000},
    ]
    result = application._fill_original_alignment_gaps(sentences, raw, 4000)
    assert len(result) == 3
    assert result[0]['estimated'] is False
    assert result[1]['estimated'] is True
    assert result[1]['begin_ms'] == 1000
    assert result[1]['end_ms'] == 3000
    assert result[2]['estimated'] is False


def _asr_sentence(text: str, start_ms: int, step_ms: int = 400) -> dict:
    words = []
    clock = start_ms
    for token in text.split():
        words.append({"text": token, "begin_time": clock, "end_time": clock + step_ms - 50})
        clock += step_ms
    return {"text": text, "begin_time": start_ms, "end_time": clock, "words": words}


def test_original_alignment_skips_audio_only_passages():
    """Intro/rubric/outro spoken but absent from the article must not be glued
    onto a sentence or shift the timeline that follows."""
    import app as application

    items = [
        {"index": 0, "para": 0, "text": "The council approved the new bridge budget."},
        {"index": 1, "para": 1, "text": "Residents welcomed the decision after years of delay."},
    ]
    asr_sentences = [
        _asr_sentence("welcome to the audio edition", 0),          # intro, not in article
        _asr_sentence("the council approved the new bridge budget", 5000),
        _asr_sentence("section two politics", 9000),               # spoken rubric, not in article
        _asr_sentence("residents welcomed the decision after years of delay", 12000),
        _asr_sentence("that is all for today", 16000),             # outro, not in article
    ]

    result = application.align_asr_words_to_original(items, asr_sentences)

    assert len(result) == 2
    # Sentence one begins where it is actually narrated (5000ms), not at the
    # start of the intro (0ms).
    assert result[0]["begin_ms"] >= 3000
    assert "welcome" not in result[0]["asr_text"]
    assert "council" in result[0]["asr_text"]
    # The rubric between paragraphs is skipped, so sentence two still lands on
    # its own narration instead of swallowing "section two politics".
    assert result[1]["begin_ms"] >= 11000
    assert "section" not in result[1]["asr_text"]
    assert "politics" not in result[1]["asr_text"]
    assert "today" not in result[1]["asr_text"]
    assert result[1]["begin_ms"] > result[0]["begin_ms"]
    assert result[0]["confidence"] == 1.0
    assert result[1]["confidence"] == 1.0


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


def test_import_package_can_be_recycled_and_restored(authenticated_client):
    client, csrf = authenticated_client
    headers = {"X-CSRF-Token": csrf}
    created = client.post(
        "/api/v1/media/imports",
        headers=headers,
        json={"total_files": 2, "source": "pytest-package"},
    ).json()
    job_id = created["job_id"]
    media_ids = []
    for index, seconds in enumerate((0.31, 0.41), 1):
        response = client.post(
            f"/api/v1/media/imports/{job_id}/file",
            headers=headers,
            files={"file": (f"part-{index}.wav", wav_bytes(seconds), "audio/wav")},
            data={"relative_path": f"Pytest Package/part-{index}.wav"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "imported"
        media_ids.append(response.json()["media_id"])
    client.post(f"/api/v1/media/imports/{job_id}/complete", headers=headers)

    packages = client.get("/api/v1/media/imports/packages")
    package = next(item for item in packages.json()["items"] if item["id"] == job_id)
    assert package["imported_count"] == 2
    assert package["active_count"] == 2

    recycled = client.delete(f"/api/v1/media/imports/{job_id}", headers=headers)
    assert recycled.status_code == 200, recycled.text
    assert recycled.json()["moved"] == 2
    deleted_ids = {item["id"] for item in client.get("/api/v1/media/items?deleted=true").json()["items"]}
    assert set(media_ids) <= deleted_ids

    restored = client.post(f"/api/v1/media/imports/{job_id}/restore", headers=headers)
    assert restored.status_code == 200, restored.text
    assert restored.json()["restored"] == 2
    active_ids = {item["id"] for item in client.get("/api/v1/media/items").json()["items"]}
    assert set(media_ids) <= active_ids

    duplicate_job = client.post(
        "/api/v1/media/imports",
        headers=headers,
        json={"total_files": 1, "source": "pytest-duplicates-only"},
    ).json()["job_id"]
    duplicate = client.post(
        f"/api/v1/media/imports/{duplicate_job}/file",
        headers=headers,
        files={"file": ("same.wav", wav_bytes(0.31), "audio/wav")},
        data={"relative_path": "Another Package/same.wav"},
    )
    assert duplicate.json()["status"] == "duplicate"
    assert client.delete(f"/api/v1/media/imports/{duplicate_job}", headers=headers).json()["moved"] == 0
    active_ids = {item["id"] for item in client.get("/api/v1/media/items").json()["items"]}
    assert set(media_ids) <= active_ids

    assert client.delete(f"/api/v1/media/imports/{job_id}", headers=headers).json()["moved"] == 2
    reimport_job = client.post(
        "/api/v1/media/imports",
        headers=headers,
        json={"total_files": 2, "source": "pytest-package-reimported"},
    ).json()["job_id"]
    for index, seconds in enumerate((0.31, 0.41), 1):
        response = client.post(
            f"/api/v1/media/imports/{reimport_job}/file",
            headers=headers,
            files={"file": (f"part-{index}.wav", wav_bytes(seconds), "audio/wav")},
            data={"relative_path": f"Pytest Package Reimported/part-{index}.wav"},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "restored"
    client.post(f"/api/v1/media/imports/{reimport_job}/complete", headers=headers)

    packages = client.get("/api/v1/media/imports/packages").json()["items"]
    current = next(item for item in packages if item["id"] == reimport_job)
    previous = next(item for item in packages if item["id"] == job_id)
    assert (current["imported_count"], current["active_count"]) == (2, 2)
    assert (previous["imported_count"], previous["active_count"]) == (0, 0)
    items = client.get("/api/v1/media/items").json()["items"]
    reimported = [item for item in items if item["id"] in media_ids]
    assert len(reimported) == 2
    assert {item["collection_name"] for item in reimported} == {"Pytest Package Reimported"}
