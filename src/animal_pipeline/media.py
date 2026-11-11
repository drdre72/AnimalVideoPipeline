from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image

from .core import digest, new_id, now


def probe(path: Path) -> dict:
    if not path.is_file():
        raise ValueError(f"media not found: {path}")
    result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                             "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise ValueError("invalid or corrupt source footage")
    info = json.loads(result.stdout)
    stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not stream or not float(info["format"].get("duration", 0)):
        raise ValueError("source has no playable video")
    return {"duration": float(info["format"]["duration"]),
            "width": stream["width"], "height": stream["height"],
            "codec": stream["codec_name"]}


def import_asset(conn: sqlite3.Connection, path: Path, *, source_ref: str, creator: str,
                 license_type: str, evidence: str, attribution: str, species: str,
                 commercial: bool, modifications: bool, behavior: str = "",
                 restrictions: str = "", expires_at: str | None = None,
                 test_only: bool = False) -> str:
    if not all((source_ref.strip(), creator.strip(), license_type.strip(), evidence.strip(), species.strip())):
        raise ValueError("missing item-specific rights or species evidence")
    info = probe(path)
    asset_id = new_id("asset")
    conn.execute("INSERT INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                 (asset_id, str(path.resolve()), digest(path), source_ref, creator, license_type,
                  evidence, attribution, int(commercial), int(modifications), restrictions,
                  expires_at, 0, int(test_only), species, behavior, info["duration"],
                  info["width"], info["height"], now()))
    return asset_id


def rights_errors(asset: sqlite3.Row, *, publishable: bool = True) -> list[str]:
    errors = []
    if not asset["approved"]:
        errors.append("asset rights not approved")
    if not asset["commercial"] or not asset["modifications"]:
        errors.append("commercial and modification rights required")
    if not asset["evidence"] or not asset["source_ref"]:
        errors.append("missing rights evidence")
    if ("by" in asset["license_type"].lower().split("-") or
            "attribution" in asset["license_type"].lower()) and not asset["attribution"]:
        errors.append("license requires attribution")
    if asset["expires_at"]:
        try:
            expiry = datetime.fromisoformat(asset["expires_at"].replace("Z", "+00:00"))
            if expiry.tzinfo is None or expiry <= datetime.now(timezone.utc):
                errors.append("expired or ambiguous rights expiry")
        except ValueError:
            errors.append("invalid rights expiry")
    if asset["restrictions"]:
        errors.append("unresolved platform restrictions")
    if publishable and asset["test_only"]:
        errors.append("test footage cannot be published")
    path = Path(asset["path"])
    if not path.is_file() or digest(path) != asset["sha256"]:
        errors.append("asset missing or hash changed")
    return errors


def approve_asset(conn: sqlite3.Connection, asset_id: str) -> None:
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not asset:
        raise ValueError("unknown asset")
    errors = rights_errors(asset, publishable=False)
    errors = [error for error in errors if error != "asset rights not approved"]
    if errors:
        raise ValueError("; ".join(errors))
    conn.execute("UPDATE assets SET approved=1 WHERE id=?", (asset_id,))


def inspect_asset(conn: sqlite3.Connection, asset_id: str, cache: Path) -> dict:
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (asset_id,)).fetchone()
    if not asset:
        raise ValueError("unknown asset")
    if not Path(asset["path"]).is_file() or digest(Path(asset["path"])) != asset["sha256"]:
        raise ValueError("asset changed; cannot inspect")
    cache.mkdir(parents=True, exist_ok=True)
    sheet = cache / f"{asset_id}.jpg"
    frame = cache / f"{asset_id}_vertical.jpg"
    duration = min(asset["duration"], 45)
    contact = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-i", asset["path"], "-t", str(duration),
         "-vf", "fps=1/5,scale=180:-1,tile=3x3", "-frames:v", "1", str(sheet)],
        capture_output=True, text=True, timeout=120)
    thumb = subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
         "-ss", str(min(1, duration / 2)), "-i", asset["path"],
         "-vf", "scale=270:480:force_original_aspect_ratio=increase,crop=270:480",
         "-frames:v", "1", str(frame)], capture_output=True, text=True, timeout=60)
    if contact.returncode or thumb.returncode:
        raise ValueError("could not inspect footage")
    with Image.open(frame) as image:
        pixels = image.convert("L").resize((8, 8)).tobytes()
    mean = sum(pixels) / len(pixels)
    fingerprint = "".join("1" if x >= mean else "0" for x in pixels)
    shots = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", asset["path"], "-t", str(duration),
         "-vf", "select=gt(scene\\,0.3),showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True, timeout=120)
    boundaries = [float(x) for x in re.findall(r"pts_time:([\d.]+)", shots.stderr)] if (
        shots.returncode == 0) else []
    duplicates = [a["id"] for a in conn.execute(
        "SELECT id FROM assets WHERE sha256=? AND id!=?", (asset["sha256"], asset_id))]
    return {"contact_sheet": str(sheet), "vertical_preview": str(frame),
            "visual_fingerprint": fingerprint, "scene_boundaries_s": boundaries,
            "exact_duplicates": duplicates, "species_tag": asset["species"],
            "species_tag_confidence": "operator supplied; not machine verified"}
