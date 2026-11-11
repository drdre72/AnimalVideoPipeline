from __future__ import annotations

import re
import sqlite3

from .core import new_id, now, transaction

NUMBERS = re.compile(r"(?<![\w])-?\d+(?:\.\d+)?")


def add_topic(conn: sqlite3.Connection, species: str, scientific_name: str, pillar: str) -> str:
    topic_id = new_id("topic")
    conn.execute("INSERT INTO topics(id,species,scientific_name,pillar,created_at) VALUES(?,?,?,?,?)",
                 (topic_id, species, scientific_name, pillar, now()))
    return topic_id


def add_fact(conn: sqlite3.Connection, topic_id: str, statement: str, organization: str,
             source_ref: str, excerpt: str, retrieved_at: str, confidence: str = "qualified",
             qualification: str = "", supersedes: str | None = None) -> str:
    if not all((statement.strip(), organization.strip(), source_ref.strip(), excerpt.strip())):
        raise ValueError("facts require a statement and item-specific evidence")
    fact_id = new_id("fact")
    with transaction(conn):
        if supersedes:
            current = conn.execute("SELECT topic_id,status FROM facts WHERE id=?", (supersedes,)).fetchone()
            if not current or current["topic_id"] != topic_id or current["status"] != "approved":
                raise ValueError("only an approved fact from this topic can be superseded")
            conn.execute("UPDATE facts SET status='superseded' WHERE id=?", (supersedes,))
            conn.execute("UPDATE plans SET status='CHANGES_REQUESTED',approved_hash=NULL "
                         "WHERE topic_id=? AND status IN ('READY_FOR_REVIEW','APPROVED','EXPORTED')", (topic_id,))
        conn.execute("INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                     (fact_id, topic_id, statement, organization, source_ref, excerpt,
                      retrieved_at, confidence, qualification, "proposed", supersedes, now()))
    return fact_id


def approve_fact(conn: sqlite3.Connection, fact_id: str) -> None:
    fact = conn.execute("SELECT * FROM facts WHERE id=?", (fact_id,)).fetchone()
    if not fact or fact["status"] != "proposed":
        raise ValueError("fact is not proposed")
    if fact["supersedes"]:
        prior = conn.execute("SELECT status FROM facts WHERE id=?", (fact["supersedes"],)).fetchone()
        if not prior or prior["status"] != "superseded":
            raise ValueError("superseded fact changed")
    conn.execute("UPDATE facts SET status='approved' WHERE id=?", (fact_id,))


def validate_segments(conn: sqlite3.Connection, topic_id: str, segments: list[dict]) -> list[str]:
    errors = []
    topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not topic or not segments:
        return ["missing topic or script segments"]
    for i, segment in enumerate(segments):
        ids = segment.get("fact_ids", [])
        if not isinstance(ids, list) or not ids:
            errors.append(f"segment {i}: missing fact IDs")
            continue
        facts = []
        for fid in ids:
            fact = conn.execute("SELECT * FROM facts WHERE id=?", (fid,)).fetchone()
            if not fact or fact["topic_id"] != topic_id or fact["status"] != "approved":
                errors.append(f"segment {i}: invalid or stale fact {fid}")
            else:
                facts.append(fact)
        text = segment.get("text", "")
        if not isinstance(text, str) or not text.strip():
            errors.append(f"segment {i}: empty narration")
            continue
        allowed = {float(n) for fact in facts for n in
                   NUMBERS.findall(fact["statement"] + " " + fact["excerpt"])}
        for n in NUMBERS.findall(text):
            if float(n) not in allowed:
                errors.append(f"segment {i}: unsupported number {n}")
        if topic["scientific_name"].lower() in text.lower() and not facts:
            errors.append(f"segment {i}: species not evidenced")
    return errors
