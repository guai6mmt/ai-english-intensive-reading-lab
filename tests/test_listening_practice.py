from __future__ import annotations

import uuid

from english_lab.listening_practice import score_dictation


def test_score_dictation_marks_missing_wrong_and_extra() -> None:
    result = score_dictation(
        "The market has changed very quickly.",
        "The markets changed quickly today.",
    )
    assert 0 < result["score"] < 100
    assert result["counts"]["wrong"] >= 1
    assert result["counts"]["missing"] >= 1
    assert result["counts"]["extra"] >= 1
    assert result["focus_phrases"]


def test_listening_attempt_is_saved_and_enters_review_queue(authenticated_client) -> None:
    client, csrf = authenticated_client
    article_id = f"practice-{uuid.uuid4().hex}"
    sentence = "Investors are watching the labour market very closely."
    response = client.post(
        "/api/v1/listening/attempts",
        headers={"X-CSRF-Token": csrf},
        json={
            "article_id": article_id,
            "sentence_index": 2,
            "sentence_text": sentence,
            "stage": "dictation",
            "answer": "Investors watch market.",
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result"]["score"] < 85
    assert payload["progress"]["attempts"] == 1
    assert payload["progress"]["due"] is True

    progress = client.get(f"/api/v1/listening/articles/{article_id}/practice")
    assert progress.status_code == 200
    assert progress.json()["summary"]["weak"] == 1
    assert progress.json()["items"][0]["last_result"]["segments"]

    review = client.get("/api/v1/listening/review?limit=100")
    assert review.status_code == 200
    assert any(item["article_id"] == article_id for item in review.json()["items"])

    shadow = client.post(
        "/api/v1/listening/attempts",
        headers={"X-CSRF-Token": csrf},
        json={
            "article_id": article_id,
            "sentence_index": 2,
            "sentence_text": sentence,
            "stage": "shadowing",
            "rating": 5,
        },
    )
    assert shadow.status_code == 200
    after_shadow = client.get(f"/api/v1/listening/articles/{article_id}/practice").json()["items"][0]
    assert after_shadow["review_score"] < 85
    assert after_shadow["last_result"]["segments"]
    assert after_shadow["due"] is True


def test_shadowing_rating_updates_spaced_repetition(authenticated_client) -> None:
    client, csrf = authenticated_client
    article_id = f"shadow-{uuid.uuid4().hex}"
    response = client.post(
        "/api/v1/listening/attempts",
        headers={"X-CSRF-Token": csrf},
        json={
            "article_id": article_id,
            "sentence_index": 0,
            "sentence_text": "A clear rhythm makes shadowing easier.",
            "stage": "shadowing",
            "rating": 5,
        },
    )
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["result"]["score"] == 100
    assert payload["progress"]["interval_days"] >= 2
    assert payload["progress"]["due"] is False
