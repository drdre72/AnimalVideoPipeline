# Google / YouTube setup and credential migration

NAMINAL's YouTube connector is **read-only** (Data API + Analytics API +
authorized public competitor metadata). Upload transport exists only as a
dormant adapter and cannot activate in this phase.

## Threat model

- The dedicated token store is `secrets/google.json` (mode `600`,
  gitignored). It is **not** `.env` and is not shared with NewsRoom.
- `youtube migrate` copies only `client_id`, `client_secret` and
  `project_id` plus migration metadata from a Google OAuth client JSON.
  It refuses tokens, channel IDs, redirect URIs and any non-allowlisted
  key. NewsRoom's `NEWSROOM_YT_REFRESH_TOKEN` is never read.
- OAuth requests **read-only** scopes only (`youtube.readonly`,
  `yt-analytics.readonly`). No `youtube.upload` scope is ever requested.
- Tokens are never logged; errors are redacted before surfacing.

## Steps

1. **Get the OAuth client JSON** from Google Cloud Console (APIs &
   Services → Credentials → OAuth 2.0 Client IDs → download). A
   Desktop/installed-app client is expected — the same Cloud project used
   by NewsRoom may be reused after verifying it in step 3.

2. **Migrate the allowlisted config:**

   ```bash
   python main.py youtube migrate /path/to/client_secret_*.json
   ```

   Inspect the result — it should report exactly three imported keys.
   `secrets/google.json` is created mode `600` and stays untracked.

3. **Enable APIs** in the same Cloud project: YouTube Data API v3 and
   YouTube Analytics API. Verify the OAuth consent screen covers the
   Google account that owns the target NAMINAL channel.

4. **Authorize the NAMINAL channel:**

   ```bash
   # set first so a wrong-account grant fails closed:
   #   config/settings.yaml -> youtube.expected_channel: "Naminal Hub"
   python main.py youtube authorize --channel "Naminal Hub"
   ```

   This runs a PKCE loopback flow on `127.0.0.1`, stores the refresh token
   in `secrets/google.json`, fetches the channel ID + title and persists
   them. If the authorized channel's title does not match
   `expected_channel` the authorization is **rejected and discarded** —
   the token is not kept.

5. **Verify:**

   ```bash
   python main.py youtube status
   # expect: client_configured=true authorized=true identity_matches=true
   #         upload_enabled=false
   python main.py youtube ingest        # first analytics snapshot
   python main.py youtube competitors   # Snugbarn + tracked public channels
   ```

`python main.py doctor` reports YouTube under OPTIONAL — absence of
authorization never blocks production.

## Identity guarantees

- Every API call re-verifies the stored channel identity against
  `youtube.expected_channel` before use; a mismatch raises and no request
  is sent.
- The dormant upload adapter requires **both** `publishing.uploads_enabled:
  true` (a separate deliberate step) **and** a verified matching channel —
  credential migration, analytics import and approvals are never
  authorization to publish.
- Analytics snapshots deduplicate on `(video_id, metrics, observed_at
  bucket)`; re-ingesting is idempotent. Unsupported fields (retention
  where unavailable, subscriber conversions) are stored `null`, never
  fabricated.
