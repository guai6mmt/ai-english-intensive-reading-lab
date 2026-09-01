from __future__ import annotations


def test_contextual_vocabulary_review_and_exports(authenticated_client) -> None:
    client, csrf = authenticated_client
    headers = {"X-CSRF-Token": csrf}

    created = client.post(
        "/api/vocabulary",
        headers=headers,
        json={
            "term": "resilience",
            "article_id": "article-1",
            "sentence_id": "p0-s0",
            "context": "The economy showed remarkable resilience after the shock.",
            "source": "Sample issue",
        },
    )
    assert created.status_code == 200, created.text
    item = created.json()["item"]
    assert item["translation"]
    assert item["encounter_count"] >= 1
    assert item["contexts"][0].startswith("The economy")

    duplicate = client.post(
        "/api/vocabulary",
        headers=headers,
        json={"term": "resilience", "context": "Resilience matters in politics."},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["item"]["encounter_count"] == item["encounter_count"] + 1

    lookup = client.post(
        "/api/dictionary/lookup",
        headers=headers,
        json={"term": "resilience", "context": "Its resilience surprised investors."},
    )
    assert lookup.status_code == 200
    assert lookup.json()["saved"] is True

    queue = client.get("/api/vocabulary/review/queue")
    assert queue.status_code == 200
    assert any(row["id"] == item["id"] for row in queue.json()["items"])
    reviewed = client.post(
        f"/api/vocabulary/review/{item['id']}", headers=headers, json={"rating": "good", "mode": "adaptive"}
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["item"]["reps"] == 1
    assert reviewed.json()["schedule"]["scheduled_days"] >= 1

    for format_name in ("csv", "anki", "json"):
        exported = client.get(f"/api/vocabulary/export/{format_name}")
        assert exported.status_code == 200
        assert "attachment" in exported.headers["content-disposition"]

