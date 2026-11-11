from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sqlite3
import tempfile
import urllib.parse
from pathlib import Path

from . import net
from .core import digest, now
from .media import import_asset, probe

API = "https://commons.wikimedia.org/w/api.php"
MAX_METADATA_BYTES = 1_000_000
MAX_MEDIA_BYTES = 200_000_000
LICENSES = {
    "CC BY 2.0": "/licenses/by/2.0",
    "CC BY 3.0": "/licenses/by/3.0",
    "CC BY 4.0": "/licenses/by/4.0",
    "CC0": "/publicdomain/zero/1.0",
    "CC0 1.0": "/publicdomain/zero/1.0",
}
LICENSE_HOST = "creativecommons.org"
LICENSE_SUFFIXES = re.compile(r"/(?:deed\.[a-zA-Z.]+|legalcode(?:\.[a-zA-Z]+)?)$")


def _license_url_ok(name: str, url: str) -> bool:
    canonical = LICENSES.get(name)
    parsed = urllib.parse.urlsplit(url.rstrip("/"))
    if not canonical or parsed.hostname != LICENSE_HOST or not parsed.path:
        return False
    path = LICENSE_SUFFIXES.sub("", parsed.path.rstrip("/"))
    return path == canonical
MEDIA_HOST = "upload.wikimedia.org"
API_HOST = "commons.wikimedia.org"


def _check_url(url: str, hosts: set[str]) -> None:
    net.check_url(url, hosts, "Commons URL")


def _open(url: str, hosts: set[str]):
    return net.bounded_open(url, hosts, name="Commons URL")


def _api(params: dict) -> dict:
    url = API + "?" + urllib.parse.urlencode({"format": "json", **params})
    with _open(url, {API_HOST}) as response:
        payload = response.read(MAX_METADATA_BYTES + 1)
    if len(payload) > MAX_METADATA_BYTES:
        raise ValueError("Commons metadata exceeds byte limit")
    return json.loads(payload)


def search(query: str, limit: int = 20) -> list[str]:
    if not query.strip() or not 1 <= limit <= 50:
        raise ValueError("invalid Commons query or limit")
    data = _api({"action": "query", "list": "search", "srnamespace": "6",
                 "srsearch": query + " filetype:video", "srlimit": str(limit)})
    seen, titles = set(), []
    for member in data.get("query", {}).get("search", []):
        title = member["title"]
        if title.lower().endswith((".ogv", ".webm", ".mp4")) and title not in seen:
            seen.add(title)
            titles.append(title)
    return titles


def _plain(value: str) -> str:
    return re.sub(r"<[^>]*>", "", html.unescape(value)).strip()[:300]


def _details(title: str) -> dict:
    if not re.fullmatch(r"File:[^/\x00-\x1f]{1,200}\.(?:ogv|webm|mp4)", title, re.IGNORECASE):
        raise ValueError("a Commons File: video title is required")
    result = _api({"action": "query", "prop": "imageinfo",
                   "iiprop": "url|size|extmetadata", "titles": title})
    pages = result.get("query", {}).get("pages", {})
    image = next((p["imageinfo"][0] for p in pages.values() if p.get("imageinfo")), None)
    if not image:
        raise ValueError("Commons file not found")
    video_url = image["url"]
    source_ref = image["descriptionurl"]
    _check_url(video_url, {MEDIA_HOST})
    _check_url(source_ref, {API_HOST})
    if not urllib.parse.urlsplit(video_url).path.startswith("/wikipedia/commons/"):
        raise ValueError("unsafe Commons media URL")
    metadata = image.get("extmetadata", {})
    license_name = _plain(metadata.get("LicenseShortName", {}).get("value", ""))
    license_url = _plain(metadata.get("LicenseUrl", {}).get("value", "")).rstrip("/")
    if not _license_url_ok(license_name, license_url):
        raise ValueError("unknown or restricted Commons license")
    if _plain(metadata.get("Restrictions", {}).get("value", "")):
        raise ValueError("Commons license has item-specific restrictions")
    creator = _plain(metadata.get("Artist", {}).get("value", ""))
    if not creator:
        raise ValueError("Commons creator requires manual verification")
    if not 0 < int(image["size"]) <= MAX_MEDIA_BYTES:
        raise ValueError("Commons media exceeds byte limit")
    return {"title": title, "url": video_url, "source_ref": source_ref,
            "creator": creator, "license": license_name, "license_url": license_url,
            "size": int(image["size"])}


def import_video(conn: sqlite3.Connection, title: str, species: str, cache: Path) -> str:
    if not species.strip():
        raise ValueError("species tag required")
    item = _details(title)
    cache.mkdir(parents=True, exist_ok=True)
    with _open(item["url"], {MEDIA_HOST}) as response:
        with tempfile.NamedTemporaryFile(prefix=".commons-", dir=cache, delete=False) as temp:
            tmp = Path(temp.name)
            try:
                total = 0
                while block := response.read(1 << 20):
                    total += len(block)
                    if total > MAX_MEDIA_BYTES:
                        raise ValueError("Commons download exceeds byte limit")
                    temp.write(block)
            except BaseException:
                tmp.unlink(missing_ok=True)
                raise
    try:
        probe(tmp)
        suffix = Path(urllib.parse.urlsplit(item["url"]).path).suffix.lower()
        filename = hashlib.sha256(title.encode()).hexdigest()[:16] + "-" + digest(tmp)[:16] + suffix
        path = cache / filename
        existing = conn.execute(
            "SELECT id,path FROM assets WHERE source_ref=? AND sha256=? AND species=? "
            "AND creator=? AND license_type=? LIMIT 1",
            (item["source_ref"], digest(tmp), species, item["creator"], item["license"])).fetchone()
        if existing and Path(existing["path"]).is_file() and digest(Path(existing["path"])) == digest(tmp):
            return existing["id"]
        if path.exists() and digest(path) != digest(tmp):
            raise ValueError("Commons cache hash collision")
        if not path.exists():
            os.replace(tmp, path)
        evidence = json.dumps({"item": item["source_ref"], "license": item["license_url"],
                               "creator": item["creator"], "retrieved_at": now()})
        credit = (f"{item['creator']}, {title.removeprefix('File:')}, "
                  f"{item['license']}, {item['source_ref']}; edited by Naminal Hub")
        return import_asset(conn, path, source_ref=item["source_ref"],
                            creator=item["creator"], license_type=item["license"],
                            evidence=evidence, attribution=credit, species=species,
                            commercial=True, modifications=True)
    finally:
        tmp.unlink(missing_ok=True)
