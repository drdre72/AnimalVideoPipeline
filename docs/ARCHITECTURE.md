# Phase Zero: architecture and dependency map

Inspected NewsRoom's AGENTS, README, pyproject, macOS production guide, ADRs,
config, storage/database, repositories, recipes, ingestion, fact ledger,
LLM, claim validator, script generator, audio, editor, render, quality,
service, CLI, macOS, publish, editorial and Gmail code.

## Boundaries and flow

`operator topic -> evidence ledger -> asset catalog -> ranked backlog ->
footage budget -> creative JSON -> deterministic claim/rights validation ->
TTS -> alignment -> video clips + narration + captions -> FFprobe/QC ->
immutable manifest (+evidence snapshot) -> operator approval of hash ->
local per-platform export -> authorized read-only analytics ingestion`.

Before planning, `budget.footage_budget` computes usable media seconds,
directional species matches, shot windows and a MICRO/STANDARD/EXTENDED
profile; the LLM receives the contract and `save_plan` re-enforces it.
Google access is an allowlist credential migration into a gitignored
`secrets/google.json`, read-only OAuth scopes, channel-identity
verification, and bounded retries. Upload transport exists only as a
dormant, separately-gated adapter.

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

Implemented so far: evidence/asset entry and triage (manual, Wikipedia
proposals, bounded Commons video import), directional-species footage
budgets with MICRO/STANDARD/EXTENDED profile selection, versioned
explainable ranking, local LLM planning under the budget contract, strict
voice + resident Kokoro worker, loudness/black/caption QC, revision-bound
review/export, bounded `run` batches with crash recovery and a circuit
breaker, launchd agent with pause/resume/health, read-only review/triage/
dashboard UI, and read-only YouTube Data + Analytics + competitor
ingestion behind allowlisted credential migration and channel-identity
checks. Not implemented: live upload transport (dormant adapter only),
machine-verified species/crop judgment, subject tracking and demographic
analytics.

## Acceptance

Offline tests reject unsupported or stale claims, bad rights, broken media,
stale approvals and unauthorized export; integration tests probe a real MP4.
Hardware acceleration and local ML must be measured on the production Mac
before claiming production readiness.
