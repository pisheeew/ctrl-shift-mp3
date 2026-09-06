# Credential and token storage in the user profile

Spotify **credentials** (client ID/secret, long-lived, user-supplied) are stored in a JSON file in the user's home directory alongside all other app state, and short-lived Spotify **access tokens** are kept only in process memory — never written to disk. We accepted plaintext-at-rest for v1 because it matches where every other state file already lives, is fully disclosed in the README, and is protected by the operating system's per-user boundaries.

**Follow-up (recorded as accepted):** the v1 plaintext-at-rest trade-off has since been closed with **DPAPI encryption** of the secret-bearing config fields (`spotify_client_id`, `spotify_client_secret`) on Windows. We chose DPAPI over a restrictive ACL because it is the stronger of the two options named for this follow-up — a copy of the file taken to another machine or another user's profile is unreadable — and because it needs no new dependency (`ctypes` against `crypt32` only). Encrypted values carry a `dpapi:` prefix; plaintext configs written by prior versions still load and are encrypted on their next save, and an undecryptable value is treated as unset rather than failing startup. Non-Windows runs keep the historical plaintext behavior, with `chmod 600` applied to the credentials file on POSIX. In-memory config always holds plaintext, so the browser never receives the secret (SEC-005 masking) and the token cache stays out of scope.

## Consequences

- The token cache must never reintroduce a disk handler (a `CacheFileHandler`); a stray `.cache` file in the working directory was the failure mode that prompted this decision.
- Anything added to the home-directory state family inherits the README's documented-locations promise, so new state files need a README update.
- New config fields holding secrets must be added to `SECRET_CONFIG_FIELDS` in `server.py`, or they will be written to disk unencrypted.
