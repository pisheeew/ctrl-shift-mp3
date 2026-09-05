# Security Policy

## Reporting a vulnerability

Please do **not** open a public issue for a security problem.

Use GitHub's **private vulnerability reporting** for this repository:

1. Go to the **Security** tab of this repository.
2. Under **Reporting**, click **Report a vulnerability**.
3. Describe the issue, how to reproduce it, and the impact.

This keeps the details private until a fix is ready. There is no separate
security email; reports submitted anywhere else publicly may take longer to
act on and may expose users before a fix exists.

## What to include

- The version you are running (the ZIP release version, or the commit if you
  run from source).
- Steps to reproduce, or a proof of concept.
- What you expected to happen and what happened instead.
- Any relevant log output from `~/.playlist_downloader.log`.

## Scope

This project is a local, unauthenticated server bound to `127.0.0.1`. Reports
that require a hostile machine, a compromised user account, or physical access
are still worth describing, but note the intended deployment in your report.

## Supported versions

Security fixes are made against the latest tagged release. Only the most
recent release line is supported.

## Response expectations

Maintainers acknowledge reports as soon as practical, work on a fix privately,
and publish the fix with a release. You will be credited in the advisory
unless you ask to remain anonymous.
