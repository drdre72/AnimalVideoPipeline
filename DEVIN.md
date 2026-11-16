# Devin video-production runbook

Read `AGENTS.md` and `docs/OPERATIONS.md` first. The project makes local
review candidates, not publishable videos by default. Do not approve, export,
upload, publish, or install background services as part of a routine run.
Keep the user's existing database, outputs, revisions, and working-tree edits.

## Before spending render time

1. Check `git status`, `doctor`, the selected topic's latest revision, job
   attempts, rights, fact IDs, and footage budget. Do not blindly rerun the
   oldest `SCRIPT_READY` plans or retry exhausted failures. Prefer one bounded
   candidate at a time; never overwrite an existing `master.mp4`.
2. Compare the proposed asset IDs **and source SHA-256 hashes** against assets
   used by existing exported/approved plans and recent renders. Different
   catalog IDs can point at the same footage. Do not reuse a previously used
   source or near-identical clip for a new video without the user's explicit
   okay; a new animal or script alone does not make reused visuals fresh.
3. Visually inspect the *actual planned source windows* in portrait framing
   before queuing. `inspect-asset <id>` supplies a contact sheet, vertical
   preview, and playable MP4; inspect more than the first frame, especially
   the hook and planned thumbnail. Check the animal is visible, correctly
   identified, sharp, well framed, and not too close after the proposed crop
   and zoom. A blurred background or different crop cannot rescue an
   unusably tight or repetitive source. If suitable fresh footage is missing,
   stop and report `NEEDS_MEDIA`; do not burn a render to find out.
   The hook must open on a clean full-frame subject within the first second —
   check the actual first rendered frame, not just that zoom=1. A clipped
   face or body in the opening frame fails preflight.
4. Check narration against available story seconds, not total seconds
   including the end card. Estimate spoken duration conservatively from word
   count and punctuation; verify facts are semantically supported, not merely
   linked by ID. Shorten/revise before rendering if timing looks implausible.
   Never silently revise an approved/exported cut: make a new revision only
   when a replacement is intended and its implications are understood.

## After *every* render, before replying

Devin must inspect **each** MP4 produced in the current task, even when QC
fails. Do not rely only on `run` output, `review`, file existence, or a single
thumbnail. Use `ffprobe` and `review <plan_id>` for streams, duration, status,
blocking checks and QC; read `manifest.json`, `alignment.json` and captions.
Open the rendered video when playback is available. Otherwise extract and
actually view frames from the rendered MP4 at the hook, several segment
boundaries, middle, last wildlife shot, and end card; also inspect any
rendered thumbnail. Check portrait crop, subject visibility, visual variety,
repetition, black/frozen frames, captions and end-card legibility. Inspect
audio by listening when possible and checking narration length versus story
time, caption alignment, loudness and the outro. If playback/listening is not
available, say exactly which checks could not be performed; never claim
firsthand audiovisual review on metadata alone.

If inspection or QC fails, mark the candidate **not ready**, give the
specific reason, and avoid further heavy renders until the cause is fixed.
In the final response state the species, plan ID, artifact path, reused-source
check, QC status, what Devin actually inspected, and any remaining human
fact/rights/species/audio review. An operator must still approve the exact
revision and hash; Devin's inspection is not a substitute for that approval.
