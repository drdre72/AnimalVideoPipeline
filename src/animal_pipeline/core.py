from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import yaml

_source_root = Path(__file__).resolve().parents[2]
ROOT = Path(os.environ.get("NAMINAL_ROOT", _source_root if (
    _source_root / "config/settings.yaml").is_file() else Path.cwd())).resolve()


def settings() -> dict:
    return yaml.safe_load((ROOT / "config/settings.yaml").read_text())


def content() -> dict:
    return yaml.safe_load((ROOT / "config/content.yaml").read_text())


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


SCHEMA = """
CREATE TABLE IF NOT EXISTS topics (
 id TEXT PRIMARY KEY, species TEXT NOT NULL, scientific_name TEXT NOT NULL,
 pillar TEXT NOT NULL, score REAL NOT NULL DEFAULT 0, reasons TEXT NOT NULL DEFAULT '{}',
 status TEXT NOT NULL DEFAULT 'DISCOVERED', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
 id TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id),
 statement TEXT NOT NULL, organization TEXT NOT NULL, source_ref TEXT NOT NULL,
 excerpt TEXT NOT NULL, retrieved_at TEXT NOT NULL, confidence TEXT NOT NULL,
 qualification TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'proposed'
   CHECK(status IN ('proposed','approved','superseded','rejected')),
 supersedes TEXT REFERENCES facts(id), created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS assets (
 id TEXT PRIMARY KEY, path TEXT NOT NULL, sha256 TEXT NOT NULL,
 source_ref TEXT NOT NULL, creator TEXT NOT NULL, license_type TEXT NOT NULL,
 evidence TEXT NOT NULL, attribution TEXT NOT NULL, commercial INTEGER NOT NULL,
 modifications INTEGER NOT NULL, restrictions TEXT NOT NULL DEFAULT '',
 expires_at TEXT, approved INTEGER NOT NULL DEFAULT 0, test_only INTEGER NOT NULL DEFAULT 0,
 species TEXT NOT NULL, behavior TEXT NOT NULL DEFAULT '', duration REAL NOT NULL,
 width INTEGER NOT NULL, height INTEGER NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS plans (
 id TEXT PRIMARY KEY, topic_id TEXT NOT NULL REFERENCES topics(id),
 revision INTEGER NOT NULL, content TEXT NOT NULL, hash TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'SCRIPT_READY', video_path TEXT,
 video_hash TEXT, qc TEXT, approved_hash TEXT, created_at TEXT NOT NULL,
 UNIQUE(topic_id,revision)
);
CREATE TABLE IF NOT EXISTS jobs (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, target_id TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'queued', attempts INTEGER NOT NULL DEFAULT 0,
 error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, claimed_at TEXT,
 UNIQUE(kind,target_id)
);
CREATE TABLE IF NOT EXISTS analytics (
 id TEXT PRIMARY KEY, video_id TEXT NOT NULL, platform TEXT NOT NULL,
 observed_at TEXT NOT NULL, published_at TEXT NOT NULL, views INTEGER NOT NULL,
 likes INTEGER, watch_seconds REAL, completion REAL, source TEXT NOT NULL,
 UNIQUE(video_id,platform,observed_at)
);
CREATE TABLE IF NOT EXISTS competitors (
 id TEXT PRIMARY KEY, channel TEXT NOT NULL, video_id TEXT NOT NULL,
 title TEXT NOT NULL, category TEXT NOT NULL, uploaded_at TEXT NOT NULL,
 observed_at TEXT NOT NULL, duration_s REAL NOT NULL, views INTEGER NOT NULL,
 likes INTEGER, subscribers INTEGER, source TEXT NOT NULL,
 UNIQUE(channel,video_id,observed_at)
);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    db = path or ROOT / settings()["storage"]["database"]
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db, isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(SCHEMA)
    if "claimed_at" not in {row["name"] for row in
                          conn.execute("PRAGMA table_info(jobs)")}:
        conn.execute("ALTER TABLE jobs ADD COLUMN claimed_at TEXT")
    return conn


@contextmanager
def transaction(conn: sqlite3.Connection):
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
