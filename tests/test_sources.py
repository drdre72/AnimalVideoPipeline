import io
import json
import wave
from pathlib import Path

import pytest

from animal_pipeline import sources
from animal_pipeline.core import connect


def _sine(path: Path, seconds: float = 1.0) -> Path:
    import subprocess
    subprocess.run(["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
                    "-f", "lavfi", "-i", "color=red:size=64x64:duration=1",
                    "-shortest", str(path)], check=True, timeout=60)
    return path


def test_source_registry_and_errors():
    assert sources.source("noaa").license == sources.PD_USGOV
    with pytest.raises(ValueError, match="unknown source"):
        sources.source("mixkit")
    with pytest.raises(ValueError, match="no search API"):
        sources.search("noaa", "otter")


def test_pick_format_prefers_https_mp4_under_1080():
    info = {"formats": [
        {"url": "http://cdn.example.com/a.mp4", "ext": "mp4", "height": 720, "vcodec": "h264"},
        {"url": "https://cdn.example.com/a480.mp4", "ext": "mp4", "height": 480,
         "vcodec": "h264", "acodec": "aac"},
        {"url": "https://cdn.example.com/a1080.mp4", "ext": "mp4", "height": 1080,
         "vcodec": "h264", "acodec": "aac"},
        {"url": "https://cdn.example.com/master.m3u8", "ext": "mp4", "height": 1080,
         "vcodec": "h264"},
        {"url": "https://cdn.example.com/a4k.mp4", "ext": "mp4", "height": 2160,
         "vcodec": "h264", "acodec": "aac"},
    ]}
    assert sources._pick_format(info).endswith("a1080.mp4")


def test_list_category_stays_on_host(tmp_path, monkeypatch):
    body = (b'<a href="/detail/videos/b-roll:/video/111/a">x</a>'
            b'<a href="https://evil.example.com/video/222/b">y</a>'
            b'<a href="/detail/videos/b-roll:/video/333/c">z</a>')
    monkeypatch.setattr(sources.net, "bounded_open",
                        lambda *a, **k: io.BytesIO(body))
    links = sources.list_category(
        "noaa", "https://videos.fisheries.noaa.gov/category/videos/x")
    assert len(links) == 2
    assert all("videos.fisheries.noaa.gov" in l["url"] for l in links)


def test_gallery_import_records_pd_rights(tmp_path, monkeypatch):
    clip = _sine(tmp_path / "clip.mp4")
    monkeypatch.setattr(sources, "_inspect_gallery",
                        lambda src, url: {"title": "Turtle B-Roll", "url": "https://cdn/x.mp4",
                                          "page": url, "duration": 10.0,
                                          "creator": "NOAA Fisheries"})
    monkeypatch.setattr(sources, "_download", lambda url, dest, **k: clip)
    conn = connect(tmp_path / "t.db")
    aid = sources.import_video(
        conn, "noaa",
        "https://videos.fisheries.noaa.gov/detail/videos/x/video/1/a",
        "Green sea turtle", tmp_path / "cache")
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    assert asset["license_type"] == sources.PD_USGOV
    assert asset["commercial"] == 1 and asset["modifications"] == 1
    assert "NOAA" in asset["attribution"] and "third-party" in asset["restrictions"]
    evidence = json.loads(asset["evidence"])
    assert evidence["license"].startswith("https://")


def test_pixabay_import_uses_api_key(tmp_path, monkeypatch):
    clip = _sine(tmp_path / "clip.mp4")
    monkeypatch.setattr(sources, "_api_keys", lambda: {"pixabay": "k"})
    payload = json.dumps({"hits": [{"id": 7, "pageURL": "https://pixabay.com/v/7",
                                    "user": "maker", "tags": "otter",
                                    "videos": {"medium": {"url": "https://cdn.pixabay.com/v.mp4"}}}]})
    monkeypatch.setattr(sources.net, "bounded_open", lambda *a, **k: io.BytesIO(payload.encode()))
    monkeypatch.setattr(sources, "_download", lambda url, dest, **k: clip)
    conn = connect(tmp_path / "t.db")
    aid = sources._api_import(conn, sources.source("pixabay"), "7", "Otter", tmp_path / "c")
    asset = conn.execute("SELECT * FROM assets WHERE id=?", (aid,)).fetchone()
    assert asset["license_type"] == "Pixabay Content License"
    assert asset["creator"] == "maker" and "Pixabay" in asset["attribution"]


def test_api_import_requires_key(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, "_api_keys", lambda: {})
    conn = connect(tmp_path / "t.db")
    with pytest.raises(ValueError, match="missing pexels key"):
        sources._api_import(conn, sources.source("pexels"), "1", "Bird", tmp_path / "c")
