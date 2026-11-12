"""Multi-source licensed-footage adapters.

Wikimedia Commons remains the primary source (see commons.py). This module
adds adapters for US federal B-roll galleries (public domain works) and
stock-footage APIs (proprietary free licenses). Every import produces the
same asset + rights-evidence shape as commons.import_video so downstream
gates (species matching, budgets, review) behave identically.

Licensing notes baked into evidence:
- NOAA/USFWS/NPS: US federal works are public domain; agencies request
  credit. NOAA *narrative* videos may embed third-party licensed clips —
  only B-roll packages are imported, and the restriction note is recorded.
- Pixabay/Pexels/Coverr: stock content licenses allowing commercial use and
  modification without attribution; keys live in secrets/sources.json.
- Mixkit has no official API and is not automated.
"""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
import sys
import tempfile
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

from . import net
from .core import ROOT, digest, now
from .media import import_asset, probe

MAX_METADATA_BYTES = 2_000_000
MAX_MEDIA_BYTES = 500_000_000
SECRETS = ROOT / "secrets" / "sources.json"
YTDLP_TIMEOUT = 300
DOWNLOAD_TIMEOUT = 600

PD_USGOV = "Public Domain (US Gov work)"


@dataclass(frozen=True)
class Source:
    key: str
    name: str
    license: str
    license_url: str
    attribution: str
    hosts: frozenset[str]
    note: str = ""
    kind: str = "gallery"  # gallery (yt-dlp) | api
    api_key_name: str = ""


SOURCES: dict[str, Source] = {
    s.key: s for s in (
        Source(
            key="noaa",
            name="NOAA Fisheries",
            license=PD_USGOV,
            license_url="https://videos.fisheries.noaa.gov/category/videos/b-roll-by-topic",
            attribution="NOAA Fisheries",
            hosts=frozenset({"videos.fisheries.noaa.gov"}),
            note=("B-roll packages are public domain with credit requested. "
                  "Narrative NOAA videos may embed third-party licensed clips "
                  "noted in credits; only B-roll items are eligible."),
        ),
        Source(
            key="fws",
            name="US Fish & Wildlife Service",
            license=PD_USGOV,
            license_url="https://www.fws.gov/library/collections/b-roll-videos",
            attribution="USFWS",
            hosts=frozenset({"www.fws.gov"}),
            note=("B-roll shot by USFWS/NCTC videographers; free to use with "
                  "credit requested. Licensed-music caveat is moot: source "
                  "audio is never used. KNOWN GAP: fws.gov embeds VBrick "
                  "players (fws.rev.vbrick.com) which yt-dlp does not "
                  "support — NCTC pages list titles but source-import needs "
                  "a VBrick-capable extractor or manual download."),
        ),
        Source(
            key="nps",
            name="National Park Service",
            license=PD_USGOV,
            license_url="https://www.nps.gov/subjects/digital/nps-media-licensing.htm",
            attribution="National Park Service",
            hosts=frozenset({"npgallery.nps.gov"}),
            note="NPS-created works are public domain; third-party items are credited per page.",
        ),
        Source(
            key="pixabay",
            name="Pixabay",
            license="Pixabay Content License",
            license_url="https://pixabay.com/service/license-summary/",
            attribution="Pixabay",
            hosts=frozenset({"pixabay.com", "cdn.pixabay.com"}),
            kind="api",
            api_key_name="pixabay",
            note="Commercial use and modification allowed without attribution.",
        ),
        Source(
            key="pexels",
            name="Pexels",
            license="Pexels License",
            license_url="https://www.pexels.com/license/",
            attribution="Pexels",
            hosts=frozenset({"www.pexels.com", "api.pexels.com"}),
            kind="api",
            api_key_name="pexels",
            note="Commercial use and modification allowed without attribution.",
        ),
        Source(
            key="vecteezy",
            name="Vecteezy",
            license="Vecteezy License (free tier)",
            license_url="https://www.vecteezy.com/licensing-agreement",
            attribution="Vecteezy",
            hosts=frozenset({"api.vecteezy.com", "files.vecteezy.com"}),
            kind="api",
            api_key_name="vecteezy",
            note=("Free-tier downloads: attribution required (link to vecteezy.com), "
                  "500/month cap. Key stored as 'vecteezy_<account>' in sources.json."),
        ),
        Source(
            key="coverr",
            name="Coverr",
            license="Coverr License",
            license_url="https://coverr.co/license",
            attribution="Coverr",
            hosts=frozenset({"api.coverr.co", "coverr.co"}),
            kind="api",
            api_key_name="coverr",
            note="Commercial use and modification allowed without attribution.",
        ),
    )
}


