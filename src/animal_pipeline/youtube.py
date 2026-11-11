"""YouTube Data API v3 and Analytics API v2 — authorized read-only ingestion.

The connector imports channel uploads, per-video statistics and Analytics
metrics into timestamped, idempotent SQLite snapshots, and pulls public
metadata for configured benchmark channels. Everything runs through the
bounded HTTPS boundary with an explicit read-only OAuth identity.

The upload adapter is deliberately dormant: the approval-bound path exists
behind `youtube.upload_enabled` plus a per-call operator acknowledgement,
and still refuses with "not activated" until a separate phase enables it.
"""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

from . import google_auth, net
from .core import content, new_id, now, settings

DATA_API = "https://www.googleapis.com/youtube/v3"
ANALYTICS_API = "https://youtubeanalytics.googleapis.com/v2/reports"
HOSTS = google_auth.GOOGLE_HOSTS
PAGE_SIZE = 50
MAX_PAGES = 20
ISO_DURATION = re.compile(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+(?:\.\d+)?)S)?")
UNSUPPORTED_FIELDS = ("impressions", "click_through_rate", "unique_viewers",
                      "revenue", "viewer_demographics")


class OAuthExpired(RuntimeError):
    pass


class QuotaExhausted(RuntimeError):
    pass


def _seconds(iso8601: str) -> float:
    match = ISO_DURATION.fullmatch(iso8601 or "")
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return (int(hours or 0) * 3600 + int(minutes or 0) * 60) * 1.0 + float(seconds or 0)


def _request(url: str, token: str, *, request=net.bounded_request) -> dict:
    cfg = settings().get("youtube", {})
    attempts = int(cfg.get("max_retries", 3))
    backoff = float(cfg.get("retry_backoff_s", 2))
    for attempt in range(attempts):
        try:
            with request(url, HOSTS, name="YouTube API",
                         headers={"Authorization": f"Bearer {token}"}) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                raise OAuthExpired("OAuth access expired; run `youtube auth`") from None
            body = exc.read()[:2000].decode("utf-8", "replace")
            if exc.code == 403 and "quota" in body.lower():
                raise QuotaExhausted("YouTube API quota exhausted; retry tomorrow") from None
            if exc.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise RuntimeError(f"YouTube API error HTTP {exc.code}") from None
        except (OSError, urllib.error.URLError):
            if attempt < attempts - 1:
                time.sleep(backoff * (attempt + 1))
                continue
            raise RuntimeError("YouTube API unreachable after bounded retries") from None
    raise RuntimeError("YouTube API unreachable")


def _api(path: str, params: dict, token: str, *, request=net.bounded_request) -> dict:
    return _request(DATA_API + path + "?" + urllib.parse.urlencode(params), token,
                    request=request)


def _analytics(params: dict, token: str, *, request=net.bounded_request) -> dict:
    return _request(ANALYTICS_API + "?" + urllib.parse.urlencode(params), token,
                    request=request)


def _videos(ids: list[str], token: str, *, request) -> list[dict]:
    items = []
    for i in range(0, len(ids), PAGE_SIZE):
        items += _api("/videos", {"part": "snippet,contentDetails,statistics",
                                  "id": ",".join(ids[i:i + PAGE_SIZE])},
                      token, request=request).get("items", [])
    return items


def _uploads_playlist(channel_id: str, token: str, *, request) -> str:
    data = _api("/channels", {"part": "contentDetails", "id": channel_id},
                token, request=request)
    items = data.get("items", [])
    if not items:
        raise RuntimeError("channel not found")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def _playlist_items(playlist: str, token: str, *, request, limit: int = PAGE_SIZE) -> list[dict]:
    items, page = [], ""
    for _ in range(MAX_PAGES):
        params = {"part": "contentDetails", "playlistId": playlist,
                  "maxResults": min(PAGE_SIZE, limit - len(items))}
        if page:
            params["pageToken"] = page
        data = _api("/playlistItems", params, token, request=request)
        items += data.get("items", [])
        page = data.get("nextPageToken", "")
        if not page or len(items) >= limit:
            break
    return items[:limit]


def _snapshot_day() -> str:
    return now()[:10]


