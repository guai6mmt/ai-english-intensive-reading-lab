from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .config import config, ensure_server_dirs


BASE_SCHEMA_VERSION = 1
SCHEMA_VERSION = 5

MIGRATIONS: dict[int, tuple[str, ...]] = {
    2: (
        """CREATE TABLE IF NOT EXISTS app_passwords (
               id TEXT PRIMARY KEY,
               user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               label TEXT NOT NULL,
               password_hash TEXT NOT NULL,
               scope TEXT NOT NULL DEFAULT 'dav:read',
               created_at TEXT NOT NULL,
               last_used_at TEXT,
               revoked_at TEXT
           )""",
        "CREATE INDEX IF NOT EXISTS idx_app_pw_user ON app_passwords(user_id)",
    ),
    3: (
        """CREATE TABLE IF NOT EXISTS content_bundle_links (
               id TEXT PRIMARY KEY,
               article_source_id TEXT NOT NULL UNIQUE,
               media_collection_id TEXT NOT NULL UNIQUE REFERENCES collections(id) ON DELETE CASCADE,
               issue_key TEXT NOT NULL DEFAULT '',
               match_method TEXT NOT NULL DEFAULT 'automatic',
               confidence REAL NOT NULL DEFAULT 0,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )""",
        """CREATE TABLE IF NOT EXISTS article_media_links (
               article_id TEXT PRIMARY KEY,
               article_source_id TEXT NOT NULL,
               media_id TEXT NOT NULL UNIQUE REFERENCES media_items(id) ON DELETE CASCADE,
               match_method TEXT NOT NULL DEFAULT 'automatic',
               confidence REAL NOT NULL DEFAULT 0,
               confirmed INTEGER NOT NULL DEFAULT 0,
               created_at TEXT NOT NULL,
               updated_at TEXT NOT NULL
           )""",
        "CREATE INDEX IF NOT EXISTS idx_article_media_source ON article_media_links(article_source_id)",
    ),
    4: (
        """CREATE TABLE IF NOT EXISTS listening_sentence_progress (
               user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
               article_id TEXT NOT NULL,
               sentence_index INTEGER NOT NULL,
               sentence_text TEXT NOT NULL,
               attempts INTEGER NOT NULL DEFAULT 0,
               best_score REAL NOT NULL DEFAULT 0,
               last_score REAL NOT NULL DEFAULT 0,
               last_stage TEXT NOT NULL DEFAULT 'dictation',
               error_count INTEGER NOT NULL DEFAULT 0,
               interval_days INTEGER NOT NULL DEFAULT 0,
               ease REAL NOT NULL DEFAULT 2.3,
               due_at TEXT NOT NULL,
               last_result_json TEXT NOT NULL DEFAULT '{}',
               last_practiced_at TEXT NOT NULL,
               updated_at TEXT NOT NULL,
               PRIMARY KEY (user_id, article_id, sentence_index)
           )""",
        """CREATE INDEX IF NOT EXISTS idx_listening_review_due
               ON listening_sentence_progress(user_id, due_at, last_score)""",
        """CREATE INDEX IF NOT EXISTS idx_listening_article
               ON listening_sentence_progress(user_id, article_id, sentence_index)""",
    ),
    5: (
        "ALTER TABLE listening_sentence_progress ADD COLUMN last_dictation_score REAL",
        "ALTER TABLE listening_sentence_progress ADD COLUMN shadowing_rating INTEGER",
    ),
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    connection = sqlite3.connect(config.database_path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    connection = connect()
    try:
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _schema_sql() -> str:
    return """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY,
        username TEXT NOT NULL COLLATE NOCASE UNIQUE,
        password_hash TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'admin',
        created_at TEXT NOT NULL,
        last_login_at TEXT
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT NOT NULL UNIQUE,
        csrf_token TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash);
    CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at);

    CREATE TABLE IF NOT EXISTS collections (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        sort_order INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(name)
    );

    CREATE TABLE IF NOT EXISTS media_items (
        id TEXT PRIMARY KEY,
        collection_id TEXT REFERENCES collections(id) ON DELETE SET NULL,
        title TEXT NOT NULL,
        original_name TEXT NOT NULL,
        relative_path TEXT NOT NULL DEFAULT '',
        storage_path TEXT NOT NULL UNIQUE,
        extension TEXT NOT NULL,
        mime_type TEXT NOT NULL,
        file_size INTEGER NOT NULL,
        sha256 TEXT NOT NULL UNIQUE,
        duration_ms INTEGER,
        bitrate INTEGER,
        sample_rate INTEGER,
        channels INTEGER,
        description TEXT NOT NULL DEFAULT '',
        difficulty TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        deleted_at TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_media_title ON media_items(title);
    CREATE INDEX IF NOT EXISTS idx_media_collection ON media_items(collection_id);
    CREATE INDEX IF NOT EXISTS idx_media_deleted ON media_items(deleted_at);

    CREATE TABLE IF NOT EXISTS favorites (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        media_id TEXT NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
        created_at TEXT NOT NULL,
        PRIMARY KEY (user_id, media_id)
    );

    CREATE TABLE IF NOT EXISTS play_progress (
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        media_id TEXT NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
        position_ms INTEGER NOT NULL DEFAULT 0,
        playback_rate REAL NOT NULL DEFAULT 1.0,
        completed INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (user_id, media_id)
    );

    CREATE TABLE IF NOT EXISTS bookmarks (
        id TEXT PRIMARY KEY,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        media_id TEXT NOT NULL REFERENCES media_items(id) ON DELETE CASCADE,
        position_ms INTEGER NOT NULL,
        note TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS import_jobs (
        id TEXT PRIMARY KEY,
        user_id TEXT REFERENCES users(id) ON DELETE SET NULL,
        kind TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        total_files INTEGER NOT NULL DEFAULT 0,
        processed_files INTEGER NOT NULL DEFAULT 0,
        imported_files INTEGER NOT NULL DEFAULT 0,
        duplicate_files INTEGER NOT NULL DEFAULT 0,
        failed_files INTEGER NOT NULL DEFAULT 0,
        message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT
    );

    CREATE TABLE IF NOT EXISTS import_job_items (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
        relative_path TEXT NOT NULL,
        status TEXT NOT NULL,
        media_id TEXT REFERENCES media_items(id) ON DELETE SET NULL,
        message TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_import_items_job ON import_job_items(job_id);

    CREATE TABLE IF NOT EXISTS upload_sessions (
        id TEXT PRIMARY KEY,
        job_id TEXT NOT NULL REFERENCES import_jobs(id) ON DELETE CASCADE,
        user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        original_name TEXT NOT NULL,
        relative_path TEXT NOT NULL,
        staging_path TEXT NOT NULL UNIQUE,
        total_bytes INTEGER NOT NULL,
        received_bytes INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_upload_sessions_job ON upload_sessions(job_id);
    """


def _migrate(connection: sqlite3.Connection) -> None:
    row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    current = int(row[0] or 0)
    if current == 0:
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (BASE_SCHEMA_VERSION, utc_now()),
        )
        current = BASE_SCHEMA_VERSION
    for version in sorted(value for value in MIGRATIONS if current < value <= SCHEMA_VERSION):
        for statement in MIGRATIONS[version]:
            try:
                connection.execute(statement)
            except sqlite3.OperationalError as exc:
                # A development build may have created a newly introduced column
                # before its migration version was finalized. Treat that one safe,
                # observable state as already applied; all other SQL errors remain fatal.
                if not (statement.lstrip().upper().startswith("ALTER TABLE") and "duplicate column name" in str(exc).lower()):
                    raise
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, utc_now()),
        )
        current = version


def initialize_database() -> None:
    ensure_server_dirs()
    with connect() as connection:
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.executescript(_schema_sql())
        _migrate(connection)
        connection.execute("PRAGMA optimize")
        # A process restart cannot resume an in-process worker. Keep the durable
        # record honest so the administrator can retry the scan.
        connection.execute(
            """UPDATE import_jobs
               SET status = 'interrupted', message = '服务重启，任务已中断，可重新发起。',
                   updated_at = ?, finished_at = ?
               WHERE status IN ('pending', 'running')""",
            (utc_now(), utc_now()),
        )
        connection.commit()


def database_ok() -> bool:
    try:
        with connect() as connection:
            return connection.execute("SELECT 1").fetchone()[0] == 1
    except sqlite3.Error:
        return False


def database_size(path: Path | None = None) -> int:
    target = path or config.database_path
    return target.stat().st_size if target.exists() else 0
