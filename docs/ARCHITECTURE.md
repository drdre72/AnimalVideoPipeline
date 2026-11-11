# Phase Zero: architecture and dependency map

Inspected NewsRoom's AGENTS, README, pyproject, macOS production guide, ADRs,
config, storage/database, repositories, recipes, ingestion, fact ledger,
LLM, claim validator, script generator, audio, editor, render, quality,
service, CLI, macOS, publish, editorial and Gmail code.

## Boundaries and flow

`operator topic -> evidence ledger -> asset catalog -> ranked backlog ->
creative JSON -> deterministic claim/rights validation -> TTS -> alignment ->
video clips + narration + captions -> FFprobe/QC -> immutable manifest ->
operator approval of hash -> local per-platform export -> imported analytics`.

SQLite WAL, a single writer and unique job keys protect durable state. The
creative model reads evidence but does not approve it or perform writes.
No distribution adapter can publish: a local package is not authorization.

## NewsRoom reuse decisions

| Pattern inspected | Decision |
| --- | --- |
| `storage/database.py` WAL + transaction + crash recovery | Adapt concepts; fresh schema |
| `intelligence/fact_ledger.py` supersession | Adapt per-species evidence, retain history |
| `story/claim_validator.py` Fact IDs + numeric check | Adapt; add species, statuses, revisions |
| `llm/ollama.py` structured local generation | Adapt with animal-specific prompts |
| `audio/tts.py`, `kokoro_worker.py` resident TTS | Adapt with independent voice and strict failure |
| `audio/alignment.py`, `editor/captions.py` | Adapt; alignment must fail closed |
| `editor/ffmpeg.py` argv + VideoToolbox | Adapt; video-first trimming/cropping replaces image slides |
| `storage/recipes.py` checked rehydration | Defer; never remove media |
| `publish/upload_session.py` durable retries | Defer until explicit account authorization |
| `ingestion/`, `broadcast/`, maps, RTMP, Gmail, channel identity | Do not reuse |

NewsRoom currently has automatic publishing enabled and TikTok draft push
enabled in its own settings. This project must never inherit those settings.
No code or secrets are imported from the working checkout; patterns are
rewritten independently. Licensing of upstream code must be verified before
any future direct copy.

## New work and milestones

A. Local schema, evidence/rights validation, clip composer, diagnostics and
offline MP4 test. B. Topic ranking, science workflow, catalog, structured
creative, caption/QC and revisions. C. Persistent serial scheduler, review,
local export and recovery. D. Authorized metadata/analytics imports, weekly
reports and evidence-based ranking. Missing capabilities are not inferred
from diagrams: see operations and testing for actual implemented scope.

Initial implementation covers local evidence/asset entry and review, basic
heuristic ranking, local LLM JSON planning, video-clip composition, strict
voice and caption prerequisites, manual review/export, serial jobs and local
analytics/competitor imports. It does not yet cover autonomous science
research, licensed media search, subject tracking, loudness QC, fully
autonomous three-video batch production or weekly optimization.

## Acceptance

Offline tests reject unsupported or stale claims, bad rights, broken media,
stale approvals and unauthorized export; integration tests probe a real MP4.
Hardware acceleration and local ML must be measured on the production Mac
before claiming production readiness.