def sync_uploads(conn: sqlite3.Connection, *, token: str | None = None,
                 request=net.bounded_request, limit: int = 200) -> dict:
    """Import the authorized channel's uploads + public statistics."""
    token = token or google_auth.access_token()
    channel = google_auth.verify_identity()
    playlist = _uploads_playlist(channel["id"], token, request=request)
    entries = _playlist_items(playlist, token, request=request, limit=limit)
    ids = [e["contentDetails"]["videoId"] for e in entries]
    imported, snapshots = 0, 0
    for video in _videos(ids, token, request=request):
        video_id = video["id"]
        cursor = conn.execute(
            "INSERT OR IGNORE INTO youtube_videos(id,video_id,plan_id,title,channel_id,published_at,duration_s,first_seen_at) VALUES(?,?,?,?,?,?,?,?)",
            (new_id("yt"), video_id, None, video["snippet"]["title"],
             channel["id"], video["snippet"]["publishedAt"],
             _seconds(video["contentDetails"]["duration"]), now()))
        imported += cursor.rowcount
        stats = video.get("statistics", {})
        conn.execute("INSERT OR IGNORE INTO analytics(id,video_id,platform,observed_at,published_at,views,likes,watch_seconds,completion,source,comments,subscribers,shares) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (new_id("snapshot"), video_id, "youtube", _snapshot_day(),
                      video["snippet"]["publishedAt"], int(stats.get("viewCount", 0)),
                      stats.get("likeCount"), None, None, "youtube_data_api",
                      stats.get("commentCount"), None, None))
        snapshots += 1
    return {"channel": channel["id"], "videos_seen": len(ids),
            "videos_imported": imported, "snapshots": snapshots,
            "unsupported_fields": list(UNSUPPORTED_FIELDS)}


def sync_analytics(conn: sqlite3.Connection, *, token: str | None = None,
                   request=net.bounded_request, limit: int = 50) -> dict:
    """Pull Analytics API metrics for videos the desk knows about."""
    token = token or google_auth.access_token()
    channel = google_auth.verify_identity()
    rows = conn.execute(
        "SELECT video_id,published_at,duration_s FROM youtube_videos "
        "ORDER BY published_at DESC LIMIT ?", (limit,)).fetchall()
    if not rows:
        return {"channel": channel["id"], "snapshots": 0,
                "note": "run `youtube sync` first", "unsupported_fields": list(UNSUPPORTED_FIELDS)}
    end = _snapshot_day()
    durations = {row["video_id"]: row["duration_s"] for row in rows}
    published = {row["video_id"]: row["published_at"] for row in rows}
    metrics = "views,estimatedMinutesWatched,averageViewDuration,likes,subscribersGained,shares"
    data = _analytics({"ids": f"channel=={channel['id']}", "startDate": "2005-01-01",
                       "endDate": end, "metrics": metrics, "dimensions": "video",
                       "maxResults": "200", "sort": "-views"}, token, request=request)
    headers = [column["name"] for column in data.get("columnHeaders", [])]
    snapshots = 0
    for record in data.get("rows", []):
        row = dict(zip(headers, record))
        video_id = row.get("video")
        if not video_id:
            continue
        avg, duration = row.get("averageViewDuration"), durations.get(video_id)
        completion = (avg / duration) if avg and duration else None
        conn.execute("INSERT OR IGNORE INTO analytics(id,video_id,platform,observed_at,published_at,views,likes,watch_seconds,completion,source,comments,subscribers,shares) "
                     "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (new_id("snapshot"), video_id, "youtube", end,
                      published.get(video_id, "1970-01-01T00:00:00Z"),
                      int(row.get("views", 0)), row.get("likes"),
                      (row.get("estimatedMinutesWatched") or 0) * 60,
                      completion, "youtube_analytics_api", None,
                      row.get("subscribersGained"), row.get("shares")))
        snapshots += 1
    return {"channel": channel["id"], "snapshots": snapshots,
            "unsupported_fields": list(UNSUPPORTED_FIELDS)}


