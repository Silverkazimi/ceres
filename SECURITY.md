# Security

## Keys and signing

- The server never receives, stores or logs a private key. Transactions are built on the server and
  signed in your browser by Hive Keychain or HiveAuth.
- Posting authority is used wherever the game accepts it. Operations that need Active authority are
  marked before you sign.
- Each signed transaction is kept exactly as signed and has a deterministic id. Ceres never re-broadcasts
  a transaction whose outcome is uncertain; it asks the chain and the game what happened instead.
- A HiveAuth session (its token and encryption key) is kept in the browser's local storage for that
  account until it expires or you press Disconnect, so a page reload doesn't ask you to scan again. Anyone
  with access to that browser profile can use the session until then. Ceres never sends it to the server.

## Input handling

Account names are validated against Hive's rules (3 to 16 characters, lowercase) before use. Everything
Ceres writes to disk stays under its own data directories (`~/.ceres/`, `~/.praetoria/cache/` and the checkout's `ceres/static/assets/` for downloaded artwork), and a request can't point it elsewhere.

## Response headers

Every response carries `X-Content-Type-Options: nosniff`,
`Referrer-Policy: strict-origin-when-cross-origin` and `X-Frame-Options: DENY`.

The page also carries a Content Security Policy, built from the page as served:

- scripts: the page's own origin, plus exact hashes of its few inline scripts and inline image-fallback
  handlers (no `unsafe-inline`, no `eval`);
- styles: own origin plus inline styles;
- images: own origin, `data:`, `blob:` and any HTTPS source (card art and avatars come from the game's
  image servers);
- connections: own origin, `https://api.splinterlands.com` (avatar settings),
  `https://api.hive.blog` (the signed-transaction queue's Hive node) and
  `wss://hive-auth.arcange.eu` (HiveAuth);
- no plugins, no `<base>` changes, no framing by other sites.

`CERES_CSP` sets the mode: `report-only` (current default), `enforce`, or `off`. The default will switch
to `enforce` once the policy has been confirmed with every supported wallet.

## Exposing Ceres to the internet

Ceres binds to loopback by default. Viewing any account needs no login, so anyone who can reach it can view any account and
start reads against the game's API on the server's behalf. An optional Posting-key login (below) only protects a player's own saved space. If you put it on a public address:

- put it behind a reverse proxy with TLS;
- be aware that a board read of a large account is heavy (memory and API calls). One board read runs at
  a time per server: the same client switching accounts or reloading still supersedes it instantly, but
  another client asking for the same account gets a `409` (it joins the running read instead of starting
  one) and another client asking for a different account gets a `429` with `Retry-After`;
- every route that would call the game's API is capped per client (minute- and hour-scale limits, answered
  with `429` and `Retry-After`, nothing upstream is spent on a refused request); set `CERES_CLIENT_LIMITS=off`
  to disable this, see CONFIGURATION;
- an operator can pause the whole API on the spot (env var or a file, see CONFIGURATION) — every route
  except health/dev-watch/csp-report then answers `503`, the running board read is stopped, and no new one
  can start until the pause is lifted. A transaction a visitor already signed in their own browser can still
  be broadcast by that browser; the pause doesn't reach it;
- Ceres trusts `X-Forwarded-For` only from a loopback peer (its own reverse proxy), so a reverse proxy on the
  same host is what makes per-client identification (and the limits above) meaningful; without one, every
  visitor behind a shared proxy would count as the same client.

## Optional login and saved space

Signing in is optional and only needed to keep your routing setup and staged changes on the server so they follow you across devices. Without it everything stays in your browser.

- Your wallet signs one short text with your Posting key (Keychain `requestSignBuffer`, or HiveAuth challenge). It is not a transaction and nothing is broadcast. The server checks the signature against the account's current public Posting authority read from a Hive node (multi-key thresholds and one level of delegated Posting authority are honoured; Active, Owner and Memo keys never count).
- A challenge is single-use, expires after five minutes, allows five attempts, is bound to the requesting browser and to the site's configured origin (`CERES_PUBLIC_ORIGIN`), and names the account.
- A session is a random bearer token kept only as a hash in server memory. The browser keeps its copy in this tab's session storage, so a reload keeps you signed in; closing the tab removes it. Sessions end after 30 minutes idle, 12 hours at most, on logout or on a server restart.
- Each player's documents (routing setup, staged changes) are private to that player: the account always comes from the session, never from a name in the request. A save must name the version it edited; a stale save is refused and never overwrites. The ten previous versions of each document are kept.
- Deleting saved data or cached data needs a fresh signature made for exactly that deletion; a login session alone is not enough. See the privacy page (`/static/privacy.html`) for everything the server keeps and for how long.
- On a hosted server (`CERES_REDACT_LOGS=1`) logs replace account names in the usual fields and messages, leave out IP addresses and query strings, and are kept seven days. Redaction works on known formats; treat logs as confidential, not anonymous.

## Known limitations: signing and duplicate transactions

Ceres never asks for your Hive private keys: your wallet signs, Ceres prepares the operations and checks what happened. Always check the account, amounts and destination in your wallet. A missing wallet answer is not proof that nothing was sent.

- **Duplicate guards live in this browser.** Pending swaps, transfers, liquidity changes, purchases and signed transactions are remembered in this browser profile. Another browser, another device, a private window or cleared site data does not see them, and signing in does not create a lock across devices.
- **A request whose answer got lost stays pending.** A swap, transfer, liquidity change or purchase whose wallet answer never arrived stays "Still pending" and cannot be repeated. It is released only when its transaction is shown not included after Hive's one-hour expiry; a late broadcast is adopted instead of signed again.
- **Not found counts only after about 65 minutes.** The inclusion check waits that long before it treats a known transaction as absent.
- **Some recovery paths still need you.** A regional DEC change or a plot lock can be dismissed while its outcome is unknown, and dismissing does not cancel a transaction. A reward claim without a transaction id can lose its pending state on a page reload. Check your wallet history before trying again.
- **Routing keeps only what it must.** A one-time routing run is temporary and is deleted when it ends, fails or the page is refreshed. If its transaction result is unknown, Ceres keeps only the transaction id, its expiry and the harvest receipts it spends; a later routing of the same harvest checks that one transaction and leaves those receipts out until it is settled or expired. A routing whose wallet never returned a transaction id is not covered.
- **Batches are checked before, not between, signatures.** The Changes flow rebuilds and compares every operation before the batch starts, but it does not re-check each later chunk or wait for each game result before asking for the next signature.
- **Buy & max rebuilds at Sign.** A prepared combine is rebuilt and compared with what you reviewed every time you press Sign; if the cards changed you see the new review and confirm again.

## Known limitations: login, saved space and browser security

- Anyone who holds the account's Posting authority can sign in as that account. Rotating the Posting key does not end a session that is already open; log out or wait for it to expire.
- Scripts and extensions with access to the page can read the session token and the HiveAuth session (token and encryption key, kept in local storage until it expires or you disconnect). These are not Hive private keys, but use a trusted browser profile.
- Saved routing setup and staged changes are private to your account through the API, but the server operator can read the files; they are not encrypted for you. Never put keys into saved data.
- A routing run is protected by a per-run token held by the page that created it. Anyone who obtains that token can operate that run until it ends, but cannot sign anything without your wallet.
- Ten previous versions of each saved document are kept. Deleting your data cannot remove copies in your browsers, your exports or the operator's own backups.
- HiveAuth sign-in has been tested with a real wallet; not every wallet and device combination has been.
- Cache cleanup is optional. When enabled, an account's caches are deleted seven days after its last board read; public reads keep a cache alive.
- The Content Security Policy is report-only by default, so it reports but does not block an injected script.

## Dependencies

Runtime dependencies are pinned in `requirements-ceres.txt` and checked with `pip-audit`. On 2026-10-20 the check reported PYSEC-2026-1325 (CVE-2024-23342, GHSA-wj6h-64fc-37mp) for the pinned ecdsa release. That advisory concerns timing leaks when signing, generating keys or doing ECDH and excludes signature verification. Ceres uses ecdsa only to verify login signatures with public keys (source review 2026-10-20), so the affected operations are not used. This will be re-checked if that use changes.

## Reporting a vulnerability

Please use the repository's private vulnerability reporting rather than a public issue.
