import io
import json
import os
import urllib.error
import urllib.parse

import pytest

from animal_pipeline import evidence, google_auth, media, youtube
from animal_pipeline.core import connect


class Response(io.BytesIO):
    def __init__(self, content, url="https://www.googleapis.com/x"):
        super().__init__(content if isinstance(content, bytes) else json.dumps(content).encode())
        self.url = url

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(code, reason=""):
    body = json.dumps({"error": {"errors": [{"reason": reason}]}}).encode()
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body))


@pytest.fixture
def conn(tmp_path):
    db = connect(tmp_path / "yt.db")
    yield db
    db.close()


@pytest.fixture
def store_path(tmp_path, monkeypatch):
    path = tmp_path / "secrets" / "google.json"
    monkeypatch.setattr(google_auth, "secrets_path", lambda: path)
    return path


def test_migrate_imports_only_allowlisted_keys(tmp_path, store_path):
    env = tmp_path / ".env"
    env.write_text("NEWSROOM_YT_CLIENT_ID=cid\nNEWSROOM_YT_CLIENT_SECRET=csec\n"
                   "NEWSROOM_YT_REFRESH_TOKEN=rt\nNEWSROOM_RTMP_URL=rtmp://x\n"
                   "EIA_API_KEY=other\nNEWSROOM_GMAIL_REFRESH_TOKEN=grt\n")
    result = google_auth.migrate(env=env)
    store = json.loads(store_path.read_text())
    assert store["client_id"] == "cid" and store["client_secret"] == "csec"
    assert "refresh_token" not in store and "rtmp" not in json.dumps(store)
    assert os.stat(store_path).st_mode & 0o777 == 0o600
    assert "csec" not in json.dumps(result) and "rt" not in json.dumps(result["imported_keys"])


def test_migrate_client_json_filters_fields(tmp_path, store_path):
    path = tmp_path / "client_secret.json"
    path.write_text(json.dumps({"installed": {
        "client_id": "c2", "client_secret": "s2", "project_id": "proj",
        "auth_uri": "https://accounts.google.com/o/oauth2/auth",
        "redirect_uris": ["http://localhost"], "token_uri": "https://oauth2.googleapis.com/token"}}))
    google_auth.migrate(client_json=path)
    store = json.loads(store_path.read_text())
    assert store["client_id"] == "c2" and store["project_id"] == "proj"
    assert "redirect_uris" not in store and "auth_uri" not in store


def test_migrate_requires_a_source(store_path):
    with pytest.raises(ValueError, match="requires"):
        google_auth.migrate()


def test_load_store_rejects_insecure_permissions(tmp_path, store_path):
    store_path.parent.mkdir(parents=True)
    store_path.write_text("{}")
    os.chmod(store_path, 0o644)
    with pytest.raises(RuntimeError, match="permissions"):
        google_auth.load_store()


def test_access_token_requires_authorization(store_path):
    with pytest.raises(RuntimeError, match="youtube auth"):
        google_auth.access_token()


def test_access_token_refresh(store_path, monkeypatch):
    store_path.parent.mkdir(parents=True)
    store_path.write_text(json.dumps(
        {"client_id": "c", "client_secret": "s", "refresh_token": "r"}))
    os.chmod(store_path, 0o600)
    monkeypatch.setattr(google_auth, "_post",
                        lambda url, fields, **kw: Response({"access_token": "fresh"}))
    assert google_auth.access_token() == "fresh"


def test_verify_identity_aborts_on_channel_mismatch(store_path, monkeypatch):
    store_path.parent.mkdir(parents=True)
    store_path.write_text(json.dumps(
        {"client_id": "c", "client_secret": "s", "refresh_token": "r",
         "channel_id": "CH_NAMINAL"}))
    os.chmod(store_path, 0o600)
    monkeypatch.setattr(google_auth, "access_token", lambda: "t")
    monkeypatch.setattr(google_auth, "channel_for_token",
                        lambda token: {"id": "CH_OTHER", "title": "Wrong"})
    with pytest.raises(RuntimeError, match="not the configured|channel changed"):
        google_auth.verify_identity()


