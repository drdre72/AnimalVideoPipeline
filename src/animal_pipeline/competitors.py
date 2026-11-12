from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import datetime
from pathlib import Path

from .core import new_id


def import_metadata(conn: sqlite3.Connection, path: Path) -> int:
    records = json.loads(path.read_text())
    count = 0
    for row in records:
        age = (datetime.fromisoformat(row["observed_at"]) -
               datetime.fromisoformat(row["uploaded_at"])).total_seconds()
        if not row["source"] or not row["channel"] or age < 0 or int(row["views"]) < 0:
            raise ValueError("invalid competitor metadata")
        cursor = conn.execute(
            "INSERT OR IGNORE INTO competitors VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("benchmark"), row["channel"], row["video_id"], row["title"],
             row["category"], row["uploaded_at"], row["observed_at"],
             row["duration_s"], row["views"], row.get("likes"),
             row.get("subscribers"), row["source"]))
        count += cursor.rowcount
    return count


def report(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM competitors").fetchall()
    unique = {}
    for row in rows:
        key = (row["channel"], row["video_id"])
        if key not in unique or row["observed_at"] > unique[key]["observed_at"]:
            unique[key] = row
    by_channel = {}
    for row in unique.values():
        days = max(1, (datetime.fromisoformat(row["observed_at"]) -
                       datetime.fromisoformat(row["uploaded_at"])).total_seconds() / 86400)
        by_channel.setdefault(row["channel"], []).append((row, row["views"] / days))
    result = []
    for channel, items in by_channel.items():
        median = statistics.median(velocity for _, velocity in items)
        for row, velocity in items:
            result.append({"channel": channel, "video_id": row["video_id"],
                           "category": row["category"], "views_per_day": round(velocity, 1),
                           "relative_to_channel_median": round(velocity / median, 2) if median else None,
                           "source": row["source"]})
    return result
