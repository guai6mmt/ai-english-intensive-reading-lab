from __future__ import annotations

import csv
import io
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .auth import current_user
from .config import config
from .database import connect, transaction, utc_now


router = APIRouter(prefix="/api/vocabulary", tags=["vocabulary"])
LEGACY_PATH = config.data_root / "vocabulary.json"
FIXED_INTERVALS = (1, 3, 7, 21, 45)

# Small instant/offline seed. Unknown words can still be saved with their context
# and enriched through the application's configured language model.
LOCAL_GLOSSARY: dict[str, tuple[str, str, str]] = {
    "tariff": ("/ˈtærɪf/", "noun", "关税；关税表"),
    "inflation": ("/ɪnˈfleɪʃn/", "noun", "通货膨胀"),
    "deficit": ("/ˈdefɪsɪt/", "noun", "赤字；不足额"),
    "subsidy": ("/ˈsʌbsədi/", "noun", "补贴；津贴"),
    "recession": ("/rɪˈseʃn/", "noun", "经济衰退"),
    "productivity": ("/ˌprɒdʌkˈtɪvəti/", "noun", "生产率"),
    "regulation": ("/ˌreɡjuˈleɪʃn/", "noun", "监管；规章"),
    "sovereign": ("/ˈsɒvrɪn/", "adjective", "主权的；拥有最高权力的"),
    "geopolitical": ("/ˌdʒiːəʊpəˈlɪtɪkl/", "adjective", "地缘政治的"),
    "populist": ("/ˈpɒpjəlɪst/", "noun/adjective", "民粹主义者；民粹主义的"),
    "monetary": ("/ˈmʌnɪtri/", "adjective", "货币的；金融的"),
    "fiscal": ("/ˈfɪskl/", "adjective", "财政的"),
    "renewable": ("/rɪˈnjuːəbl/", "adjective", "可再生的"),
    "semiconductor": ("/ˌsemikənˈdʌktə/", "noun", "半导体"),
    "diplomacy": ("/dɪˈpləʊməsi/", "noun", "外交；外交手腕"),
    "sanction": ("/ˈsæŋkʃn/", "noun/verb", "制裁；批准"),
    "coalition": ("/ˌkəʊəˈlɪʃn/", "noun", "联盟；联合政府"),
    "election": ("/ɪˈlekʃn/", "noun", "选举"),
    "migration": ("/maɪˈɡreɪʃn/", "noun", "迁移；移民流动"),
    "demographic": ("/ˌdeməˈɡræfɪk/", "adjective", "人口结构的"),
    "manufacturing": ("/ˌmænjuˈfæktʃərɪŋ/", "noun", "制造业"),
    "investment": ("/ɪnˈvestmənt/", "noun", "投资"),
    "consumption": ("/kənˈsʌmpʃn/", "noun", "消费；消耗"),
    "currency": ("/ˈkʌrənsi/", "noun", "货币；通用"),
    "obscure": ("/əbˈskjʊə/", "adjective/verb", "模糊的；使不明显"),
    "prudence": ("/ˈpruːdns/", "noun", "审慎；谨慎"),
    "valiant": ("/ˈvæliənt/", "adjective", "英勇的；勇敢的"),
    "implication": ("/ˌɪmplɪˈkeɪʃn/", "noun", "影响；含义；暗示"),
    "resilience": ("/rɪˈzɪliəns/", "noun", "韧性；复原力"),
}


class VocabularyCreate(BaseModel):
    article_id: str | None = Field(default=None, max_length=200)
    sentence_id: str = Field(default="", max_length=200)
    term: str = Field(min_length=1, max_length=200)
    lemma: str = Field(default="", max_length=200)
    phonetic: str = Field(default="", max_length=200)
    part_of_speech: str = Field(default="", max_length=100)
    definition: str = Field(default="", max_length=4000)
    translation: str = Field(default="", max_length=4000)
    context: str = Field(default="", max_length=10_000)
    source: str = Field(default="", max_length=1000)
    kind: str = Field(default="word", max_length=32)
    layer: str = Field(default="", max_length=100)


