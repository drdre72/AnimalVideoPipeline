from __future__ import annotations

import argparse
import asyncio
import json
import platform
import shutil
import sqlite3
import subprocess
from pathlib import Path

from . import analytics, commons, competitors, creative, editor, evidence, media, review, scheduler
from .core import ROOT, connect, settings


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="naminal", description="Naminal Hub local animal video desk")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("doctor", help="Check local Mac, FFmpeg and model readiness")
    sub.add_parser("status", help="Read local database summary")
    topic = sub.add_parser("topic", help="Add a proposed animal subject")
    topic.add_argument("species")
    topic.add_argument("scientific_name")
    topic.add_argument("pillar")
    sub.add_parser("topics", help="Rank subjects by verified facts and usable footage")
    fact = sub.add_parser("fact", help="Import one manually researched fact")
    for name in ("topic_id", "statement", "organization", "source_ref", "excerpt", "retrieved_at"):
        fact.add_argument(name)
    fact.add_argument("--supersedes")
    approve_fact = sub.add_parser("verify-fact", help="Operator verifies exact source excerpt")
    approve_fact.add_argument("fact_id")
    asset = sub.add_parser("asset", help="Catalog one local licensed footage file")
    for name in ("path", "source_ref", "creator", "license_type", "evidence", "species"):
        asset.add_argument(name)
    asset.add_argument("--attribution", default="")
    asset.add_argument("--behavior", default="")
    asset.add_argument("--expires-at")
    asset.add_argument("--commercial", action="store_true")
    asset.add_argument("--modifications", action="store_true")
    asset.add_argument("--test-only", action="store_true")
    assets = sub.add_parser("assets", help="List cataloged footage and rights status")
    assets.add_argument("--all", action="store_true")
    commons_search = sub.add_parser("commons-search", help="Search Commons video titles via the official API")
    commons_search.add_argument("query")
    commons_search.add_argument("--allow-network", action="store_true")
    commons_import = sub.add_parser("commons-import", help="Download one Commons file into unapproved catalog")
    commons_import.add_argument("title")
    commons_import.add_argument("--species", required=True)
    commons_import.add_argument("--allow-network", action="store_true")
    inspect = sub.add_parser("inspect-asset", help="Generate clip contact sheet and vertical preview")
    inspect.add_argument("asset_id")
    rights = sub.add_parser("verify-rights", help="Operator confirms rights evidence")
    rights.add_argument("asset_id")
    plan = sub.add_parser("plan", help="Generate plan with local Ollama from approved evidence")
    plan.add_argument("topic_id")
    draft = sub.add_parser("draft", help="Validate and store a structured local plan JSON")
    draft.add_argument("json_path")
    draft.add_argument("--footage-first", action="store_true",
                       help="Permit a shorter, non-padded Short with an editorial reason")
    draft.add_argument("--reason", default="")
    render = sub.add_parser("render", help="Render footage and approved-voice narration")
    render.add_argument("plan_id")
    rev = sub.add_parser("review", help="Inspect current revision, QC and readiness")
    rev.add_argument("plan_id")
    approval = sub.add_parser("approve", help="Approve an exact video SHA-256")
    approval.add_argument("plan_id")
    approval.add_argument("sha256")
    for field in review.REQUIRED_ATTESTATIONS:
        approval.add_argument(f"--verify-{field}", action="store_true")
    exp = sub.add_parser("export", help="Generate a local manual-upload package only")
    exp.add_argument("plan_id")
    exp.add_argument("--platform", required=True, choices=review.PLATFORMS)
    exp.add_argument("--dry-run", action="store_true")
    queue = sub.add_parser("queue", help="Queue one serial render job idempotently")
    queue.add_argument("plan_id")
    retry = sub.add_parser("retry", help="Retry a failed job up to three attempts")
    retry.add_argument("job_id")
    sub.add_parser("run", help="Recover and process one bounded queue batch")
    perf = sub.add_parser("analytics", help="Import or report authorized local analytics JSON")
    perf.add_argument("action", choices=("import", "report"))
    perf.add_argument("path", nargs="?")
    bench = sub.add_parser("competitors", help="Import or report permitted public metadata JSON")
    bench.add_argument("action", choices=("import", "report"))
    bench.add_argument("path", nargs="?")
    sub.add_parser("simulate", help="Run synthetic nonpublishable fixture demonstration")
    return p


