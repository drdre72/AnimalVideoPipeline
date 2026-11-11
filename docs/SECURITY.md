# Phase Zero: rights and security threat model

Untrusted scientific excerpts, model text, metadata and media paths can
inject instructions or malformed input. Treat all as data. Never run
generated shell commands; FFmpeg uses bounded argv calls. Only local files
with recorded hashes enter renders. The opt-in Commons importer pins the
official API and upload hosts (including redirects), verifies HTTPS and
applies separate metadata and video byte ceilings. It rejects unrecognized
or restricted licenses and leaves imported assets unapproved. Never log
OAuth credentials or private configuration; keep them out of this repository.

Threats: unsupported science, fraudulent/expired licenses, species mismatch,
misleading cropping, synthetic wildlife misrepresented as real, stale
approvals, duplicate jobs, disk/memory exhaustion, unauthorized publishing
and prompt injection via external content. Mitigations: approved cited
evidence and independent editorial review, explicit per-asset rights
records, FFprobe and file hashes, revision-bound QC and approvals, SQLite
constraints, serial heavy operations and no publishing transport.

Manual attestation is not independent proof of ownership: a reviewer must
inspect source and rights evidence. A citation ID does not establish semantic
entailment; the reviewer must verify that script wording follows its excerpt.