class VocabularyUpdate(BaseModel):
    phonetic: str | None = Field(default=None, max_length=200)
    part_of_speech: str | None = Field(default=None, max_length=100)
    definition: str | None = Field(default=None, max_length=4000)
    translation: str | None = Field(default=None, max_length=4000)
    notes: str | None = Field(default=None, max_length=10_000)
    layer: str | None = Field(default=None, max_length=100)
    mastery: str | None = None


class ReviewRating(BaseModel):
    rating: str = Field(pattern="^(again|hard|good|easy)$")
    mode: str = Field(default="adaptive", pattern="^(adaptive|fixed)$")


def normalize_term(value: str) -> str:
    value = str(value or "").strip().lower().replace("’", "'")
    value = re.sub(r"^[^a-z]+|[^a-z'-]+$", "", value)
    return re.sub(r"\s+", " ", value)


def infer_lemma(value: str) -> str:
    word = normalize_term(value)
    if " " in word or len(word) < 4:
        return word
    irregular = {"went": "go", "gone": "go", "better": "good", "best": "good", "children": "child", "men": "man", "women": "woman"}
    if word in irregular:
        return irregular[word]
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ing") and len(word) > 5:
        root = word[:-3]
        if len(root) > 2 and root[-1] == root[-2]:
            root = root[:-1]
        return root
    if word.endswith("ed") and len(word) > 4:
        root = word[:-2]
        if root.endswith("i"):
            return root[:-1] + "y"
        if len(root) > 2 and root[-1] == root[-2]:
            root = root[:-1]
        return root
    if word.endswith("es") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _context_rows(connection: Any, entry_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not entry_ids:
        return {}
    marks = ",".join("?" for _ in entry_ids)
    rows = connection.execute(
        f"SELECT * FROM vocabulary_contexts WHERE entry_id IN ({marks}) ORDER BY created_at DESC",
        entry_ids,
    ).fetchall()
    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(row["entry_id"], []).append(dict(row))
    return result


def _payloads(rows: list[Any], connection: Any) -> list[dict[str, Any]]:
    contexts = _context_rows(connection, [row["id"] for row in rows])
    result = []
    for row in rows:
        item = dict(row)
        item_contexts = contexts.get(item["id"], [])
        item["context_items"] = item_contexts
        item["contexts"] = [ctx["context"] for ctx in item_contexts]
        item["context"] = item["contexts"][0] if item["contexts"] else ""
        item["recognise"] = item["mastery"] == "mastered"
        item["hear"] = item["reps"] >= 2
        item["speak"] = item["reps"] >= 3
        item["write"] = item["mastery"] == "mastered"
        result.append(item)
    return result


def _migrate_legacy(user_id: str) -> None:
    if not LEGACY_PATH.is_file():
        return
    with connect() as connection:
        if connection.execute("SELECT 1 FROM vocabulary_entries WHERE user_id = ? LIMIT 1", (user_id,)).fetchone():
            return
    try:
        items = json.loads(LEGACY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict) or not normalize_term(item.get("term", "")):
            continue
        legacy_contexts = item.get("contexts") or []
        if isinstance(legacy_contexts, str):
            legacy_context = legacy_contexts
        elif isinstance(legacy_contexts, list) and legacy_contexts:
            legacy_context = str(legacy_contexts[-1] or "")
        else:
            legacy_context = ""
        add_entry(
            user_id,
            VocabularyCreate(
                article_id=item.get("article_id"),
                term=str(item.get("term") or ""),
                translation=str(item.get("translation") or ""),
                context=legacy_context,
                kind=str(item.get("kind") or "word"),
                layer=str(item.get("layer") or ""),
            ),
            count_encounter=False,
        )


def add_entry(user_id: str, spec: VocabularyCreate, *, count_encounter: bool = True) -> dict[str, Any]:
    normalized = normalize_term(spec.term)
    if not normalized:
        raise HTTPException(400, "没有识别到可保存的英文单词或短语。")
    lemma = normalize_term(spec.lemma) or infer_lemma(normalized)
    local = LOCAL_GLOSSARY.get(lemma) or LOCAL_GLOSSARY.get(normalized)
    phonetic = spec.phonetic or (local[0] if local else "")
    pos = spec.part_of_speech or (local[1] if local else "")
    translation = spec.translation or (local[2] if local else "")
    now = utc_now()
    with transaction() as connection:
        row = connection.execute(
            "SELECT * FROM vocabulary_entries WHERE user_id = ? AND normalized_term = ?",
            (user_id, normalized),
        ).fetchone()
        if row:
            updates = {
                "lemma": lemma or row["lemma"], "phonetic": phonetic or row["phonetic"],
                "part_of_speech": pos or row["part_of_speech"],
                "definition": spec.definition or row["definition"],
                "translation": translation or row["translation"],
                "kind": spec.kind or row["kind"], "layer": spec.layer or row["layer"],
            }
            connection.execute(
                """UPDATE vocabulary_entries SET lemma=?, phonetic=?, part_of_speech=?, definition=?,
                          translation=?, kind=?, layer=?, encounter_count=encounter_count+?, updated_at=? WHERE id=?""",
                (*updates.values(), int(count_encounter), now, row["id"]),
            )
            entry_id = row["id"]
        else:
            entry_id = uuid.uuid4().hex
            connection.execute(
                """INSERT INTO vocabulary_entries(
                       id,user_id,term,normalized_term,lemma,phonetic,part_of_speech,definition,
                       translation,kind,layer,due_at,created_at,updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (entry_id, user_id, spec.term.strip(), normalized, lemma, phonetic, pos,
                 spec.definition, translation, spec.kind, spec.layer, _today(), now, now),
            )
        if spec.context.strip():
            connection.execute(
                """INSERT OR IGNORE INTO vocabulary_contexts(
                       id,entry_id,article_id,sentence_id,context,source,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (uuid.uuid4().hex, entry_id, spec.article_id, spec.sentence_id,
                 spec.context.strip(), spec.source.strip(), now),
            )
        row = connection.execute("SELECT * FROM vocabulary_entries WHERE id = ?", (entry_id,)).fetchone()
        return _payloads([row], connection)[0]


def lookup_payload(user_id: str, term: str) -> dict[str, Any]:
    normalized = normalize_term(term)
    lemma = infer_lemma(normalized)
    with connect() as connection:
        row = connection.execute(
            """SELECT * FROM vocabulary_entries
               WHERE user_id=? AND normalized_term IN (?,?)
               ORDER BY CASE normalized_term WHEN ? THEN 0 ELSE 1 END LIMIT 1""",
            (user_id, normalized, lemma, normalized),
        ).fetchone()
        if row:
            return {"found": True, "saved": True, "item": _payloads([row], connection)[0]}
        cached = connection.execute("SELECT payload_json, provider FROM dictionary_cache WHERE normalized_term=?", (normalized,)).fetchone()
        if cached:
            try:
                item = json.loads(cached["payload_json"])
                return {"found": True, "saved": False, "cached": True, "provider": cached["provider"], "item": item}
            except json.JSONDecodeError:
                pass
    local = LOCAL_GLOSSARY.get(normalized) or LOCAL_GLOSSARY.get(lemma)
    item = {
        "term": term.strip(), "normalized_term": normalized, "lemma": lemma,
        "phonetic": local[0] if local else "", "part_of_speech": local[1] if local else "",
        "definition": "", "translation": local[2] if local else "", "contexts": [],
    }
    return {"found": bool(local), "saved": False, "item": item}


def cache_lookup(term: str, item: dict[str, Any], provider: str) -> None:
    normalized = normalize_term(term)
    if not normalized:
        return
    with transaction() as connection:
        connection.execute(
            """INSERT INTO dictionary_cache(normalized_term,payload_json,provider,updated_at)
               VALUES(?,?,?,?) ON CONFLICT(normalized_term) DO UPDATE SET
               payload_json=excluded.payload_json,provider=excluded.provider,updated_at=excluded.updated_at""",
            (normalized, json.dumps(item, ensure_ascii=False), provider[:80], utc_now()),
        )


@router.get("")
def list_vocabulary(
    request: Request,
    query: str = "",
    mastery: str = "",
    due: bool = False,
    limit: int = Query(default=500, ge=1, le=2000),
) -> dict[str, Any]:
    user = current_user(request)
    _migrate_legacy(user["id"])
    clauses = ["user_id = ?"]
    params: list[Any] = [user["id"]]
    if query.strip():
        clauses.append("(normalized_term LIKE ? OR translation LIKE ? OR definition LIKE ?)")
        needle = f"%{query.strip().lower()}%"
        params.extend([needle, needle, needle])
    if mastery in {"new", "learning", "mastered"}:
        clauses.append("mastery = ?")
        params.append(mastery)
    if due:
        clauses.append("due_at <= ? AND mastery != 'mastered'")
        params.append(_today())
    with connect() as connection:
        rows = connection.execute(
            f"SELECT * FROM vocabulary_entries WHERE {' AND '.join(clauses)} ORDER BY due_at, updated_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        items = _payloads(list(rows), connection)
        counts = connection.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN due_at <= ? AND mastery != 'mastered' THEN 1 ELSE 0 END) due,
                      SUM(CASE WHEN mastery = 'mastered' THEN 1 ELSE 0 END) mastered
               FROM vocabulary_entries WHERE user_id = ?""",
            (_today(), user["id"]),
        ).fetchone()
    return {"items": items, "summary": {key: int(counts[key] or 0) for key in ("total", "due", "mastered")}}


@router.post("")
def create_vocabulary(spec: VocabularyCreate, request: Request) -> dict[str, Any]:
    user = current_user(request)
    item = add_entry(user["id"], spec)
    return {"item": item}


@router.get("/lookup")
def lookup_vocabulary(term: str, request: Request) -> dict[str, Any]:
    user = current_user(request)
    return lookup_payload(user["id"], term)


@router.patch("/{entry_id}")
def update_vocabulary(entry_id: str, spec: VocabularyUpdate, request: Request) -> dict[str, Any]:
    user = current_user(request)
    values = spec.model_dump(exclude_none=True)
    if values.get("mastery") not in {None, "new", "learning", "mastered"}:
        raise HTTPException(400, "掌握状态无效。")
    if not values:
        raise HTTPException(400, "没有需要更新的字段。")
    values["updated_at"] = utc_now()
    assignments = ",".join(f"{key}=?" for key in values)
    with transaction() as connection:
        result = connection.execute(
            f"UPDATE vocabulary_entries SET {assignments} WHERE id=? AND user_id=?",
            (*values.values(), entry_id, user["id"]),
        )
        if not result.rowcount:
            raise HTTPException(404, "生词不存在。")
        row = connection.execute("SELECT * FROM vocabulary_entries WHERE id=?", (entry_id,)).fetchone()
        item = _payloads([row], connection)[0]
    return {"item": item}


@router.delete("/{entry_id}")
def delete_vocabulary(entry_id: str, request: Request) -> dict[str, bool]:
    user = current_user(request)
    with transaction() as connection:
        result = connection.execute("DELETE FROM vocabulary_entries WHERE id=? AND user_id=?", (entry_id, user["id"]))
    if not result.rowcount:
        raise HTTPException(404, "生词不存在。")
    return {"deleted": True}


def _next_schedule(row: Any, rating: str, mode: str) -> dict[str, Any]:
    reps = int(row["reps"] or 0) + 1
    lapses = int(row["lapses"] or 0) + (1 if rating == "again" else 0)
    difficulty = float(row["difficulty"] or 5)
    stability = float(row["stability"] or 0)
    if mode == "fixed":
        tier = int(row["tier"] or 0)
        if rating == "again":
            tier = max(0, tier - 1)
        elif rating == "hard":
            tier = max(1, tier)
        else:
            tier = min(len(FIXED_INTERVALS), tier + (2 if rating == "easy" else 1))
        days = FIXED_INTERVALS[max(0, tier - 1)] if tier else 0
    else:
        grade = {"again": 1, "hard": 2, "good": 3, "easy": 4}[rating]
        difficulty = min(10.0, max(1.0, difficulty + (3 - grade) * 0.65))
        if rating == "again":
            stability = max(0.2, stability * 0.45)
            days = 0
        else:
            gain = {"hard": 1.25, "good": 2.1, "easy": 3.2}[rating]
            stability = max(1.0, (stability or 0.7) * gain * (11 - difficulty) / 6)
            days = max(1, min(365, round(stability)))
        tier = int(row["tier"] or 0)
    due = (datetime.now(timezone.utc) + timedelta(days=days)).date().isoformat()
    mastery = "mastered" if reps >= 5 and days >= 21 and rating in {"good", "easy"} else "learning"
    state = 3 if rating == "again" else (2 if reps > 1 else 1)
    return {
        "tier": tier, "due_at": due, "reps": reps, "lapses": lapses,
        "stability": round(stability, 4), "difficulty": round(difficulty, 4),
        "state": state, "scheduled_days": days, "last_review": utc_now(), "mastery": mastery,
    }


@router.get("/review/queue")
def review_queue(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    user = current_user(request)
    _migrate_legacy(user["id"])
    with connect() as connection:
        rows = connection.execute(
            """SELECT * FROM vocabulary_entries
               WHERE user_id=? AND due_at<=? AND mastery!='mastered'
               ORDER BY due_at, lapses DESC, created_at LIMIT ?""",
            (user["id"], _today(), limit),
        ).fetchall()
        return {"items": _payloads(list(rows), connection)}


@router.post("/review/{entry_id}")
def review_vocabulary(entry_id: str, spec: ReviewRating, request: Request) -> dict[str, Any]:
    user = current_user(request)
    with transaction() as connection:
        row = connection.execute("SELECT * FROM vocabulary_entries WHERE id=? AND user_id=?", (entry_id, user["id"])).fetchone()
        if not row:
            raise HTTPException(404, "生词不存在。")
        schedule = _next_schedule(row, spec.rating, spec.mode)
        connection.execute(
            """UPDATE vocabulary_entries SET tier=:tier,due_at=:due_at,reps=:reps,lapses=:lapses,
                      stability=:stability,difficulty=:difficulty,state=:state,scheduled_days=:scheduled_days,
                      last_review=:last_review,mastery=:mastery,updated_at=:last_review WHERE id=:id""",
            {**schedule, "id": entry_id},
        )
        connection.execute(
            """INSERT INTO vocabulary_review_log(
                   id,entry_id,user_id,rating,mode,interval_before,interval_after,reviewed_at)
               VALUES(?,?,?,?,?,?,?,?)""",
            (uuid.uuid4().hex, entry_id, user["id"], spec.rating, spec.mode,
             int(row["scheduled_days"] or 0), schedule["scheduled_days"], schedule["last_review"]),
        )
        updated = connection.execute("SELECT * FROM vocabulary_entries WHERE id=?", (entry_id,)).fetchone()
        item = _payloads([updated], connection)[0]
    return {"item": item, "schedule": schedule}


@router.get("/export/{format_name}")
def export_vocabulary(format_name: str, request: Request) -> Response:
    user = current_user(request)
    with connect() as connection:
        rows = connection.execute("SELECT * FROM vocabulary_entries WHERE user_id=? ORDER BY term COLLATE NOCASE", (user["id"],)).fetchall()
        items = _payloads(list(rows), connection)
        logs = [dict(row) for row in connection.execute("SELECT * FROM vocabulary_review_log WHERE user_id=? ORDER BY reviewed_at", (user["id"],)).fetchall()]
    stamp = _today()
    if format_name == "json":
        body = json.dumps({"app": "english-lab", "exported_at": utc_now(), "items": items, "review_log": logs}, ensure_ascii=False, indent=2)
        return Response(body, media_type="application/json; charset=utf-8", headers={"Content-Disposition": f'attachment; filename="english-lab-vocabulary-{stamp}.json"'})
    output = io.StringIO(newline="")
    if format_name == "anki":
        writer = csv.writer(output, delimiter="\t", lineterminator="\n")
        for item in items:
            back = "<br>".join(filter(None, [item["phonetic"], item["translation"] or item["definition"], item["context"]]))
            writer.writerow([item["term"], back])
        mime, ext = "text/tab-separated-values; charset=utf-8", "txt"
    elif format_name == "csv":
        fields = ["term", "lemma", "phonetic", "part_of_speech", "translation", "definition", "mastery", "encounter_count", "due_at", "context"]
        writer = csv.DictWriter(output, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(items)
        mime, ext = "text/csv; charset=utf-8", "csv"
    else:
        raise HTTPException(404, "支持导出 CSV、Anki 或 JSON。")
    return Response("\ufeff" + output.getvalue(), media_type=mime, headers={"Content-Disposition": f'attachment; filename="english-lab-vocabulary-{stamp}.{ext}"'})
