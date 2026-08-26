from __future__ import annotations

import difflib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from .auth import current_user
from .config import config
from .database import connect, transaction, utc_now


router = APIRouter(prefix="/api/v1/listening", tags=["listening-practice"])
TOKEN_RE = re.compile(r"[A-Za-z]+(?:[’'][A-Za-z]+)?|\d+(?:\.\d+)?")
LIBRARY_PATH = config.data_root / "library.json"


class PracticeAttempt(BaseModel):
    article_id: str = Field(min_length=1, max_length=200)
    sentence_index: int = Field(ge=0, le=100_000)
    sentence_text: str = Field(min_length=1, max_length=10_000)
    stage: Literal["dictation", "correction", "shadowing", "review"] = "dictation"
    answer: str = Field(default="", max_length=10_000)
    rating: int | None = Field(default=None, ge=1, le=5)


def _tokens(text: str) -> list[str]:
    return TOKEN_RE.findall(str(text or ""))


def _norm(token: str) -> str:
    return token.lower().replace("’", "'")


def score_dictation(expected: str, actual: str) -> dict[str, Any]:
    expected_tokens = _tokens(expected)
    actual_tokens = _tokens(actual)
    expected_norm = [_norm(token) for token in expected_tokens]
    actual_norm = [_norm(token) for token in actual_tokens]
    matcher = difflib.SequenceMatcher(None, expected_norm, actual_norm, autojunk=False)
    segments: list[dict[str, Any]] = []
    missing = extra = wrong = 0
    focus_indexes: set[int] = set()

    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            for offset, token in enumerate(expected_tokens[i1:i2]):
                segments.append({"kind": "correct", "expected": token, "actual": actual_tokens[j1 + offset]})
            continue
        if tag == "delete":
            for index in range(i1, i2):
                missing += 1
                focus_indexes.add(index)
                segments.append({"kind": "missing", "expected": expected_tokens[index], "actual": ""})
            continue
        if tag == "insert":
            for token in actual_tokens[j1:j2]:
                extra += 1
                segments.append({"kind": "extra", "expected": "", "actual": token})
            continue

        expected_span = expected_tokens[i1:i2]
        actual_span = actual_tokens[j1:j2]
        paired = min(len(expected_span), len(actual_span))
        for offset in range(paired):
            wrong += 1
            focus_indexes.add(i1 + offset)
            segments.append({
                "kind": "wrong",
                "expected": expected_span[offset],
                "actual": actual_span[offset],
            })
        for offset in range(paired, len(expected_span)):
            missing += 1
            focus_indexes.add(i1 + offset)
            segments.append({"kind": "missing", "expected": expected_span[offset], "actual": ""})
        for offset in range(paired, len(actual_span)):
            extra += 1
            segments.append({"kind": "extra", "expected": "", "actual": actual_span[offset]})

    error_units = missing + extra + wrong
    denominator = max(1, len(expected_tokens), len(actual_tokens))
    score = round(max(0.0, 100.0 * (1.0 - error_units / denominator)), 1)
    focus_phrases: list[str] = []
    for index in sorted(focus_indexes):
        start = max(0, index - 1)
        end = min(len(expected_tokens), index + 2)
        phrase = " ".join(expected_tokens[start:end])
        if phrase and phrase not in focus_phrases:
            focus_phrases.append(phrase)

    return {
        "score": score,
        "counts": {"correct": max(0, len(expected_tokens) - missing - wrong), "missing": missing, "extra": extra, "wrong": wrong},
        "segments": segments,
        "focus_phrases": focus_phrases[:6],
        "word_count": len(expected_tokens),
    }


def _schedule(score: float, previous_interval: int, previous_ease: float) -> tuple[int, float, datetime]:
    now = datetime.now(timezone.utc)
    ease = float(previous_ease or 2.3)
    if score >= 95:
        interval = 2 if previous_interval < 1 else min(60, max(2, round(previous_interval * ease)))
        ease = min(2.8, ease + 0.08)
        due = now + timedelta(days=interval)
    elif score >= 85:
        interval = max(1, previous_interval or 1)
        due = now + timedelta(days=interval)
    elif score >= 70:
        interval = 0
        ease = max(1.5, ease - 0.12)
        due = now + timedelta(hours=12)
    else:
        interval = 0
        ease = max(1.3, ease - 0.2)
        due = now
    return interval, round(ease, 2), due


