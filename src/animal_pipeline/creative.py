from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import datetime

from .budget import footage_budget, select_profile, species_match
from .core import canonical_hash, new_id, now, settings
from .evidence import validate_segments
from .media import rights_errors


def _topic_feedback(conn: sqlite3.Connection, topic_id: str) -> float | None:
    rows = conn.execute(
        "SELECT a.video_id, a.platform, a.views, a.published_at, a.observed_at "
        "FROM analytics a JOIN plans p ON p.id = a.video_id "
        "WHERE p.topic_id=? UNION ALL "
        "SELECT a.video_id, a.platform, a.views, a.published_at, a.observed_at "
        "FROM analytics a JOIN youtube_videos y ON y.video_id = a.video_id "
        "JOIN plans p ON p.id = y.plan_id WHERE p.topic_id=?",
        (topic_id, topic_id)).fetchall()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (row["video_id"], row["platform"])
        if key not in latest or row["observed_at"] > latest[key]["observed_at"]:
            latest[key] = row
    rates = []
    for row in latest.values():
        age = (datetime.fromisoformat(row["observed_at"]) -
               datetime.fromisoformat(row["published_at"])).total_seconds() / 86400
        rates.append(row["views"] / max(age, 1))
    return sorted(rates)[len(rates) // 2] if rates else None


def _comparable_demand(conn: sqlite3.Connection, species: str) -> float | None:
    keyword = species.split()[0].lower()
    if len(keyword) < 3:
        return None
    rates = []
    for row in conn.execute("SELECT title,uploaded_at,observed_at,views FROM competitors"):
        if keyword not in (row["title"] or "").lower():
            continue
        days = max(1.0, (datetime.fromisoformat(row["observed_at"]) -
                         datetime.fromisoformat(row["uploaded_at"])).total_seconds() / 86400)
        rates.append(row["views"] / days)
    return sorted(rates)[len(rates) // 2] if rates else None


def rank_topics(conn: sqlite3.Connection) -> list[dict]:
    cfg = settings().get("ranking", {})
    weights = cfg.get("weights", {})
    formula = cfg.get("formula_version", "v2")
    outlier_cap = float(cfg.get("outlier_views_per_day", 500))
    exported_pillars = {row["pillar"] for row in conn.execute(
        "SELECT DISTINCT t.pillar FROM topics t JOIN plans p ON p.topic_id=t.id "
        "WHERE p.status='EXPORTED'")}
    results = []
    for topic in conn.execute("SELECT * FROM topics"):
        facts = conn.execute("SELECT COUNT(*) FROM facts WHERE topic_id=? AND status='approved'",
                             (topic["id"],)).fetchone()[0]
        matching = [a for a in conn.execute("SELECT * FROM assets")
                    if species_match(topic["species"], a["species"])]
        assets = [a for a in matching if not rights_errors(a)]
        usable = sum(a["duration"] for a in assets)
        exact = sum(1 for a in assets if a["species"] == topic["species"])
        views_per_day = _topic_feedback(conn, topic["id"])
        comparable = _comparable_demand(conn, topic["species"])
        signals = {
            "evidence": {"value": min(facts, 5), "basis": "observed",
                       "note": f"{facts} approved facts, capped at 5"},
            "footage": {"value": round(min(usable, 60) / 60, 3), "basis": "observed",
                        "note": f"{usable:.1f}s usable of 60s target"},
            "specificity": {"value": exact / len(assets) if assets else 0, "basis": "observed",
                            "note": f"{exact} of {len(assets)} assets exactly species-tagged"},
            "rights": {"value": len(assets) / len(matching) if matching else 0,
                       "basis": "observed",
                       "note": f"{len(assets)} of {len(matching)} matching assets rights-clear"},
            "own_performance": {"value": round(min(views_per_day or 0, outlier_cap), 2),
                                "basis": "observed",
                                "note": "median views/day, latest snapshot per platform"},
            "comparable_demand": {"value": round(comparable or 0, 2), "basis": "predicted",
                                  "note": "median views/day of keyword-matched public metadata"},
            "novelty": {"value": 0 if topic["pillar"] in exported_pillars else 1,
                        "basis": "heuristic",
                        "note": "controlled exploration: pillar has no exported video"},
            "behavior": {"value": 1 if any(a["behavior"] for a in assets) else 0,
                         "basis": "observed",
                         "note": "documented visual behaviour in cataloged clips"},
            "cost": {"value": round(min(usable / 60, 1), 2), "basis": "predicted",
                     "note": "rough render minutes relative to a one-minute short"}}
        contributions = {
            "evidence": min(facts, 5) * weights.get("evidence", 2),
            "footage": signals["footage"]["value"] * weights.get("footage", 10),
            "specificity": signals["specificity"]["value"] * weights.get("specificity", 2),
            "rights": signals["rights"]["value"] * weights.get("rights", 5),
            "own_performance": (min(views_per_day or 0, outlier_cap) /
                                outlier_cap * weights.get("own_performance", 8)),
            "comparable_demand": (min(comparable or 0, outlier_cap) /
                                  outlier_cap * weights.get("comparable_demand", 4)),
            "novelty": signals["novelty"]["value"] * weights.get("novelty", 3),
            "behavior": signals["behavior"]["value"] * weights.get("behavior", 2),
            "cost": signals["cost"]["value"] * weights.get("cost", -2)}
        score = round(sum(contributions.values()), 3)
        reasons = {"formula": formula, "approved_facts": facts,
                   "usable_visuals": len(assets), "usable_footage_s": round(usable, 1),
                   "analytics": ({"median_views_per_day": round(views_per_day, 2),
                                  "rank_boost": round(contributions["own_performance"], 2)}
                                 if views_per_day is not None else "no observed performance"),
                   "signals": signals, "contributions": {k: round(v, 3) for k, v in
                                                          contributions.items()},
                   "method": "transparent weighted formula, not a virality guarantee"}
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
    assets = [dict(a) for a in conn.execute("SELECT * FROM assets")
              if species_match(topic["species"], a["species"]) and not rights_errors(a)]
    if not facts or not assets:
        raise ValueError("approved evidence and licensed, non-test footage are both required")
    footage = footage_budget(conn, topic_id)
    config = settings()["llm"]
    prompt = json.dumps({"instruction": "Write an original animal video narrative for a vertical "
                         "short. Only use the supplied facts and footage. Return JSON with keys "
                         "hooks (three distinct strings), title, segments (list of objects "
                         "text, fact_ids, asset_id, start_s, duration_s), description. "
                         "Each segment must reference a supporting fact and a matching clip; "
                         "no invented claims. start_s is the in-point in seconds INTO the "
                         "source clip (0 means clip start), not a position in the final "
                         "timeline; segments are played in list order. The sum of segment "
                         "duration_s must not exceed footage_budget.limits.max_total_s, each "
                         "shot must fit inside its asset's in_s..out_s window (start_s + "
                         "duration_s <= that asset's duration) and stay under "
                         "limits.max_shot_s, shots on the same asset must not overlap, and the "
                         "whole script must read within narration.estimated_max_words words. "
                         "Segment text is spoken aloud: write measurements in words "
                         "('8 centimeters'), never abbreviations ('8 cm').",
                         "topic": dict(topic), "facts": facts,
                         "footage_budget": footage})
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
    footage = footage_budget(conn, topic_id, simulation=simulation)
    total_s = sum(float(s.get("duration_s", 0)) for s in segments)
    if total_s > footage["limits"]["max_total_s"] + 0.05:
        errors.append("plan exceeds usable footage budget")
    for segment in segments:
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (segment.get("asset_id"),)).fetchone()
        if not asset or not species_match(topic["species"], asset["species"]):
            errors.append("missing or wrong-species footage")
        elif rights_errors(asset, publishable=not simulation):
            errors.extend(rights_errors(asset, publishable=not simulation))
        if float(segment.get("start_s", -1)) < 0 or float(segment.get("duration_s", 0)) <= 0:
            errors.append("invalid shot timing")
        elif float(segment["duration_s"]) > footage["limits"]["max_shot_s"] + 0.05:
            errors.append("shot exceeds maximum shot length")
        elif asset and float(segment["start_s"]) + float(segment["duration_s"]) > asset["duration"] + 0.05:
            errors.append("shot exceeds source footage")
    if len(plan.get("hooks", [])) != 3 or not plan.get("title") or errors:
        raise ValueError("; ".join(errors or ["plan requires three hooks and a title"]))
    rev = conn.execute("SELECT COALESCE(MAX(revision),0)+1 FROM plans WHERE topic_id=?",
                       (topic_id,)).fetchone()[0]
    plan_id = new_id("plan")
    plan = {**plan, "footage_profile": {"profile": footage["profile"],
                                        "target_s": footage["target_s"],
                                        "usable_s": footage["total_usable_s"],
                                        "reason": footage["reason"]}}
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


OPEN_REVISIONS = ("SCRIPT_READY", "READY_FOR_REVIEW", "APPROVED", "EXPORTED")


def draft_ready(conn: sqlite3.Connection, limit: int) -> dict:
    drafted, errors, needs_media, needs_evidence = [], {}, {}, {}
    for topic in rank_topics(conn):
        if len(drafted) >= limit:
            break
        reasons = topic["reasons"]
        if not reasons["approved_facts"]:
            needs_evidence[topic["id"]] = "no approved facts"
            continue
        if not reasons["usable_visuals"]:
            needs_media[topic["id"]] = "no rights-cleared footage"
            continue
        if conn.execute("SELECT 1 FROM plans WHERE topic_id=? AND status IN "
                        f"({','.join('?' * len(OPEN_REVISIONS))}) LIMIT 1",
                        (topic["id"], *OPEN_REVISIONS)).fetchone():
            continue
        footage = footage_budget(conn, topic["id"])
        if footage["needs_media"]:
            needs_media[topic["id"]] = footage["reason"]
            continue
        try:
            drafted.append(save_plan(conn, generate_plan(conn, topic["id"]),
                                     footage_first=footage["profile"] != "EXTENDED",
                                     reason=footage["reason"]))
        except (OSError, urllib.error.URLError) as exc:
            errors["llm"] = f"planner unavailable: {exc}"
            break
        except (ValueError, KeyError, json.JSONDecodeError) as exc:
            errors[topic["id"]] = str(exc)[:200]
    return {"drafted": drafted, "errors": errors, "needs_media": needs_media,
            "needs_evidence": needs_evidence}
