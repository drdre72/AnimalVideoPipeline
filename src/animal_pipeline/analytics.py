from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

from .core import new_id


def import_snapshots(conn: sqlite3.Connection, path: Path) -> int:
    records = json.loads(path.read_text())
    count = 0
    for item in records:
        if int(item["views"]) < 0 or not item["source"]:
            raise ValueError("invalid performance source or views")
        datetime.fromisoformat(item["published_at"])
        datetime.fromisoformat(item["observed_at"])
        cur = conn.execute(
            "INSERT OR IGNORE INTO analytics(id,video_id,platform,observed_at,published_at,"
            "views,likes,watch_seconds,completion,source,comments,subscribers,shares) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (new_id("snapshot"), item["video_id"], item["platform"],
             item["observed_at"], item["published_at"], item["views"],
             item.get("likes"), item.get("watch_seconds"), item.get("completion"),
             item["source"], item.get("comments"), item.get("subscribers"),
             item.get("shares")))
        count += cur.rowcount
    return count


def report(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute("SELECT * FROM analytics ORDER BY observed_at").fetchall()
    result = []
    for row in rows:
        age = (datetime.fromisoformat(row["observed_at"]) -
               datetime.fromisoformat(row["published_at"])).total_seconds() / 86400
        result.append({"video_id": row["video_id"], "platform": row["platform"],
                       "age_days": round(age, 2),
                       "views_per_day": round(row["views"] / max(age, 1), 2),
                       "source": row["source"]})
    return result