def _decode_result(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def _row_payload(row: Any) -> dict[str, Any]:
    payload = dict(row)
    payload["last_result"] = _decode_result(payload.pop("last_result_json", "{}"))
    payload["due"] = str(payload.get("due_at") or "") <= utc_now()
    component_scores = []
    if payload.get("last_dictation_score") is not None:
        component_scores.append(float(payload["last_dictation_score"]))
    if payload.get("shadowing_rating") is not None:
        component_scores.append(float(payload["shadowing_rating"]) * 20)
    payload["review_score"] = min(component_scores) if component_scores else float(payload.get("last_score") or 0)
    return payload


def _article_titles() -> dict[str, str]:
    try:
        data = json.loads(Path(LIBRARY_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    titles: dict[str, str] = {}
    for source in data.get("sources", []):
        for article in source.get("articles", []):
            if article.get("id"):
                titles[str(article["id"])] = str(article.get("title") or "Untitled article")
    return titles


@router.get("/articles/{article_id}/practice")
def article_practice(article_id: str, request: Request) -> dict[str, Any]:
    user = current_user(request)
    with connect() as connection:
        rows = connection.execute(
            """SELECT * FROM listening_sentence_progress
               WHERE user_id = ? AND article_id = ? ORDER BY sentence_index""",
            (user["id"], article_id),
        ).fetchall()
    items = [_row_payload(row) for row in rows]
    return {
        "article_id": article_id,
        "items": items,
        "summary": {
            "practiced": len(items),
            "weak": sum(1 for item in items if float(item["review_score"]) < 85),
            "due": sum(1 for item in items if item["due"]),
            "average": round(sum(float(item["review_score"]) for item in items) / len(items), 1) if items else 0,
        },
    }


@router.get("/review")
def review_queue(request: Request, limit: int = Query(default=30, ge=1, le=100)) -> dict[str, Any]:
    user = current_user(request)
    now = utc_now()
    with connect() as connection:
        rows = connection.execute(
            """SELECT * FROM listening_sentence_progress
               WHERE user_id = ? AND (
                   due_at <= ? OR
                   MIN(
                       COALESCE(last_dictation_score, last_score),
                       COALESCE(shadowing_rating * 20, last_score)
                   ) < 85
               )
               ORDER BY CASE WHEN due_at <= ? THEN 0 ELSE 1 END,
                        MIN(
                            COALESCE(last_dictation_score, last_score),
                            COALESCE(shadowing_rating * 20, last_score)
                        ) ASC,
                        due_at ASC
               LIMIT ?""",
            (user["id"], now, now, limit),
        ).fetchall()
    titles = _article_titles()
    items = [_row_payload(row) for row in rows]
    for item in items:
        item["article_title"] = titles.get(item["article_id"], "文章")
    return {"items": items, "total": len(items), "as_of": now}


@router.post("/attempts")
def save_attempt(spec: PracticeAttempt, request: Request) -> dict[str, Any]:
    user = current_user(request)
    if spec.stage == "shadowing":
        if spec.rating is None:
            raise HTTPException(400, "跟读阶段需要选择 1–5 星自评。")
        result: dict[str, Any] = {
            "score": float(spec.rating * 20),
            "rating": spec.rating,
            "counts": {"correct": 0, "missing": 0, "extra": 0, "wrong": 0},
            "segments": [],
            "focus_phrases": [],
        }
    else:
        if not spec.answer.strip():
            raise HTTPException(400, "请先输入听写内容。")
        result = score_dictation(spec.sentence_text, spec.answer)

    now = utc_now()
    with transaction() as connection:
        existing = connection.execute(
            """SELECT attempts, best_score, interval_days, ease, error_count,
                      last_dictation_score, shadowing_rating, last_result_json
               FROM listening_sentence_progress
               WHERE user_id = ? AND article_id = ? AND sentence_index = ?""",
            (user["id"], spec.article_id, spec.sentence_index),
        ).fetchone()
        previous_interval = int(existing["interval_days"] if existing else 0)
        previous_ease = float(existing["ease"] if existing else 2.3)
        dictation_score = (
            float(result["score"])
            if spec.stage != "shadowing"
            else (float(existing["last_dictation_score"]) if existing and existing["last_dictation_score"] is not None else None)
        )
        shadowing_rating = (
            int(spec.rating)
            if spec.stage == "shadowing"
            else (int(existing["shadowing_rating"]) if existing and existing["shadowing_rating"] is not None else None)
        )
        component_scores = [score for score in (dictation_score, float(shadowing_rating * 20) if shadowing_rating else None) if score is not None]
        review_score = min(component_scores) if component_scores else float(result["score"])
        interval, ease, due = _schedule(review_score, previous_interval, previous_ease)
        error_count = int(existing["error_count"] if existing else 0)
        counts = result.get("counts") or {}
        error_count += int(counts.get("missing", 0)) + int(counts.get("extra", 0)) + int(counts.get("wrong", 0))
        attempts = int(existing["attempts"] if existing else 0) + 1
        best_score = max(float(existing["best_score"] if existing else 0), float(result["score"]))
        stored_result = result
        if spec.stage == "shadowing" and existing:
            previous_result = _decode_result(existing["last_result_json"])
            if previous_result.get("segments"):
                stored_result = previous_result
        connection.execute(
            """INSERT INTO listening_sentence_progress(
                   user_id, article_id, sentence_index, sentence_text, attempts,
                   best_score, last_score, last_dictation_score, shadowing_rating,
                   last_stage, error_count, interval_days,
                   ease, due_at, last_result_json, last_practiced_at, updated_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id, article_id, sentence_index) DO UPDATE SET
                   sentence_text = excluded.sentence_text,
                   attempts = excluded.attempts,
                   best_score = excluded.best_score,
                   last_score = excluded.last_score,
                   last_dictation_score = excluded.last_dictation_score,
                   shadowing_rating = excluded.shadowing_rating,
                   last_stage = excluded.last_stage,
                   error_count = excluded.error_count,
                   interval_days = excluded.interval_days,
                   ease = excluded.ease,
                   due_at = excluded.due_at,
                   last_result_json = excluded.last_result_json,
                   last_practiced_at = excluded.last_practiced_at,
                   updated_at = excluded.updated_at""",
            (
                user["id"], spec.article_id, spec.sentence_index, spec.sentence_text,
                attempts, best_score, float(result["score"]), dictation_score, shadowing_rating,
                spec.stage, error_count,
                interval, ease, due.isoformat(timespec="seconds"),
                json.dumps(stored_result, ensure_ascii=False), now, now,
            ),
        )
        row = connection.execute(
            """SELECT * FROM listening_sentence_progress
               WHERE user_id = ? AND article_id = ? AND sentence_index = ?""",
            (user["id"], spec.article_id, spec.sentence_index),
        ).fetchone()
    return {"result": result, "progress": _row_payload(row)}
