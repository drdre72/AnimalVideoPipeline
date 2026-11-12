# Feature status and outstanding risks

Tested scope: 87 tests, all offline-safe (Google APIs mocked); see TESTING.md.

## Proven end-to-end

- Evidence ledger → catalog → rights → plan → render → QC → review →
  approve → export. Two real productions (day octopus, giant cuttlefish)
  exported for YouTube/TikTok/Instagram with hash-bound approvals.
- FootageBudget contract before every plan: directional species matching
  (generic topic may use more-specific footage, never the reverse), usable
  seconds, in/out windows, shot cap, narration estimate, MICRO/STANDARD/
  EXTENDED profile with recorded reason. `save_plan` re-enforces it; an
  over-budget plan fails closed — verified live (LLM overshoot rejected).
- Versioned explainable ranking: observed vs predicted signals separated,
  analytics log-scaled against outliers, exploration bonus for
  underrepresented pillars.
- Bounded `run`: idempotent enqueue, stale-job recovery, retry bounds,
  `WAITING_FOR_MODEL` when Ollama is down, needs_media/needs_evidence
  triage, serial rendering. launchd agent operator-installed only, with
  pause/resume/health.
- Read-only YouTube connector: allowlist credential migration
  (`client_id`/`client_secret`/`project_id` only → gitignored mode-600
  `secrets/google.json`), PKCE loopback OAuth with read-only scopes,
  persisted channel identity checked before every call, deduplicated
  analytics + competitor snapshots, dormant upload adapter gated on
  explicit enablement AND verified channel identity.
- Read-only UI on 127.0.0.1: queue, `/triage` (prefilled verify commands),
  `/dashboard` (candidates, budgets, drafts, failures, exports, analytics,
  disk), `/report` (weekly, non-causal wording).

## Not implemented / dormant

- Live upload transport — adapter refuses unless `publishing
  .uploads_enabled` plus verified channel; nothing enables it.
- Machine species/crop judgment, subject tracking, semantic citation
  support — operator attestations by design.
- Retention/demographics beyond what the Analytics API returns for the
  authorized channel; competitor retention/revenue is never claimed.
- launchd agent: plist template exists; `services install` is manual.

## Outstanding risks

- **LLM timing contract**: qwen3 conflates `start_s` (source in-point)
  with timeline position ~1-in-2 runs; the validator rejects it
  fail-closed and the prompt now spells the contract out, but multi-asset
  plans may still need an operator re-time at `draft` time.
- **Flamboyant plan semantics**: one generated segment implies the colour
  change hides the animal; evidence describes a warning display. The
  deterministic validator cannot catch semantics — flagged for the
  operator visual-review pass (documented limitation).
- **YouTube authorization is operator-pending**: `youtube status` shows
  `client_configured=true, authorized=false` — no live API demo until
  `youtube authorize` is run interactively.
- **`uv sync` wheel staleness**: new modules may need a package reinstall
  before `main.py` sees them.
- **iCloud Drive sync**: the repo lives under `~/Documents`, which iCloud
  syncs; a mass package install produced ~21,500 hidden "name 2" conflict
  duplicates inside `.venv`, turning a transformers import into a 4-minute
  stall (fixed once: `find .venv -name '* 2.*' -delete`). If it recurs,
  either keep removing them, move `.venv` outside the synced tree, or
  exclude the folder in System Settings → iCloud.
- **16 GB RAM**: one heavy job at a time is enforced; sustained daily
  throughput is bounded by render time, not scheduler capacity.
