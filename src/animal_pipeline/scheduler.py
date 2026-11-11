from __future__ import annotations

import asyncio
import shutil
import sqlite3
from pathlib import Path

from .core import ROOT, new_id, now, settings, transaction


def enqueue(conn: sqlite3.Connection, kind: str, target_id: str) -> bool:
    if kind not in ("render",):
        raise ValueError("unsupported job kind")
    with transaction(conn):
        cursor = conn.execute("INSERT OR IGNORE INTO jobs(id,kind,target_id,created_at) VALUES(?,?,?,?)",
                              (new_id("job"), kind, target_id, now()))
    return bool(cursor.rowcount)


def recover(conn: sqlite3.Connection) -> int:
    with transaction(conn):
        result = conn.execute("UPDATE jobs SET status='queued',error='recovered after interruption' "
                              "WHERE status='running'")
    return result.rowcount


def retry(conn: sqlite3.Connection, job_id: str) -> bool:
    with transaction(conn):
        result = conn.execute("UPDATE jobs SET status='queued',error='' "
                              "WHERE id=? AND status='failed' AND attempts<3", (job_id,))
    return bool(result.rowcount)


def resource_errors(root: Path = ROOT) -> list[str]:
    cfg = settings()["storage"]
    free = shutil.disk_usage(root).free / 1024**3
    errors = ["low disk"] if free < cfg["min_free_gb"] else []
    try:
        import subprocess
        out = subprocess.run(["memory_pressure", "-Q"], capture_output=True, text=True, timeout=10)
        import re
        match = re.search(r"System-wide free percentage:\s*(\d+)", out.stdout)
        if match and int(match.group(1)) < 10:
            errors.append("low available memory")
    except (OSError, subprocess.TimeoutExpired):
        errors.append("memory availability unknown")
    return errors


async def drain(conn: sqlite3.Connection, renderer, *, limit: int = 1) -> list[dict]:
    results = []
    queue = asyncio.Queue(maxsize=settings()["workers"]["queue_size"])
    rows = conn.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT ?",
                        (min(limit, queue.maxsize),)).fetchall()
    for row in rows:
        queue.put_nowait(row)
    while not queue.empty():
        if resource_errors():
            break
        row = await queue.get()
        with transaction(conn):
            claim = conn.execute("UPDATE jobs SET status='running',attempts=attempts+1 "
                                 "WHERE id=? AND status='queued'", (row["id"],))
        if not claim.rowcount:
            continue
        try:
            result = renderer(conn, row["target_id"])
        except Exception as exc:
            conn.execute("UPDATE jobs SET status='failed',error=? WHERE id=?",
                         (str(exc)[:500], row["id"]))
            results.append({"id": row["id"], "error": str(exc)})
        else:
            conn.execute("UPDATE jobs SET status='done',error='' WHERE id=?", (row["id"],))
            results.append({"id": row["id"], "result": result})
        queue.task_done()
    return results
