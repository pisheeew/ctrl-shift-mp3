# Local-only, unauthenticated server

The app serves its API and UI over plain HTTP bound to `127.0.0.1` only, with no authentication, by design: it is a single-user desktop tool, so the trust boundary is the local machine, not the network. Unauthenticated reachability from the browser is mitigated rather than solved: every request is checked for a same-origin `Origin` header and an allowlisted `Host` (`127.0.0.1` / `localhost`, any port, since the app may bind a fallback port; a request with no `Host` at all is also rejected), which blocks drive-by CSRF and DNS-rebinding pages. Adding real auth would force credential setup onto every launch for no threat-model gain; if the threat model ever changes, this ADR is the one to revisit.

## Consequences

- The README must keep stating that the app is for trusted machines/networks only.
- Any new route inherits the origin/host checks automatically (enforced in one `before_request`), so a contributor cannot accidentally expose an unprotected endpoint without touching the check itself.
