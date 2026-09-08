# A public personal project, shared with all rights reserved

The repository is public so the released bundle's users can read what they run — not to invite collaboration. ctrl-shift-mp3 is a personal project maintained by one person in their spare time; it is **not open source**. The code is shared as-is for reading and personal use, with no license grant to reuse, modify, or redistribute it, and no support, roadmap, or review capacity offered. External contributions are not part of the maintenance model. Private vulnerability reports remain welcome through GitHub's private reporting (see `SECURITY.md`).

We chose an honest record over community convention: a public repository wearing open-source scaffolding it cannot honor — an open license grant, a contributing guide, a PR flow — sets expectations the maintainer has explicitly retired, and silently disappointing contributors is worse than saying so up front. Bundled third-party software keeps its own obligations: the FFmpeg GPL notice duties for the shipped binaries are unaffected by this posture.

## Considered options

- Keep the MIT license and accept the implied contribution flow — rejected: it grants rights the maintainer does not intend to administer and invites a review load the project cannot sustain.
- Keep the repository private — rejected: users of the released bundle benefit from being able to inspect the source, and that does not require granting them anything.

## Consequences

- `LICENSE` will be replaced in place (same filename, so packaging needs no change) with an all-rights-reserved notice naming the project as copyright holder, and the README banner and license section will say the same thing; the current MIT text is retracted by this decision and its removal is the recorded follow-up (#36).
- The scaffolding that assumes external contributors — the contributing guide, Code of Conduct, issue templates, and pull-request template — is retired by this decision; its removal is likewise recorded as follow-up #36. Dependabot stays — it serves the maintainer's own pin-refresh cadence (see ADR-0002), not an external flow.
- Releases remain the only supported distribution channel; forks and clones exist without a license grant.