def source(name: str) -> Source:
    src = SOURCES.get(name)
    if not src:
        raise ValueError(f"unknown source '{name}'; choose from {sorted(SOURCES)}")
    return src


def _api_keys() -> dict:
    if not SECRETS.is_file():
        raise ValueError("no API keys: create secrets/sources.json")
    return json.loads(SECRETS.read_text())


def _ytdlp_info(url: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "yt_dlp", "--dump-json", "--no-download",
         "--no-playlist", "--socket-timeout", "30", url],
        capture_output=True, text=True, timeout=YTDLP_TIMEOUT)
    if result.returncode:
        raise ValueError("yt-dlp could not read this source page")
    return json.loads(result.stdout)


def _pick_format(info: dict) -> str:
    formats = [f for f in info.get("formats", [])
               if f.get("vcodec") not in (None, "none") and f.get("url")
               and urllib.parse.urlsplit(f["url"]).scheme == "https"
               and not f["url"].endswith((".m3u8", ".mpd"))]
    if not formats and urllib.parse.urlsplit(info.get("url", "")).scheme == "https":
        return info["url"]
    # prefer the largest progressive mp4 within 1080p, else best available
    mp4s = [f for f in formats if f.get("ext") == "mp4"
            and (f.get("height") or 0) <= 1080 and f.get("acodec") != "none"]
    pool = mp4s or [f for f in formats if f.get("acodec") != "none"] or formats
    best = max(pool, key=lambda f: (f.get("height") or 0, f.get("tbr") or 0))
    return best["url"]


def list_category(src_key: str, page_url: str, limit: int = 25) -> list[dict]:
    """Enumerate video links on a source's category/search page (gov galleries)."""
    src = source(src_key)
    if src.kind != "gallery":
        raise ValueError(f"{src.key} uses the API; call search() instead")
    net.check_url(page_url, set(src.hosts), f"{src.name} page URL")
    with net.bounded_open(page_url, set(src.hosts), name=f"{src.name} page") as r:
        body = r.read(MAX_METADATA_BYTES + 1)
    if len(body) > MAX_METADATA_BYTES:
        raise ValueError(f"{src.name} page exceeds byte limit")
    text = body.decode("utf-8", "replace")
    links: list[dict] = []
    seen: set[str] = set()
    for m in re.finditer(r'href="([^"]*(?:/video/|/media/|/AssetDetail/)[^"]*)"[^>]*>', text):
        url = urllib.parse.urljoin(page_url, m.group(1))
        if urllib.parse.urlsplit(url).hostname not in src.hosts or url in seen:
            continue
        seen.add(url)
        links.append({"url": url})
        if len(links) >= limit:
            break
    return links


def _inspect_gallery(src: Source, url: str) -> dict:
    net.check_url(url, set(src.hosts), f"{src.name} video URL")
    info = _ytdlp_info(url)
    page = info.get("webpage_url") or url
    title = info.get("title") or Path(urllib.parse.urlsplit(page).path).name
    duration = info.get("duration") or 0
    media_url = _pick_format(info)
    parsed = urllib.parse.urlsplit(media_url)
    if parsed.scheme != "https":
        raise ValueError(f"{src.name} media URL must be https")
    return {"title": title, "url": media_url, "page": page,
            "duration": float(duration),
            "creator": info.get("uploader") or src.attribution}