def _youtube_router():
    def request(url, hosts, *, name="YouTube API", data=None, headers=None,
                timeout=30, method=None):
        if "/channels" in url:
            return Response({"items": [{"id": "CH1", "snippet": {"title": "Naminal Hub"},
                                        "contentDetails": {"relatedPlaylists": {"uploads": "UU1"}}}]})
        if "/playlistItems" in url:
            return Response({"items": [{"contentDetails": {"videoId": "VID1"}},
                                       {"contentDetails": {"videoId": "VID2"}}]})
        if "/videos" in url:
            return Response({"items": [
                {"id": "VID1", "snippet": {"title": "Octopus", "publishedAt": "2026-01-01T00:00:00Z"},
                 "contentDetails": {"duration": "PT1M2S"},
                 "statistics": {"viewCount": "100", "likeCount": "9", "commentCount": "3"}},
                {"id": "VID2", "snippet": {"title": "Cuttlefish", "publishedAt": "2026-01-02T00:00:00Z"},
                 "contentDetails": {"duration": "PT22S"},
                 "statistics": {"viewCount": "50", "likeCount": "4"}}]})
        raise AssertionError(f"unexpected url {url}")
    return request


def test_sync_uploads_imports_videos_and_snapshots_idempotently(conn, monkeypatch):
    monkeypatch.setattr(google_auth, "verify_identity",
                        lambda: {"id": "CH1", "title": "Naminal Hub"})
    result = youtube.sync_uploads(conn, token="t", request=_youtube_router())
    assert result["videos_imported"] == 2 and result["snapshots"] == 2
    row = conn.execute("SELECT video_id,duration_s FROM youtube_videos WHERE video_id='VID1'").fetchone()
    assert row["duration_s"] == pytest.approx(62)
    assert conn.execute("SELECT views,comments FROM analytics WHERE video_id='VID1'").fetchone()[:] == (100, 3)
    again = youtube.sync_uploads(conn, token="t", request=_youtube_router())
    assert again["videos_imported"] == 0
    assert "impressions" in result["unsupported_fields"]


def test_sync_analytics_records_metrics_and_unsupported(conn, monkeypatch):
    conn.execute("INSERT INTO youtube_videos VALUES('a','VID1',NULL,'Octopus','CH1',"
                 "'2026-01-01T00:00:00Z',60,'x')")
    monkeypatch.setattr(google_auth, "verify_identity",
                        lambda: {"id": "CH1", "title": "Naminal Hub"})
    def request(url, hosts, **kw):
        return Response({"columnHeaders": [{"name": n} for n in
                         ("video", "views", "estimatedMinutesWatched",
                          "averageViewDuration", "likes", "subscribersGained", "shares")],
                         "rows": [["VID1", 500, 400, 48.0, 30, 12, 5]]})
    result = youtube.sync_analytics(conn, token="t", request=request)
    row = conn.execute("SELECT views,watch_seconds,completion,subscribers,shares,source "
                       "FROM analytics WHERE video_id='VID1' AND source='youtube_analytics_api'").fetchone()
    assert row[:] == (500, 24000.0, pytest.approx(0.8), 12, 5, "youtube_analytics_api")
    assert "impressions" in result["unsupported_fields"]


def test_quota_and_network_failures_are_bounded(conn, monkeypatch):
    monkeypatch.setattr(google_auth, "verify_identity",
                        lambda: {"id": "CH1", "title": "Naminal Hub"})
    calls = {"n": 0}
    def quota(url, hosts, **kw):
        calls["n"] += 1
        raise http_error(403, "quotaExceeded")
    with pytest.raises(youtube.QuotaExhausted):
        youtube.sync_uploads(conn, token="t", request=quota)
    assert calls["n"] == 1  # quota is not retried

    calls["n"] = 0
    def expired(url, hosts, **kw):
        calls["n"] += 1
        raise http_error(401)
    with pytest.raises(youtube.OAuthExpired):
        youtube.sync_uploads(conn, token="t", request=expired)
    assert calls["n"] == 1  # expired OAuth is not retried

    calls["n"] = 0
    def down(url, hosts, **kw):
        calls["n"] += 1
        raise urllib.error.URLError("offline")
    with pytest.raises(RuntimeError, match="unreachable"):
        youtube.sync_uploads(conn, token="t", request=down)
    assert calls["n"] == 3  # bounded retries only


