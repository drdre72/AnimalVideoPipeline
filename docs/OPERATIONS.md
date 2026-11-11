# Operations

Run `uv sync --no-editable --extra dev`, then
`uv run --no-editable python main.py doctor`. FFmpeg and FFprobe are required.
For production voice, install `uv sync --no-editable --extra tts`,
use the approved `af_bella` voice in `config/settings.yaml`, run Ollama locally
with an installed Qwen-class model and configure a local whisper.cpp model.
The strict render path fails rather than substitute another voice or estimated
caption timing. Production assets and evidence must be supplied by an operator.
Use `--help` for each command. Simulation outputs are marked nonpublishable.

All external actions and launchd installs remain disabled. Database, output
and cache are local; no automated deletion or rehydration is enabled. The
`simulate` command generates a short synthetic color test and tone, marks
its QC as failed, and cannot export it. It does not satisfy the real animal
video acceptance criterion. Complete this with rights-cleared wildlife
footage, independently verified species-specific facts and an approved voice
and whisper model. `plan` needs a running local Ollama server.
To approve a production render, inspect its manifest, facts, species/crop,
rights and sound, then pass all four `--verify-*` attestations with the exact
SHA-256 shown by `review`. This is an operator assertion, not a substitute for
independent evidence.

Some Wikimedia Commons OGV files use Theora/Vorbis and may appear black or
fail to play in macOS QuickTime. `inspect-asset <asset_id>` writes a separate
H.264/AAC `playable_preview` MP4 in `cache/media/` for review, alongside its
contact sheet and vertical still. The rights-bound original remains intact;
the preview does not approve the asset or authorize distribution.

For footage shorter than the default 61-second target, `draft <plan.json>
--footage-first --reason "Use distinct footage rather than padding"` records
an explicit editorial exception for a 15–70-second Short. This does not
bypass fact, rights, captions, audio, QC or exact-revision review.

Before any plan call, `budget.footage_budget` computes a deterministic
contract: which rights-cleared assets verify the topic's species (exact tag,
or a more specific tag inside a configured `taxonomy` family — a generic
clip never satisfies a specific topic), usable seconds per asset, in/out
windows, the maximum shot length, a narration word estimate, and the
selected production profile: EXTENDED (>=61 s usable), STANDARD (30–60),
MICRO (15–29) or NEEDS_MEDIA with an actionable reason. `generate_plan`
hands the whole contract to the LLM; `save_plan` re-checks it
deterministically and rejects plans whose total segment duration exceeds
usable footage or whose shots break the windows or shot cap. `run` drafts
non-EXTENDED profiles by passing the recorded profile reason as the
footage-first exception, and reports NEEDS_MEDIA topics under
`needs_media` rather than attempting them.

`run` is one bounded batch. It first drafts plans for ranked topics that
have approved facts and footage but no open revision
(`SCRIPT_READY`/`READY_FOR_REVIEW`/`APPROVED`/`EXPORTED`), up to
`production.daily_review_target`; a draft that fails validation is recorded
in `plan_errors` and never stored, a topic whose footage budget is below
the micro floor is reported under `needs_media`, and an unreachable
planner stops the drafting phase only. It then enqueues `SCRIPT_READY` plans, recovers stale
`running` jobs (claimed longer than `workers.stale_after_s`, or claimed
before `claimed_at` existed) and requeues `failed` jobs that still have
attempts under `workers.max_attempts`, then drains queued jobs serially up
to `workers.queue_size` (`--limit` narrows it, `--no-draft` skips drafting).
A running job claimed recently is never stolen by a second `run`; a stale
job out of attempts fails permanently.
Enqueueing is idempotent on `(kind, target_id)`, so repeat runs do not
duplicate work. Rendering stays serial: one VideoToolbox encode and one
SQLite writer at a time on this hardware.

Production renders normalize narration toward `render.loudness_target`
(-14 LUFS) with a static gain capped by `voice.max_gain_db` and an
`alimiter` ceiling, then verify the finished file with ebur128 inside
`render.loudness_tolerance`. A narration track needing more than the cap
fails before encoding; a render drifting outside the band fails QC.

`research-search` and `research-propose <topic_id> <title>` use the official
Wikipedia API over the same bounded-HTTPS boundary as Commons (`--allow-network`
required). `research-propose` fetches only the article's plain-text intro,
files each verbatim sentence as a `proposed` fact with the canonical article
URL, and is idempotent per topic. These are triage candidates: narration still
requires `verify-fact` per fact, and `reject-fact` disposes of the rest. The
operator verifies the excerpt and its underlying citation — Wikipedia is a
finding aid, not an authority.

`services install|status|uninstall` manages a single LaunchAgent,
`ai.naminal.desk`, that runs `main.py run --limit <service.run_limit>` every
`service.interval_s` seconds under the project `.venv`. It is operator
install only, `RunAtLoad` is off, output goes to `output/logs/`, and the
agent still cannot publish — it only produces review-ready drafts.

Narration runs through a resident Kokoro worker
(`python -m animal_pipeline.kokoro_worker`): the model loads once per
process instead of per render, a dead worker respawns once, and the
per-call CLI remains the cold fallback. First synthesis includes the
model load; subsequent ones are an order of magnitude faster.

Current limits: operator-assisted topic entry and per-fact verification
(remote research covers Wikipedia intro extracts only), no automatic
publication, and no source-specific platform API analytics. Performance
imports are local JSON. Voice and Metal inference performance have not
been validated in this project. Disk availability and current hardware
status appear in `doctor`.

`serve --port 8787` runs a read-only local review UI on 127.0.0.1: the
queue, per-revision video, blocking checks, script and manifest, plus a
`/triage` page listing proposed facts and unverified assets with their
`verify-fact`/`verify-rights` commands ready to paste. Unblocked revisions
show the prefilled `approve` command (exact SHA-256, all four attestations)
and approved ones the three `export` commands; nothing mutates over HTTP —
a fresh SQLite connection per request serves GETs only.