def _download(url: str, dest: Path, extra_hosts: set[str] | None = None) -> Path:
    hosts = {urllib.parse.urlsplit(url).hostname or ""} | (extra_hosts or set())
    hosts.discard("")
    dest.mkdir(parents=True, exist_ok=True)
    with net.bounded_open(url, hosts, name="media download") as r:
        with tempfile.NamedTemporaryFile(prefix=".src-", dir=dest, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            try:
                total = 0
                while block := r.read(1 << 20):
                    total += len(block)
                    if total > MAX_MEDIA_BYTES:
                        raise ValueError("download exceeds byte limit")
                    tmp.write(block)
            except BaseException:
                tmp_path.unlink(missing_ok=True)
                raise
    return tmp_path


def _vecteezy_creds(keys: dict) -> tuple[str, str]:
    """sources.json holds e.g. "vecteezy_ID230020": "<token>" — the numeric
    part (without the 'ID' prefix) is the account id in API paths."""
    for name, token in keys.items():
        m = re.fullmatch(r"vecteezy_?ID?(\d+)", name)
        if m and token:
            return m.group(1), token
    raise ValueError("missing vecteezy creds: add 'vecteezy_ID<account>': '<token>'")


def _vecteezy_get(acct: str, token: str, path: str) -> dict:
    req = net.bounded_request(
        f"https://api.vecteezy.com/v2/{acct}{path}",
        {"api.vecteezy.com"}, name="Vecteezy API",
        headers={"Authorization": f"Bearer {token}"})
    with req as r:
        return json.loads(r.read(MAX_METADATA_BYTES + 1))


def search(src_key: str, query: str, limit: int = 20) -> list[dict]:
    src = source(src_key)
    if not query.strip() or not 1 <= limit <= 50:
        raise ValueError("invalid query or limit")
    if src.kind != "api":
        raise ValueError(f"{src.key} has no search API; paste its page URL into source-import")
    keys = _api_keys()
    key = keys.get(src.api_key_name)
    if src.key != "vecteezy" and not key:
        raise ValueError(f"missing {src.api_key_name} key in secrets/sources.json")
    q = urllib.parse.quote_plus(query)
    if src.key == "pixabay":
        url = (f"https://pixabay.com/api/videos/?key={key}&q={q}&per_page={limit}"
               f"&safesearch=true")
        with net.bounded_open(url, set(src.hosts), name="Pixabay API") as r:
            data = json.loads(r.read(MAX_METADATA_BYTES + 1))
        return [{"ref": str(h["id"]), "title": h.get("tags", ""), "duration": h.get("duration"),
                 "page": h.get("pageURL"), "user": h.get("user")}
                for h in data.get("hits", [])]
    if src.key == "pexels":
        req = net.bounded_request(
            f"https://api.pexels.com/videos/search?query={q}&per_page={limit}",
            {"api.pexels.com"}, name="Pexels API",
            headers={"Authorization": key})
        with req as r:
            data = json.loads(r.read(MAX_METADATA_BYTES + 1))
        return [{"ref": str(v["id"]), "title": v.get("url", "").rsplit("-", 1)[0].rsplit("/", 1)[-1],
                 "duration": v.get("duration"), "page": v.get("url"),
                 "user": (v.get("user") or {}).get("name")}
                for v in data.get("videos", [])]
    if src.key == "vecteezy":
        acct, token = _vecteezy_creds(keys)
        req = net.bounded_request(
            f"https://api.vecteezy.com/v2/{acct}/resources?term={q}"
            f"&content_type=video&per_page={limit}",
            {"api.vecteezy.com"}, name="Vecteezy API",
            headers={"Authorization": f"Bearer {token}"})
        with req as r:
            data = json.loads(r.read(MAX_METADATA_BYTES + 1))
        items = data.get("resources") or data.get("data") or []
        return [{"ref": str(i["id"]), "title": i.get("title", ""),
                 "page": i.get("page_url") or f"https://www.vecteezy.com/video/{i['id']}",
                 "user": i.get("contributor_name")}
                for i in items]
    if src.key == "coverr":
        req = net.bounded_request(
            f"https://api.coverr.co/videos?query={q}&page_size={limit}",
            {"api.coverr.co"}, name="Coverr API", headers={"x-api-key": key})
        with req as r:
            data = json.loads(r.read(MAX_METADATA_BYTES + 1))
        hits = data.get("hits") or data.get("videos") or []
        return [{"ref": h.get("id") or h.get("slug"), "title": h.get("title", ""),
                 "duration": h.get("duration"), "page": h.get("url") or h.get("link"),
                 "user": (h.get("user") or {}).get("name") if isinstance(h.get("user"), dict) else h.get("user")}
                for h in hits]
    return []


def _api_import(conn: sqlite3.Connection, src: Source, ref: str, species: str, cache: Path) -> str:
    keys = _api_keys()
    key = keys.get(src.api_key_name)
    if src.key == "vecteezy":
        acct, token = _vecteezy_creds(keys)
        detail = _vecteezy_get(acct, token, f"/resources/{urllib.parse.quote_plus(ref)}")
        res = detail.get("resource", detail)
        dl = _vecteezy_get(acct, token, f"/resources/{urllib.parse.quote_plus(ref)}/download")
        media_url = dl.get("url")
        if not media_url:
            raise ValueError("Vecteezy returned no download URL")
        title = res.get("title") or f"Vecteezy video {ref}"
        page = res.get("page_url") or f"https://www.vecteezy.com/video/{ref}"
        creator = (res.get("contributor_name") or res.get("contributor") or "Vecteezy")
        return _finish_import(conn, src, tmp_path=_download(media_url, cache), cache=cache,
                              title=title, page=page, creator=creator, species=species)
    if not key:
        raise ValueError(f"missing {src.api_key_name} key in secrets/sources.json")
    if src.key == "pixabay":
        url = f"https://pixabay.com/api/videos/?key={key}&id={urllib.parse.quote_plus(ref)}"
        with net.bounded_open(url, set(src.hosts), name="Pixabay API") as r:
            data = json.loads(r.read(MAX_METADATA_BYTES + 1))
        hits = data.get("hits", [])
        if not hits:
            raise ValueError("Pixabay video not found")
        h = hits[0]
        vids = h.get("videos") or {}
        pick = next((vids[k]["url"] for k in ("large", "medium", "small", "tiny") if vids.get(k)), None)
        if not pick:
            raise ValueError("no downloadable Pixabay rendition")
        return _finish_import(conn, src, tmp_path=_download(pick, cache), cache=cache,
                              title=f"Pixabay video {ref}", page=h.get("pageURL") or ref,
                              creator=h.get("user") or "Pixabay", species=species)
    raise ValueError(f"import for {src.key} not implemented; download manually and use commons-import")


def _finish_import(conn: sqlite3.Connection, src: Source, *, tmp_path: Path, cache: Path,
                   title: str, page: str, creator: str, species: str) -> str:
    cache.mkdir(parents=True, exist_ok=True)
    try:
        probe(tmp_path)
        suffix = Path(urllib.parse.urlsplit(page).path).suffix or ".mp4"
        filename = f"{src.key}-{digest(tmp_path)[:16]}{suffix}"
        path = cache / filename
        existing = conn.execute(
            "SELECT id,path FROM assets WHERE source_ref=? AND sha256=? AND species=? LIMIT 1",
            (page, digest(tmp_path), species)).fetchone()
        if existing and Path(existing["path"]).is_file() and digest(Path(existing["path"])) == digest(tmp_path):
            return existing["id"]
        if not path.exists():
            tmp_path.replace(path)
        evidence = json.dumps({"item": page, "license": src.license_url,
                               "creator": creator, "source": src.key,
                               "note": src.note, "retrieved_at": now()})
        credit = (f"{creator}, {title}, {src.license}, {page}; "
                  f"courtesy {src.attribution}; edited by Naminal Hub")
        # Restrictions is a hard rights gate reserved for per-item problems
        # the operator must clear; source policy notes stay in evidence.
        return import_asset(conn, path, source_ref=page, creator=creator,
                            license_type=src.license, evidence=evidence,
                            attribution=credit, species=species,
                            commercial=True, modifications=True)
    finally:
        tmp_path.unlink(missing_ok=True)


def import_video(conn: sqlite3.Connection, src_key: str, ref: str, species: str, cache: Path) -> str:
    if not species.strip():
        raise ValueError("species tag required")
    src = source(src_key)
    if src.kind == "api":
        return _api_import(conn, src, ref, species, cache)
    item = _inspect_gallery(src, ref)
    tmp_path = _download(item["url"], cache)
    return _finish_import(conn, src, tmp_path=tmp_path, cache=cache, title=item["title"],
                          page=item["page"], creator=item["creator"], species=species)
