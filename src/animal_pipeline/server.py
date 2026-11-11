from __future__ import annotations

import html
import json
import mimetypes
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .budget import footage_budget
from .core import ROOT, connect, settings
from .creative import rank_topics
from .report import weekly
from .review import PLATFORMS, review_errors


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<title>{_esc(title)} — Naminal Hub review</title>
<style>body{{font-family:-apple-system,sans-serif;max-width:760px;margin:2rem auto;
padding:0 1rem;background:#111;color:#eee}}table{{width:100%;border-collapse:collapse}}
td,th{{padding:.4rem;border-bottom:1px solid #333;text-align:left;font-size:.9rem}}
a{{color:#8ec7ff}}video{{width:100%;max-width:360px;border-radius:8px}}
pre{{background:#000;padding:.7rem;overflow:auto;font-size:.75rem}}
.badge{{padding:.15rem .5rem;border-radius:4px;background:#333;font-size:.75rem}}
.block{{color:#ff9c9c}}.cmd{{position:relative;padding-right:4.5rem}}
.copy{{position:absolute;top:.5rem;right:.5rem;background:#333;border:0;color:#8ec7ff;
padding:.2rem .6rem;border-radius:4px;cursor:pointer;font-size:.75rem}}</style>
<script>function copycmd(b){{navigator.clipboard.writeText(
b.parentElement.innerText.replace('copy','').trim())}}</script>
</head><body><h1>Naminal Hub</h1>{body}
<p><a href="/">← Review queue</a> · <a href="/triage">Triage</a></p></body></html>"""


def _cmd_block(commands: list[str]) -> str:
    return ("<pre class='cmd'><button class='copy' onclick='copycmd(this)'>copy</button>"
            + _esc("\n".join(commands)) + "</pre>")


def queue_page(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT p.id,p.revision,p.status,p.created_at,t.species FROM plans p "
        "JOIN topics t ON t.id=p.topic_id ORDER BY p.created_at DESC").fetchall()
    lines = "".join(
        f"<tr><td><a href='/review/{_esc(r['id'])}'>{_esc(r['species'])}</a></td>"
        f"<td>r{_esc(r['revision'])}</td><td><span class='badge'>{_esc(r['status'])}</span></td>"
        f"<td>{_esc(r['created_at'][:19])}</td></tr>" for r in rows)
    pending = conn.execute(
        "SELECT (SELECT COUNT(*) FROM facts WHERE status='proposed') + "
        "(SELECT COUNT(*) FROM assets WHERE approved=0)").fetchone()[0]
    body = ("<h2>Review queue</h2><table><tr><th>Subject</th><th>Rev</th>"
            "<th>Status</th><th>Created (UTC)</th></tr>" + lines + "</table>"
            f"<p><a href='/triage'>{pending} item(s) awaiting verification</a> · "
            "<a href='/dashboard'>Dashboard</a> · <a href='/report'>Weekly report</a> — "
            "approval and export commands are prefilled on each revision page.</p>")
    return _page("Queue", body)


def review_page(conn: sqlite3.Connection, plan_id: str) -> str:
    row = conn.execute("SELECT p.*, t.species FROM plans p JOIN topics t ON t.id=p.topic_id "
                       "WHERE p.id=?", (plan_id,)).fetchone()
    if not row:
        return _page("Unknown", "<p>Unknown plan.</p>")
    plan = json.loads(row["content"])
    blocking = review_errors(conn, row)
    manifest_path = Path(row["video_path"]).with_name("manifest.json") if row["video_path"] else None
    manifest = (manifest_path.read_text() if manifest_path and manifest_path.is_file() else "{}")
    video = (f"<video controls src='/video/{_esc(plan_id)}'></video>"
             if row["video_path"] and Path(row["video_path"]).is_file() else
             "<p>No rendered video.</p>")
    errors = "".join(f"<li class='block'>{_esc(e)}</li>" for e in blocking)
    script = "".join(f"<li>{_esc(s['text'])} <em>({_esc(s['duration_s'])}s)</em></li>"
                     for s in plan.get("segments", []))
    action = ""
    if not blocking and row["status"] == "READY_FOR_REVIEW":
        action = ("<h3>Approve this exact revision</h3>" + _cmd_block([
            f"python main.py approve {row['id']} {row['video_hash']} "
            "--verify-facts --verify-visuals --verify-rights --verify-audio"]))
    elif not blocking and row["status"] == "APPROVED":
        action = ("<h3>Local export packages</h3>" + _cmd_block([
            f"python main.py export {row['id']} --platform {platform}"
            for platform in PLATFORMS]))
    body = (f"<h2>{_esc(plan.get('title', row['species']))}</h2>"
            f"<p><span class='badge'>{_esc(row['status'])}</span> "
            f"rev {_esc(row['revision'])} · <code>{_esc(row['video_hash'] or 'no video')}</code></p>"
            f"{video}<h3>Blocking checks</h3><ul>{errors or '<li>none</li>'}</ul>{action}"
            f"<h3>Script</h3><ul>{script}</ul><h3>Description</h3>"
            f"<p>{_esc(plan.get('description', ''))}</p><h3>Manifest</h3><pre>{_esc(manifest)}</pre>")
    return _page(row["species"], body)


def triage_page(conn: sqlite3.Connection) -> str:
    facts = conn.execute(
        "SELECT f.id,f.statement,f.excerpt,f.organization,f.source_ref,t.species "
        "FROM facts f JOIN topics t ON t.id=f.topic_id "
        "WHERE f.status='proposed' ORDER BY f.created_at").fetchall()
    assets = conn.execute(
        "SELECT id,species,creator,license_type,source_ref,duration FROM assets "
        "WHERE approved=0 ORDER BY created_at").fetchall()
    fact_rows = "".join(
        f"<tr><td>{_esc(f['species'])}</td><td>{_esc(f['statement'])}</td>"
        f"<td>{_esc(f['organization'])}</td></tr>"
        f"<tr><td colspan='3'><small>{_esc(f['excerpt'][:220])} — "
        f"<a href='{_esc(f['source_ref'])}'>{_esc(f['source_ref'][:80])}</a></small></td></tr>"
        for f in facts)
    asset_rows = "".join(
        f"<tr><td>{_esc(a['species'])}</td><td>{_esc(a['creator'])}</td>"
        f"<td>{_esc(a['license_type'])}</td><td>{_esc(round(a['duration'], 1))}s</td></tr>"
        f"<tr><td colspan='4'><small><a href='{_esc(a['source_ref'])}'>"
        f"{_esc(a['source_ref'][:100])}</a></small></td></tr>" for a in assets)
    commands = ([f"python main.py verify-fact {f['id']}" for f in facts] +
                [f"python main.py verify-rights {a['id']}" for a in assets])
    bulk = ("<h3>Verify everything listed</h3>" + _cmd_block(commands)
            if commands else "<p>Nothing awaiting verification.</p>")
    body = (f"<h2>Triage — verify before approval</h2>"
            f"<h3>Proposed facts ({len(facts)})</h3><table>{fact_rows or '<tr><td>none</td></tr>'}</table>"
            f"<h3>Unverified assets ({len(assets)})</h3><table>{asset_rows or '<tr><td>none</td></tr>'}</table>"
            f"{bulk}<p>Read each excerpt and source before running its command — "
            "verification is an operator assertion, not a formality.</p>")
    return _page("Triage", body)


def dashboard_page(conn: sqlite3.Connection) -> str:
    ranked = rank_topics(conn)[:8]
    budget_rows = ""
    for topic in ranked:
        try:
            budget = footage_budget(conn, topic["id"])
            detail = (f"{budget['profile']} · {budget['total_usable_s']:.1f}s usable · "
                      f"{len(budget['assets'])} asset(s)")
        except ValueError:
            detail = "budget unavailable"
        signals = topic["reasons"].get("signals", {})
        budget_rows += (f"<tr><td>{_esc(topic['species'])}</td><td>{_esc(round(topic['score'], 1))}</td>"
                        f"<td>{_esc(detail)}</td>"
                        f"<td><small>{_esc(topic['reasons'].get('formula', ''))} · "
                        f"{_esc(', '.join(f'{k}={v}' for k, v in topic['reasons'].get('contributions', {}).items()))}</small></td></tr>")
    awaiting = conn.execute(
        "SELECT p.id,p.status,p.revision,t.species FROM plans p JOIN topics t ON t.id=p.topic_id "
        "WHERE p.status IN ('SCRIPT_READY','QC_FAILED','CHANGES_REQUESTED') ORDER BY p.created_at DESC"
    ).fetchall()
    awaiting_rows = "".join(
        f"<tr><td><a href='/review/{_esc(r['id'])}'>{_esc(r['species'])}</a></td>"
        f"<td>r{_esc(r['revision'])}</td><td><span class='badge'>{_esc(r['status'])}</span></td></tr>"
        for r in awaiting)
    running = conn.execute(
        "SELECT kind,target_id,attempts,claimed_at FROM jobs WHERE status='running'").fetchall()
    last_run = conn.execute("SELECT started_at,finished_at,summary FROM runs "
                            "ORDER BY started_at DESC LIMIT 1").fetchone()
    failed = conn.execute(
        "SELECT kind,target_id,attempts,error FROM jobs WHERE status='failed' "
        "ORDER BY created_at DESC LIMIT 5").fetchall()
    failed_rows = "".join(
        f"<tr><td>{_esc(j['kind'])}</td><td>{_esc(j['attempts'])}</td>"
        f"<td><small>{_esc(j['error'][:160])}</small></td></tr>" for j in failed)
    exportable = conn.execute(
        "SELECT p.id,t.species,p.revision FROM plans p JOIN topics t ON t.id=p.topic_id "
        "WHERE p.status='APPROVED'").fetchall()
    export_rows = "".join(
        f"<li><a href='/review/{_esc(r['id'])}'>{_esc(r['species'])} r{_esc(r['revision'])}</a></li>"
        for r in exportable)
    videos = conn.execute(
        "SELECT video_id,plan_id,title,published_at FROM youtube_videos ORDER BY published_at DESC LIMIT 10"
    ).fetchall()
    video_rows = ""
    for video in videos:
        snap = conn.execute(
            "SELECT views,observed_at,subscribers FROM analytics WHERE video_id=? "
            "ORDER BY observed_at DESC LIMIT 1", (video["video_id"],)).fetchone()
        video_rows += (f"<tr><td>{_esc(video['title'][:40])}</td>"
                       f"<td><code>{_esc(video['video_id'])}</code></td>"
                       f"<td>{_esc(snap['views'] if snap else '—')}</td>"
                       f"<td>{_esc((snap['observed_at'] or '')[:10] if snap else '—')}</td></tr>")
    subscribers = conn.execute(
        "SELECT COALESCE(SUM(subscribers),0) FROM (SELECT subscribers FROM analytics "
        "WHERE subscribers IS NOT NULL GROUP BY video_id,platform HAVING "
        "observed_at=MAX(observed_at))").fetchone()[0]
    free_gb = round(__import__("shutil").disk_usage(ROOT).free / 1024**3, 1)
    stage = (f"running job(s): {len(running)}" if running else
             (f"last run {last_run['started_at'][:19]}"
              + (" (unfinished)" if last_run and not last_run["finished_at"] else "")
              if last_run else "no runs yet"))
    body = (f"<h2>Operator dashboard</h2>"
            f"<h3>Active stage</h3><p>{_esc(stage)} · free disk {_esc(free_gb)} GB</p>"
            f"<h3>Top research candidates</h3><table><tr><th>Topic</th><th>Score</th>"
            f"<th>Footage</th><th>Signals</th></tr>{budget_rows or '<tr><td>none</td></tr>'}</table>"
            f"<h3>Drafts awaiting attention</h3><table>{awaiting_rows or '<tr><td>none</td></tr>'}</table>"
            f"<h3>Render failures &amp; retry reasons</h3><table>{failed_rows or '<tr><td>none</td></tr>'}</table>"
            f"<h3>Export-ready</h3><ul>{export_rows or '<li>none</li>'}</ul>"
            f"<h3>Published videos</h3><table><tr><th>Title</th><th>ID</th><th>Views</th>"
            f"<th>Observed</th></tr>{video_rows or '<tr><td colspan=4>none tracked</td></tr>'}</table>"
            f"<p><small>Subscriber gain observed: {_esc(subscribers)} · "
            f"<a href='/report'>weekly optimization report</a></small></p>")
    return _page("Dashboard", body)


def report_page(conn: sqlite3.Connection) -> str:
    data = weekly(conn)
    top = "".join(f"<tr><td><code>{_esc(v['video_id'])}</code></td><td>{_esc(v['platform'])}</td>"
                  f"<td>{_esc(v['views'])}</td><td>{_esc(v['views_per_day'])}/day</td>"
                  f"<td><small>{_esc(v['source'])}</small></td></tr>" for v in data["top_videos"])
    experiments = "".join(f"<tr><td>{_esc(e['topic'])}</td><td>{_esc(e['kind'])}</td>"
                          f"<td><small>{_esc(e['detail'])}</small></td></tr>"
                          for e in data["experiments"])
    failures = "".join(f"<tr><td>{_esc(f['kind'])}</td><td>{_esc(f['attempts'])}</td>"
                       f"<td><small>{_esc(f['error'][:160])}</small></td></tr>"
                       for f in data["render_failures"])
    pipeline = ", ".join(f"{_esc(k)}: {_esc(v)}" for k, v in sorted(data["pipeline"].items()))
    channel = data["channel"]
    body = (f"<h2>Weekly optimization report</h2>"
            f"<p><small>generated {_esc(data['generated_at'][:19])} · {_esc(data['period_days'])}-day view · "
            f"{_esc(data['note'])}</small></p>"
            f"<h3>Performance</h3><table><tr><th>Video</th><th>Platform</th><th>Views</th>"
            f"<th>Velocity</th><th>Source</th></tr>{top or '<tr><td colspan=5>no observed data</td></tr>'}</table>"
            f"<p>Tracked videos: {_esc(channel['tracked_videos'])} · "
            f"observed subscriber gain: {_esc(channel['subscriber_gain_observed'])} · "
            f"median velocity: {_esc(channel['median_views_per_day'])}/day</p>"
            f"<h3>Failures</h3><table>{failures or '<tr><td>none</td></tr>'}</table>"
            f"<h3>Pipeline</h3><p>{pipeline or 'no plans'}</p>"
            f"<h3>Recommended experiments</h3><table>{experiments or '<tr><td>none</td></tr>'}</table>")
    return _page("Weekly report", body)


def make_handler(db_path: Path | None):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, content: bytes, mime: str = "text/html; charset=utf-8"):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "GET":
                self.wfile.write(content)

        def _video(self, conn: sqlite3.Connection, plan_id: str):
            row = conn.execute("SELECT video_path, video_hash FROM plans WHERE id=?",
                               (plan_id,)).fetchone()
            if not row or not row["video_path"]:
                return self._send(404, b"no video")
            path = Path(row["video_path"]).resolve()
            if not path.is_file() or ROOT not in path.parents:
                return self._send(404, b"no video")
            return self._send(200, path.read_bytes(),
                              mimetypes.guess_type(path.name)[0] or "video/mp4")

        def do_GET(self):
            conn = connect(db_path)
            try:
                path = urlparse(self.path).path
                if path == "/":
                    return self._send(200, queue_page(conn).encode())
                if path == "/triage":
                    return self._send(200, triage_page(conn).encode())
                if path == "/dashboard":
                    return self._send(200, dashboard_page(conn).encode())
                if path == "/report":
                    return self._send(200, report_page(conn).encode())
                if path.startswith("/review/"):
                    target = path[len("/review/"):].split("/")[0]
                    return self._send(200, review_page(conn, target).encode())
                if path.startswith("/video/"):
                    return self._video(conn, path[len("/video/"):].split("/")[0])
                self._send(404, b"not found")
            finally:
                conn.close()

        def do_HEAD(self):
            self.do_GET()

        def log_message(self, *_args):
            return

    return Handler


def serve(db_path: Path | None = None, host: str = "127.0.0.1",
          port: int = 8787) -> None:
    httpd = ThreadingHTTPServer((host, port), make_handler(db_path))
    print(f"Review UI (read-only, local): http://{host}:{port}")
    httpd.serve_forever()
