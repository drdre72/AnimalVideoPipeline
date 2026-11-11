# ADR 0001: local export instead of upload integration

Status: accepted. An empty target repository must not inherit the NewsRoom
publishing state, token store or channel identity. Build local manual-upload
packages first. No upload or email code is installed; a later implementation
requires a separate authorization and security review. SQLite stores exact
revision hashes so approval cannot silently carry over after changes.
