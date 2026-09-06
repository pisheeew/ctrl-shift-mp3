# Domain Docs

How AI coding agents working in this repo should consume its domain documentation. Like [issue-tracker.md](issue-tracker.md), this is a working document of the repo's AI-assisted development process, kept in the open.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root: the domain glossary — Playlist, Track, Match, Download, Run, Credential, Access token — including the synonyms each term avoids.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in. The current set:
  - [`0001-local-only-unauthenticated-server.md`](../adr/0001-local-only-unauthenticated-server.md)
  - [`0002-pinned-release-supply-chain.md`](../adr/0002-pinned-release-supply-chain.md)
  - [`0003-credential-and-token-storage.md`](../adr/0003-credential-and-token-storage.md)

New glossary terms and ADRs are added lazily, when a term or decision actually gets resolved in the maintainer's domain-modeling workflow. Don't create them upfront on speculation, and don't flag the docs' brevity.

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids: the app downloads a **Track** via its **Match**, never a "candidate result"; users supply **Credentials**, never an "API key".

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it in the issue you're working from, so the term gets modeled when it settles).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0001 (local-only, unauthenticated server), but worth reopening because…_
