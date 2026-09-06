# Credential and token storage in the user profile

Spotify **credentials** (client ID/secret, long-lived, user-supplied) are stored as plaintext JSON in the user's home directory alongside all other app state, and short-lived Spotify **access tokens** are kept only in process memory — never written to disk. We accepted plaintext-at-rest for v1 because it matches where every other state file already lives, is fully disclosed in the README, and is protected by the operating system's per-user boundaries; encrypting with DPAPI (or restrictive ACLs) is a deliberate follow-up, not an oversight.

## Consequences

- The token cache must never reintroduce a disk handler (a `CacheFileHandler`); a stray `.cache` file in the working directory was the failure mode that prompted this decision.
- Anything added to the home-directory state family inherits the README's documented-locations promise, so new state files need a README update.