def _approved_plan(conn, footage):
    topic = evidence.add_topic(conn, "Animal", "Species", "P")
    fact = evidence.add_fact(conn, topic, "It exists.", "Org", "local:s", "It exists.", "x")
    evidence.approve_fact(conn, fact)
    asset = media.import_asset(conn, footage, source_ref="local", creator="T",
                               license_type="original", evidence="fixture", attribution="T",
                               species="Animal", commercial=True, modifications=True)
    media.approve_asset(conn, asset)
    from animal_pipeline import creative
    plan_id = creative.save_plan(conn, {"topic_id": topic, "title": "T",
        "hooks": ["a", "b", "c"], "description": "desc",
        "segments": [{"text": "It exists.", "fact_ids": [fact], "asset_id": asset,
                      "start_s": 0, "duration_s": 2}]})
    conn.execute("UPDATE plans SET status='APPROVED',video_hash='h',approved_hash='h',"
                 "video_path='/tmp/x.mp4' WHERE id=?", (plan_id,))
    return plan_id


@pytest.fixture
def footage(tmp_path):
    import subprocess
    path = tmp_path / "f.mp4"
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", "testsrc2=s=320x240:r=30:d=3",
                    "-c:v", "h264_videotoolbox", "-b:v", "1M", str(path)],
                   check=True, timeout=60)
    return path


def test_upload_adapter_is_dormant_and_duplicate_safe(conn, footage):
    plan_id = _approved_plan(conn, footage)
    prepared = youtube.upload(conn, plan_id, dry_run=True)
    assert prepared["dry_run"] and prepared["metadata"]["status"]["privacyStatus"] == "private"
    with pytest.raises(RuntimeError, match="disabled"):
        youtube.upload(conn, plan_id, operator_ack=True, dry_run=False)
    conn.execute("INSERT INTO youtube_videos VALUES('a','VIDX',?,'T','CH1','x',60,'x')",
                 (plan_id,))
    with pytest.raises(ValueError, match="already uploaded"):
        youtube.upload(conn, plan_id, dry_run=True)


def test_link_video_binds_only_approved_revisions(conn, footage):
    plan_id = _approved_plan(conn, footage)
    conn.execute("INSERT INTO youtube_videos VALUES('a','VID1',NULL,'T','CH1','x',60,'x')")
    assert youtube.link_video(conn, plan_id, "VID1")["linked"] == "VID1"
    assert youtube.link_video(conn, plan_id, "VID1")["linked"] == "VID1"  # idempotent
    other = _approved_plan(conn, footage)
    with pytest.raises(ValueError, match="another plan"):
        youtube.link_video(conn, other, "VID1")
    with pytest.raises(ValueError, match="unknown YouTube video"):
        youtube.link_video(conn, plan_id, "VID_MISSING")


def test_competitor_sync_imports_public_metadata(conn, monkeypatch):
    monkeypatch.setattr(google_auth, "verify_identity",
                        lambda: {"id": "CH1", "title": "Naminal Hub"})
    def request(url, hosts, **kw):
        if "forHandle" in urllib.parse.unquote(url):
            return Response({"items": [{"id": "CHS", "snippet": {"title": "Snugbarn"},
                                       "contentDetails": {"relatedPlaylists": {"uploads": "UU9"}}}]})
        if "/playlistItems" in url:
            return Response({"items": [{"contentDetails": {"videoId": "SV1"}}]})
        if "/videos" in url:
            return Response({"items": [{"id": "SV1", "snippet": {
                "title": "Otter", "publishedAt": "2026-01-01T00:00:00Z", "categoryId": "15"},
                "contentDetails": {"duration": "PT40S"},
                "statistics": {"viewCount": "9000", "likeCount": "500"}}]})
        raise AssertionError(url)
    result = youtube.sync_competitors(conn, token="t", request=request)
    row = conn.execute("SELECT channel,views,duration_s FROM competitors WHERE video_id='SV1'").fetchone()
    assert row[:] == ("Snugbarn", 9000, 40.0)
    assert "Snugbarn" in result["channels"] and "no private" in result["note"]
