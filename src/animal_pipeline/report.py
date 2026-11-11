"""Weekly optimization report — descriptive, not causal.

Summarizes observed performance, pipeline failures and candidate
experiments from the local database. Correlations are reported as
correlations; nothing here claims that a signal caused an outcome.
"""
from __future__ import annotations

import sqlite3
import statistics
from datetime import datetime, timedelta, timezone

from .budget import footage_budget
from .core import now


def _latest_snapshots(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    rows = conn.execute("SELECT * FROM analytics ORDER BY observed_at").fetchall()
    latest: dict[tuple[str, str], sqlite3.Row] = {}
    for row in rows:
        key = (row["video_id"], row["platform"])
        if key not in latest or row["observed_at"] >= latest[key]["observed_at"]:
            latest[key] = row
    return list(latest.values())


def weekly(conn: sqlite3.Connection, days: int = 7) -> dict:
    snapshots = _latest_snapshots(conn)
    top = []
    for row in snapshots:
        try:
            age = max(1.0, (datetime.fromisoformat(row["observed_at"]) -
                            datetime.fromisoformat(row["published_at"])
                            ).total_seconds() / 86400)
        except ValueError:
            age = 1.0
        top.append({"video_id": row["video_id"], "platform": row["platform"],
                    "views": row["views"], "views_per_day": round(row["views"] / age, 1),
                    "subscribers_gained": row["subscribers"], "source": row["source"]})
    top.sort(key=lambda item: -item["views_per_day"])
    subscribers = sum(r["subscribers"] or 0 for r in snapshots)
    failures = [dict(j) for j in conn.execute(
        "SELECT kind,target_id,attempts,error FROM jobs WHERE status='failed' "
        "ORDER BY created_at DESC LIMIT 10")]
    plans = {row["status"]: row["n"] for row in conn.execute(
        "SELECT status,COUNT(*) n FROM plans GROUP BY status")}
    experiments = []
    for topic in conn.execute("SELECT id,species FROM topics"):
        try:
            budget = footage_budget(conn, topic["id"])
        except ValueError:
            continue
        if budget["needs_media"]:
            experiments.append({"topic": topic["species"], "kind": "needs_media",
                                "detail": budget["reason"]})
        elif not conn.execute("SELECT 1 FROM facts WHERE topic_id=? AND status='approved'",
                              (topic["id"],)).fetchone():
            pending = conn.execute("SELECT COUNT(*) FROM facts WHERE topic_id=? "
                                   "AND status='proposed'", (topic["id"],)).fetchone()[0]
            experiments.append({"topic": topic["species"], "kind": "needs_evidence",
                                "detail": f"{pending} proposed facts awaiting verification"})
        elif not conn.execute("SELECT 1 FROM plans WHERE topic_id=?", (topic["id"],)).fetchone():
            experiments.append({"topic": topic["species"], "kind": "unproduced",
                                "detail": f"{budget['profile']} profile ready; no draft exists"})
    velocities = [item["views_per_day"] for item in top]
    return {"generated_at": now(), "period_days": days,
            "top_videos": top[:5],
            "channel": {"tracked_videos": len({r['video_id'] for r in snapshots}),
                        "subscriber_gain_observed": subscribers,
                        "median_views_per_day": round(statistics.median(velocities), 1)
                        if velocities else None},
            "render_failures": failures,
            "pipeline": plans,
            "experiments": experiments[:10],
            "note": "Descriptive report: observed associations are not causal "
                    "evidence. Verify rights and facts before acting on suggestions."}