def sync_competitors(conn: sqlite3.Connection, *, token: str | None = None,
                     request=net.bounded_request, per_channel: int = 25) -> dict:
    """Public metadata for configured benchmark channels (Snugbarn & co)."""
    token = token or google_auth.access_token()
    results = {}
    for bench in content().get("benchmark_channels", []):
        handle = bench.get("handle", "").lstrip("@")
        if bench.get("platform") != "youtube" or not handle:
            continue
        data = _api("/channels", {"part": "contentDetails,snippet", "forHandle": handle},
                    token, request=request)
        items = data.get("items", [])
        if not items:
            results[handle] = {"error": "channel not found"}
            continue
        channel = items[0]
        playlist = channel["contentDetails"]["relatedPlaylists"]["uploads"]
        entries = _playlist_items(playlist, token, request=request, limit=per_channel)
        ids = [e["contentDetails"]["videoId"] for e in entries]
        imported = 0
        for video in _videos(ids, token, request=request):
            stats = video.get("statistics", {})
            cursor = conn.execute(
                "INSERT OR IGNORE INTO competitors(id,channel,video_id,title,category,uploaded_at,observed_at,duration_s,views,likes,subscribers,source) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                (new_id("benchmark"), channel["snippet"]["title"], video["id"],
                 video["snippet"]["title"],
                 video["snippet"].get("categoryId", ""),
                 video["snippet"]["publishedAt"], _snapshot_day(),
                 _seconds(video["contentDetails"]["duration"]),
                 int(stats.get("viewCount", 0)), stats.get("likeCount"),
                 stats.get("subscriberCount"), "youtube_data_api"))
            imported += cursor.rowcount
        results[handle] = {"channel": channel["snippet"]["title"],
                           "videos_seen": len(ids), "imported": imported}
    return {"channels": results, "note": "public metadata only; no private "
              "retention, revenue or demographic data is available or claimed"}


def link_video(conn: sqlite3.Connection, plan_id: str, video_id: str) -> dict:
    """Bind a manually uploaded YouTube video to its approved plan revision."""
    plan = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not plan or plan["status"] not in ("APPROVED", "EXPORTED"):
        raise ValueError("only an approved or exported revision can be linked")
    video = conn.execute("SELECT * FROM youtube_videos WHERE video_id=?",
                         (video_id,)).fetchone()
    if not video:
        raise ValueError("unknown YouTube video; run `youtube sync` first")
    if video["plan_id"] and video["plan_id"] != plan_id:
        raise ValueError("video already linked to another plan")
    conn.execute("UPDATE youtube_videos SET plan_id=? WHERE video_id=?", (plan_id, video_id))
    return {"linked": video_id, "plan_id": plan_id}


def prepare_upload(conn: sqlite3.Connection, plan_id: str) -> dict:
    """Metadata the dormant adapter would send — no network, no upload."""
    row = conn.execute("SELECT * FROM plans WHERE id=?", (plan_id,)).fetchone()
    if not row or row["status"] not in ("APPROVED", "EXPORTED") or not row["approved_hash"]:
        raise ValueError("exact revision must be approved first")
    if row["approved_hash"] != row["video_hash"]:
        raise ValueError("approval no longer matches the rendered video")
    linked = conn.execute("SELECT video_id FROM youtube_videos WHERE plan_id=?",
                          (plan_id,)).fetchone()
    if linked:
        raise ValueError(f"revision already uploaded as {linked['video_id']}")
    plan = json.loads(row["content"])
    return {"plan_id": plan_id, "revision": row["revision"], "sha256": row["video_hash"],
            "video": row["video_path"],
            "metadata": {"snippet": {"title": plan["title"][:100],
                                     "description": (plan.get("description", "")
                                                     + "\n#Shorts")[:5000],
                                     "categoryId": "15"},
                         "status": {"privacyStatus": "private",
                                    "selfDeclaredMadeForKids": False}},
            "note": "dry-run only; upload transport is not activated"}


def upload(conn: sqlite3.Connection, plan_id: str, *, operator_ack: bool = False,
           dry_run: bool = True, request=net.bounded_request) -> dict:
    """Future-ready upload entry point — deliberately not activated."""
    package = prepare_upload(conn, plan_id)
    if dry_run:
        return {"dry_run": True, **package}
    if not settings().get("youtube", {}).get("upload_enabled") or not operator_ack:
        raise RuntimeError("uploads are disabled; export packages for manual upload")
    google_auth.verify_identity()
    raise RuntimeError("live upload transport is not activated in this phase")
