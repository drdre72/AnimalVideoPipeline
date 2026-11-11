# Naminal Hub engineering rules

- Root package `src/animal_pipeline`; `config/` contains safe local defaults;
  `docs/` covers architecture, rights, security, tests and operations.
- `uv sync --no-editable --extra dev`;
  `uv run --no-editable --extra dev pytest`;
  `uv run --no-editable python main.py doctor`.
- SQLite is authoritative: keep facts and revisions append-only, use atomic
  transactions, never silently discard sources, media or failed jobs.
- Factual narration must cite approved, current Fact IDs. Citation existence
  and matching numbers are necessary but not sufficient: editorial reviewers
  must inspect semantic support and species/footage alignment.
- Every asset must have item-specific evidence of commercial and derivative
  rights, and attribution. Unknown, expired or restricted rights block review.
- No NewsRoom tokens, identities, voices, OAuth files or private settings.
- External publishing, uploads, emails and background services are absent
  unless a future separately authorized design adds them. Export is local only.
- One heavy task at a time on 16 GB Apple Silicon; no Docker, CUDA or cloud APIs.
- Subprocesses use argv arrays and bounded timeouts; output files are atomic.
- A revision edit invalidates approval; exact revision and SHA-256 are required
  at approval and export. Read-only CLI commands never change external state.
- Definition of done: offline tests pass, real FFprobe validation for MP4,
  source and rights manifests, explicit hardware validation status.
