# Operations

Run `uv sync --extra dev`, then `uv run python main.py doctor`. FFmpeg and
FFprobe are required. For production voice, install `uv sync --extra tts`,
choose and approve a voice in `config/settings.yaml`, run Ollama locally
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

Current limits: operator-assisted topic entry and evidence approval, no remote
research/discovery or footage download, no resident TTS worker, no local
preview UI or launchd installation, no automatic publication, and no
source-specific platform API analytics. Performance imports are local JSON.
Voice and Metal inference performance have not been validated in this project.
Disk availability and current hardware status appear in `doctor`.
