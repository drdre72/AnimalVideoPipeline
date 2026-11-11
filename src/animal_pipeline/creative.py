from __future__ import annotations

import json
import sqlite3
import urllib.request

from .core import canonical_hash, new_id, now, settings
from .evidence import validate_segments
from .media import rights_errors


def rank_topics(conn: sqlite3.Connection) -> list[dict]:
    results = []
    for topic in conn.execute("SELECT * FROM topics"):
        facts = conn.execute("SELECT COUNT(*) FROM facts WHERE topic_id=? AND status='approved'",
                             (topic["id"],)).fetchone()[0]
        assets = [a for a in conn.execute("SELECT * FROM assets WHERE species=?", (topic["species"],))
                  if not rights_errors(a)]
        score = min(facts, 5) * 2 + min(len(assets), 3) * 5
        reasons = {"approved_facts": facts, "usable_visuals": len(assets),
                   "method": "editorial heuristic, not a virality prediction"}
        conn.execute("UPDATE topics SET score=?,reasons=? WHERE id=?",
                     (score, json.dumps(reasons), topic["id"]))
        results.append({"id": topic["id"], "species": topic["species"],
                        "score": score, "reasons": reasons})
    return sorted(results, key=lambda t: -t["score"])


def generate_plan(conn: sqlite3.Connection, topic_id: str) -> dict:
    topic = conn.execute("SELECT * FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not topic:
        raise ValueError("unknown topic")
    facts = [dict(r) for r in conn.execute(
        "SELECT * FROM facts WHERE topic_id=? AND status='approved'", (topic_id,))]
    assets = [dict(a) for a in conn.execute("SELECT * FROM assets WHERE species=?", (topic["species"],))
              if not rights_errors(a)]
    if not facts or not assets:
        raise ValueError("approved evidence and licensed, non-test footage are both required")
    config = settings()["llm"]
    prompt = json.dumps({"instruction": "Write an original animal video narrative. Only use the supplied facts. "
                         "Return JSON with keys hooks (three distinct strings), title, segments (list of objects "
                         "text, fact_ids, asset_id, start_s, duration_s), description. "
                         "Each segment must reference a supporting fact and a matching clip; no invented claims.",
                         "topic": dict(topic), "facts": facts,
                         "assets": [{k: a[k] for k in ("id", "species", "behavior", "duration")}
                                    for a in assets]})
    req = urllib.request.Request(config["base_url"].rstrip("/") + "/api/generate",
                                 data=json.dumps({"model": config["model"], "prompt": prompt,
                                                  "stream": False, "format": "json",
                                                  "keep_alive": 0}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as response:
        plan = json.loads(json.loads(response.read())["response"])
    plan["topic_id"] = topic_id
    return plan


def save_plan(conn: sqlite3.Connection, plan: dict, *, simulation: bool = False,
              footage_first: bool = False, reason: str = "") -> str:
    if footage_first:
        if len(reason.strip()) < 15:
            raise ValueError("footage-first exception requires an editorial reason")
        plan = {**plan, "runtime_exception": reason.strip()}
    elif "runtime_exception" in plan:
        raise ValueError("runtime exception requires explicit footage-first selection")
    topic_id = plan["topic_id"]
    segments = plan.get("segments", [])
    errors = validate_segments(conn, topic_id, segments)
    topic = conn.execute("SELECT species FROM topics WHERE id=?", (topic_id,)).fetchone()
    if not topic:
        raise ValueError("unknown topic")
    for segment in segments:
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (segment.get("asset_id"),)).fetchone()
        if not asset or asset["species"] != topic["species"]:
            errors.append("missing or wrong-species footage")
        elif rights_errors(asset, publishable=not simulation):
            errors.extend(rights_errors(asset, publishable=not simulation))
        if float(segment.get("start_s", -1)) < 0 or float(segment.get("duration_s", 0)) <= 0:
            errors.append("invalid shot timing")
        elif asset and float(segment["start_s"]) + float(segment["duration_s"]) > asset["duration"] + 0.05:
            errors.append("shot exceeds source footage")
    if len(plan.get("hooks", [])) != 3 or not plan.get("title") or errors:
        raise ValueError("; ".join(errors or ["plan requires three hooks and a title"]))
    rev = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM plans WHERE topic_id=?",
                       (topic_id,)).fetchone()[0]
    plan_id = new_id("plan")
    conn.execute("UPDATE plans SET status='CHANGES_REQUESTED',approved_hash=NULL "
                 "WHERE topic_id=? AND status IN ('READY_FOR_REVIEW','APPROVED','EXPORTED')", (topic_id,))
    conn.execute("INSERT INTO plans(id,topic_id,revision,content,hash,created_at) VALUES(?,?,?,?,?,?)",
                 (plan_id, topic_id, rev, json.dumps(plan), canonical_hash(plan), now()))
    return plan_id


def revise_plan(conn: sqlite3.Connection, plan_id: str, updated: dict) -> str:
    original = conn.execute("SELECT topic_id FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not original or updated.get("topic_id") != original["topic_id"]:
        raise ValueError("revision must remain with original topic")
    return save_plan(conn, updated)
