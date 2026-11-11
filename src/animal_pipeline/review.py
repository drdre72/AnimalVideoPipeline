from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from pathlib import Path

from .core import ROOT, digest, settings
from .evidence import validate_segments
from .media import probe, rights_errors

PLATFORMS = ("youtube", "tiktok", "instagram")
REQUIRED_ATTESTATIONS = ("facts", "visuals", "rights", "audio")


def review_errors(conn: sqlite3.Connection, row: sqlite3.Row) -> list[str]:
    errors = []
    plan = json.loads(row["content"])
    errors.extend(validate_segments(conn, row["topic_id"], plan["segments"]))
    if row["status"] not in ("READY_FOR_REVIEW", "APPROVED", "EXPORTED"):
        errors.append("revision not ready for review")
    if not row["video_path"] or not Path(row["video_path"]).is_file():
        errors.append("missing video")
    elif digest(Path(row["video_path"])) != row["video_hash"]:
        errors.append("video hash changed")
    else:
        try:
            video = probe(Path(row["video_path"]))
            cfg = settings()["render"]
            min_duration = (cfg["exception_min_duration_s"] if plan.get("runtime_exception")
                            else cfg["min_duration_s"])
            if video["width"] != cfg["width"] or video["height"] != cfg["height"] or not (
                    min_duration <= video["duration"] <= cfg["max_duration_s"] + 0.2):
                errors.append("video no longer matches approved output profile")
        except ValueError:
            errors.append("video is corrupt")
    if not row["qc"] or not json.loads(row["qc"]).get("ok"):
        errors.append("mandatory QC did not pass")
    else:
        rendered_voice = json.loads(row["qc"]).get("voice") or {}
        current = settings()["voice"]
        if any(rendered_voice.get(key) != current.get(key)
               for key in ("backend", "name", "model")):
            errors.append("approved voice configuration changed")
    for shot in plan["segments"]:
        asset = conn.execute("SELECT * FROM assets WHERE id=?", (shot["asset_id"],)).fetchone()
        if not asset:
            errors.append("missing footage")
        else:
            errors.extend(rights_errors(asset))
    other = conn.execute("SELECT MAX(revision) FROM plans WHERE topic_id=?", (row["topic_id"],)).fetchone()[0]
    if row["revision"] != other:
        errors.append("newer revision exists")
    return sorted(set(errors))


def approve(conn: sqlite3.Connection, plan_id: str, expected_hash: str,
            *, verified: tuple[str, ...] = ()) -> None:
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not row or row["video_hash"] != expected_hash:
        raise ValueError("exact rendered video SHA-256 required")
    errors = review_errors(conn, row)
    if errors:
        raise ValueError("; ".join(errors))
    if set(verified) != set(REQUIRED_ATTESTATIONS):
        raise ValueError("operator must explicitly verify facts, visuals, rights and audio")
    conn.execute("UPDATE plans SET status='APPROVED',approved_hash=? WHERE id=?",
                 (expected_hash, plan_id))


def export(conn: sqlite3.Connection, plan_id: str, platform: str, *,
           dry_run: bool = False) -> dict:
    if platform not in PLATFORMS:
        raise ValueError("unknown platform")
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not row or row["status"] not in ("APPROVED", "EXPORTED") or not row["approved_hash"]:
        raise ValueError("exact revision must be approved before local export")
    errors = review_errors(conn, row)
    if row["approved_hash"] != row["video_hash"]:
        errors.append("approval hash mismatch")
    if errors:
        raise ValueError("; ".join(errors))
    plan = json.loads(row["content"])
    assets = [conn.execute("SELECT * FROM assets WHERE id=?", (s["asset_id"],)).fetchone()
              for s in plan["segments"]]
    package = {"brand": "Naminal Hub", "platform": platform, "plan_id": plan_id,
               "revision": row["revision"], "sha256": row["video_hash"],
               "title": plan["title"], "description": plan.get("description", ""),
               "hashtags": plan.get("hashtags", []),
               "thumbnail": "thumbnail.jpg",
               "attribution": sorted({a["attribution"] for a in assets if a["attribution"]}),
               "disclosure_checklist": ["Confirm species, rights and facts",
                                        "Review synthetic-media disclosures",
                                        "Operator uploads manually"]}
    if dry_run:
        return package
    dest = ROOT / "output" / "exports" / plan_id / platform
    dest.mkdir(parents=True, exist_ok=True)
    video_tmp = dest / "Naminal_Hub.part.mp4"
    image_tmp = dest / "thumbnail.part.jpg"
    json_tmp = dest / "package.part.json"
    try:
        shutil.copyfile(row["video_path"], video_tmp)
        if digest(video_tmp) != row["video_hash"]:
            raise RuntimeError("export copy hash mismatch")
        thumbnail_time = min(3, probe(video_tmp)["duration"] / 2)
        result = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-ss", str(thumbnail_time), "-i", str(video_tmp),
             "-frames:v", "1", str(image_tmp)],
            capture_output=True, text=True, timeout=120)
        if result.returncode or not image_tmp.is_file():
            raise RuntimeError("could not create export thumbnail")
        json_tmp.write_text(json.dumps(package, indent=2))
        os.replace(video_tmp, dest / "Naminal_Hub.mp4")
        os.replace(image_tmp, dest / "thumbnail.jpg")
        os.replace(json_tmp, dest / "package.json")
    finally:
        for temporary in (video_tmp, image_tmp, json_tmp):
            temporary.unlink(missing_ok=True)
    conn.execute("UPDATE plans SET status='EXPORTED' WHERE id=?", (plan_id,))
    return {"directory": str(dest), **package}
