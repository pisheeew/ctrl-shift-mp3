# Pinned, checksum-verified release supply chain; draft-only publishing

Every release input is pinned — the exact Python patch version, exact pip requirements, and an FFmpeg archive verified by SHA-256 at download time — and publishing is always a human-reviewed step: the workflow creates a draft release and never publishes. We chose reproducibility and reviewability over freshness; the known cost (dependency pins like `yt-dlp` go stale and must be bumped deliberately, see `docs/maintainers/releasing.md`) is accepted and handled with scheduled dependency updates rather than by loosening the pins.

## Considered options

- Continuous publishing on every green build — rejected: it removes the human review gate that catches notices, naming, and packaging mistakes before users see them.
- Unpinned ("latest") tooling — rejected: two builds of the same tag could then differ, which defeats "rebuilding the same version does not silently change its behavior".

## Consequences

- Transitive pip dependencies are still resolved (not hash-pinned) at build time; tightening that to `--require-hashes` is a recorded follow-up, not a change of direction.
