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
an explicit editorial exception for a 20–70-second Short. This does not
bypass fact, rights, captions, audio, QC or exact-revision review.

`run` is one bounded batch: it enqueues `SCRIPT_READY` plans up to
`production.daily_review_target`, recovers stale `running` jobs (claimed
longer than `workers.stale_after_s`, or claimed before `claimed_at` existed)
and requeues `failed` jobs that still have attempts under
`workers.max_attempts`, then drains queued jobs serially up to
`workers.queue_size` (`--limit` narrows it). A running job claimed recently is
never stolen by a second `run`; a stale job out of attempts fails permanently.
Enqueueing is idempotent on `(kind, target_id)`, so repeat runs do not
duplicate work. Rendering stays serial: one VideoToolbox encode and one
SQLite writer at a time on this hardware.

`research-search` and `research-propose <topic_id> <title>` use the official
Wikipedia API over the same bounded-HTTPS boundary as Commons (`--allow-network`
required). `research-propose` fetches only the article's plain-text intro,
files each verbatim sentence as a `proposed` fact with the canonical article
URL, and is idempotent per topic. These are triage candidates: narration still
requires `verify-fact` per fact, and `reject-fact` disposes of the rest. The
operator verifies the excerpt and its underlying citation — Wikipedia is a
finding aid, not an authority.

Current limits: operator-assisted topic entry and per-fact verification
(remote research covers Wikipedia intro extracts only), no resident TTS
worker, no launchd installation, no automatic publication, and no
source-specific platform API analytics. Performance imports are local JSON.
Voice and Metal inference performance have not been validated in this
project. Disk availability and current hardware status appear in `doctor`.

`serve --port 8787` runs a read-only local review UI on 127.0.0.1: the
queue, per-revision video, blocking checks, script and manifest. It opens
a fresh SQLite connection per request and cannot approve anything —
approval stays on the CLI with an exact SHA-256 and all four attestations.
