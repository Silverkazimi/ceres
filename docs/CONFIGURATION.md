# Configuration

## Command line

`python3 -m ceres [options]`

| Option | Default | What it does |
|---|---|---|
| `--host` | `127.0.0.1` | Address to bind. Anything other than loopback logs a warning; read SECURITY first. |
| `--port` | `27390` | Port to bind. |
| `--player` | none | Optional default account for API calls that don't name one. Without it the page opens on its start page and such calls are refused (HTTP 422). The page itself always names the account it shows. |
| `--stale-after` | `60` | Seconds after which the board header marks the data as stale. |
| `--capture-concurrency` | `8` | Parallel requests for the optional full capture (not the normal page load). |
| `--log-level` | `info` | Log verbosity. |
| `--no-reload-watch` | off | Turns off the developer auto-reload (same as `CERES_DEV_WATCH=0`). |

## Environment variables

| Variable | Default | What it does |
|---|---|---|
| `CERES_CSP` | `report-only` | Content Security Policy mode for the page: `report-only` (violations are reported in the browser console, nothing is blocked), `enforce`, or `off`. |
| `CERES_ALLOWED_HOSTS` | unset | Comma-separated public host names a hosted deployment answers to. Without it a server bound to the loopback address answers only to 127.0.0.1, localhost and ::1; a server bound to another interface also answers to IP addresses, but not to names (other Host headers get 421). Not needed for a normal local install. |
| `CERES_STORED_DATA_CLEANUP` | unset | `1` turns on the start-up removal of old, unused cache files and a daily sweep that deletes an account's caches seven days after its last board read. Off by default: a local install deletes nothing it did not write. |
| `CERES_DEV_WATCH` | `1` | `0` turns off the auto-reload watcher and the browser's version poll. Set `0` in production. |
| `CERES_CAPTURE_MEMORY_MB` | `2048` | Memory cap for the worker that reads an account. Raise it for very large accounts. |
| `CERES_CARD_ART_CACHE` | `1` | `0` turns off the local card-art cache (art is then loaded straight from the game's image server). The cache lives under `~/.ceres/card-art/` on your machine. |
| `CERES_HARVEST_SIGNING` | `1` | `0` turns off harvest and tax signing. |
| `CERES_HARVEST_ROUTING_SIGNING` | `0` | `1` turns on signing for routing harvested resources. |
| `CERES_ROUTING_SALE_DEC` | `0` | `1` offers selling resources for DEC as a routing option. |
| `CERES_ROUTING_LP_WALLET` | `0` | `1` offers adding liquidity from wallet DEC as a routing option. |
| `CERES_ROUTING_LP_SELL_HALF` | `0` | `1` offers "sell half, then add to the pool" as a routing option. |
| `CERES_WALLET_LP_SIGNING` | `1` | `0` turns off signing for wallet swaps. |
| `CERES_WORKER_LOG_LEVEL` | `info` | Log verbosity of the worker that reads an account. |
| `CERES_CLIENT_LIMITS` | `on` | `off` disables the per-client rate limits on routes that call the game's API. |
| `CERES_PAUSED` | unset | `1` pauses the whole API (every `/api/*` route except health, dev-watch and csp-report answers 503) until unset and restarted. See "Operator pause" below for the file-based alternative, which needs no restart. |

Variables that start with `CERES_TEST_` or `CERES_CAPTURE_WORKER_` exist for the test suite and aren't
meant for normal use.

## Where data lives

`~/.ceres/`: logs (rotating), the latest snapshot per account (used only to answer follow-up requests
from the same page load, never to draw a page), the request journal, routing evidence and the card-art cache.
`~/.praetoria/cache/`: cached game reference data. `ceres/static/assets/` inside the checkout: artwork downloaded the first time it is needed.

## Operator pause

Create a file named `PAUSED` in the data directory (`~/.ceres/PAUSED` by default) to pause the API without
a restart; it is checked at most once a second. Delete the file to resume. This has the same effect as
setting `CERES_PAUSED=1`, except it needs no restart to take effect or to lift.