def doctor() -> dict:
    cfg = settings()
    encoders = subprocess.run(["ffmpeg", "-encoders"], capture_output=True, text=True, timeout=30
                              ).stdout if shutil.which("ffmpeg") else ""
    return {"machine": platform.machine(), "macos": platform.system() == "Darwin",
            "ffmpeg": bool(shutil.which("ffmpeg")), "ffprobe": bool(shutil.which("ffprobe")),
            "videotoolbox": "h264_videotoolbox" in encoders,
            "whisper": bool(shutil.which("whisper-cli")),
            "whisper_model": (ROOT / cfg["alignment"]["model_path"]).is_file(),
            "ollama": bool(shutil.which("ollama")),
            "voice_approved": cfg["voice"]["approved"],
            "free_gb": round(shutil.disk_usage(ROOT).free / 1024**3, 1),
            "publishing_enabled": False}


def dispatch(args: argparse.Namespace) -> object:
    if args.command == "doctor":
        return doctor()
    if args.command == "simulate":
        from .simulation import demo
        return demo()
    conn = connect()
    try:
        if args.command == "status":
            return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("topics", "facts", "assets", "plans", "jobs", "analytics", "competitors")}
        if args.command == "topic":
            return evidence.add_topic(conn, args.species, args.scientific_name, args.pillar)
        if args.command == "topics":
            return creative.rank_topics(conn)
        if args.command == "fact":
            return evidence.add_fact(conn, args.topic_id, args.statement, args.organization,
                                     args.source_ref, args.excerpt, args.retrieved_at,
                                     supersedes=args.supersedes)
        if args.command == "verify-fact":
            evidence.approve_fact(conn, args.fact_id)
            return "fact approved after operator verification"
        if args.command == "asset":
            return media.import_asset(conn, Path(args.path), source_ref=args.source_ref,
                                      creator=args.creator, license_type=args.license_type,
                                      evidence=args.evidence, attribution=args.attribution,
                                      species=args.species, behavior=args.behavior,
                                      commercial=args.commercial, modifications=args.modifications,
                                      expires_at=args.expires_at, test_only=args.test_only)
        if args.command == "assets":
            return [dict(a) for a in conn.execute("SELECT * FROM assets")]
        if args.command in ("commons-search", "commons-import"):
            if not args.allow_network:
                raise ValueError("explicit --allow-network required for Commons access")
            if args.command == "commons-search":
                return commons.search(args.query)
            return {"asset_id": commons.import_video(conn, args.title, args.species,
                                                       ROOT / "cache" / "commons"),
                    "approved": False, "requires_operator_rights_review": True}
        if args.command == "inspect-asset":
            return media.inspect_asset(conn, args.asset_id, ROOT / "cache" / "media")
        if args.command == "verify-rights":
            media.approve_asset(conn, args.asset_id)
            return "rights approved after operator verification"
        if args.command == "plan":
            return creative.generate_plan(conn, args.topic_id)
        if args.command == "draft":
            return creative.save_plan(conn, json.loads(Path(args.json_path).read_text()),
                                      footage_first=args.footage_first, reason=args.reason)
        if args.command == "render":
            return editor.compose(conn, args.plan_id)
        if args.command == "review":
            row = conn.execute("SELECT * FROM plans WHERE id=?", (args.plan_id,)).fetchone()
            return {"plan": dict(row), "blocking": review.review_errors(conn, row)} if row else "unknown plan"
        if args.command == "approve":
            verified = tuple(field for field in review.REQUIRED_ATTESTATIONS
                             if getattr(args, f"verify_{field}"))
            review.approve(conn, args.plan_id, args.sha256, verified=verified)
            return "exact revision approved"
        if args.command == "export":
            return review.export(conn, args.plan_id, args.platform, dry_run=args.dry_run)
        if args.command == "queue":
            return {"enqueued": scheduler.enqueue(conn, "render", args.plan_id)}
        if args.command == "retry":
            return {"requeued": scheduler.retry(conn, args.job_id)}
        if args.command == "run":
            recovered = scheduler.recover(conn)
            return {"recovered": recovered, "jobs": asyncio.run(scheduler.drain(conn, editor.compose))}
        if args.command == "analytics":
            if args.action == "import":
                if not args.path:
                    raise ValueError("analytics import requires a local JSON path")
                return {"imported": analytics.import_snapshots(conn, Path(args.path))}
            return analytics.report(conn)
        if args.command == "competitors":
            if args.action == "import":
                if not args.path:
                    raise ValueError("competitors import requires an authorized metadata JSON path")
                return {"imported": competitors.import_metadata(conn, Path(args.path))}
            return competitors.report(conn)
        raise ValueError("unsupported command")
    finally:
        conn.close()


def main() -> int:
    p = parser()
    args = p.parse_args()
    try:
        print(json.dumps(dispatch(args), indent=2, default=str))
    except (ValueError, RuntimeError, OSError, sqlite3.Error) as exc:
        p.exit(1, f"error: {exc}\n")
    return 0
