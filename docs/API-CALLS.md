# What Ceres calls, and how often

> **Draft, source-verified 2026-10-30, line numbers rechecked 2026-10-30 against current HEAD.** Every row
> names the code that makes the call (`file:line`). "Attempts" means total tries per call, including the
> first. Nothing here is a measured rate; it is what the code allows. `ceres/client_limits.py` makes no
> outbound call of its own; it only decides whether a route may make its own.

Ceres talks to these services (the first three are game and Hive APIs; the last two are artwork and the SPS validator):

| Service | Address | Used for |
|---|---|---|
| Splinterlands Land API | `https://vapi.splinterlands.com` | plots, worksites, staking, resources, liquidity |
| Splinterlands main API | `https://api.splinterlands.com` | cards, balances, settings, transaction results |
| Hive API node | `https://api.hive.blog` | account history, transaction checks, and (from the browser) broadcasting signed transactions |
| Splinterlands validator API | `https://splinterlands-validator-api.splinterlands.com` | `GET /balances?account=…` (routing inventory, SPS balance) and `GET /transaction?id=…` (SPS funding, recovery and bridge confirmation); one attempt per read, 15 s socket timeout, 256 KiB body ceiling (`ceres/harvest_routing_validator.py`) |
| Game artwork host | `https://d36mxiodymuqjm.cloudfront.net` | one GET per missing local image on first use (10 s timeout, 5 MiB limit, redirects refused, SHA-256 checked, two workers, failures suppressed for 60 s; `ceres/asset_sources.py`), plus card art (≤3 candidate URLs, 2 s each, 12 MiB limit; `ceres/card_art_cache.py`). No account data is sent. An image already on disk costs zero calls. |

The browser also loads images from the game's image servers and, while a HiveAuth session is connected,
keeps a WebSocket to the HiveAuth relay (`wss://hive-auth.arcange.eu`).

**Shared limits.**

- Game reads on the capture and harvest dispatchers are spaced at least 0.3 s apart (this pacing is local to those dispatchers, not global; wallet, DEC and LP reads run in parallel, and transport retries (default 3, configurable 1–5) add attempts, so call counts below are logical calls, not strict HTTP request counts)
  (`praetoria/land_analysis.py:75`, `ceres/routes_harvest.py:42`, `ceres/game_read.py:18`).
- Only one account read (board load) runs at a time per server. The same client superseding it is instant;
  another client asking for the same account gets 409 and joins the read instead of starting one; another
  client asking for a different account gets 429 (`ceres/client_limits.py`, `capture_gate`).
- Every route that would call upstream is also capped per client (429 with `Retry-After`, `CERES_CLIENT_LIMITS`
  to disable) — see `ceres/client_limits.py`.
- Routing runs have their own call ceiling, held with the in-memory run and computed from the compiled plan (see "Resource routing" below).

---

## Board

### Open or refresh a board: `GET /api/board`, `POST /api/capture/refresh`

Runs `LandCapture.capture_quick` (`praetoria/land_analysis.py:1553`) in a worker process.

| Call | Code | Per load |
|---|---|---:|
| Land `GET /land/deeds?player=<account>`: plots, worksites and staking in one bundle | `land_analysis.py:1666` | 1 |
| Main `GET /cards/collection/<account>`: whole collection, no pagination | `praetoria/api.py:36` | 1 |
| Land `GET /land/stake/items/<type>/available`: unassigned totems, titles and power cores, 100 per page | `land_analysis.py:2066`, families at `:119`, page size `:132` | 3 + one per extra 100 items |
| Main `GET /players/item_details`: item catalogue (static item definitions; reused for up to 24 h, so a changed upstream definition can take that long to show) | `land_analysis.py:2085` | 0–1 |
| Main `GET /cards/get_details`: card stats catalogue | `praetoria/land_analysis.py:1382` | 0–1 (kept for a day) |
| Main `GET /players/balances?username=<account>` | `capture.py:1213` | 1 |
| Land `GET /land/stake/deeds/<plot>/assets`: re-read of plots whose earlier asset read failed | `capture.py:2468-2481`, cap `:2338` | 0–8 |

### Open one plot: `POST /api/plots/<plot>/expand`

`LandCapture.expand_deed` (`land_analysis.py:1883`):

| Call | Code | Per open |
|---|---|---:|
| Land `GET /land/deeds/<plot number>` | `:1902` | 1 |
| Land `GET /land/projects/deed/<plot>/active` | `:1905` | 1 |
| Land `GET /land/stake/deed/details/<plot>` | `:1906` | 1 |
| Land `GET /land/stake/deeds/<plot>/assets` | `:1920` | 1 |

### Worker picker: `GET /api/plots/<plot>/candidates`

No call when the board has the card pool and this plot's worker slots. If the slots are missing, the four plot reads above (`ceres/routes_candidates.py:148`). If the card pool was too large to keep, the account bundle, the plot's assets and one collection read (`ceres/routes_candidates.py:214`).

### Avatar (browser)

Main `GET /players/player_avatar/<account>`, once per account shown in the board header or on the Cards page, up to 3 tries on failure (`ceres/static/avatar.js:78`,
called from `ceres/static/board.js:918` and `ceres/static/card-browser.js:591`; not repeated while the same account stays open).

---

## Harvest and taxes

| Action | Calls | Code |
|---|---|---|
| Plot harvest box (`GET /api/plots/<plot>/harvest`) | Land `…/projects/deed/<plot>/active` 1, Land `/land/liquidity/region/<account>` 1. Each is reused for 10 s unless `fresh=1`. | `ceres/routes_harvest.py:145-166`, cache `:41` |
| Keep/Castle taxes (`GET /api/plots/<plot>/taxes`) | Land `/land/resources/taxes/<plot>` 1, up to 2 attempts | `routes_harvest.py:186-199`, `:55` |
| Harvest preview / preflight, by region | Land `/land/deeds/owned/<account>` 1 (all regions only), `/land/liquidity/region/<account>` 1, `/land/resources/production/region/harvestable` 1 per region. Up to 2 attempts each. | `routes_harvest.py:272-298`, `:52` |
| Harvest preview / preflight, by plot | `/land/liquidity/region/<account>` 1, `…/active` 1 per plot, taxes 1 per Keep/Castle | `routes_harvest.py:329-368` |
| After signing (`POST /api/harvest/verify`) | Main `/transactions/lookup` 1 per operation; Land `/land/resources/fragment_history/<tx>` ≤1 per accepted member; Land `/land/resources/history/<tx>` ≤1 per member without direct amounts; Land `/land/resources/rewardactions/<plot>?limit=5` 1 per plot needing per-plot amounts. Lookups and fragment reads are metered (25 per action). | `routes_harvest.py:675-815`; one lookup per operation in `fides/game_verdict.py:171-175` |

---

## Staged-operation preview

Every preview of a staged change (`POST /api/op-preview`, `/api/plots/<plot>/op-preview`, `/api/plots/<plot>/worksite-preview`, `ceres/routes_op_preview.py`) re-reads each touched plot live from the game, in memory only: the four plot reads (deed, active project, staking details, assets) once per touched plot, at most 4 plots at once (`ceres/capture.py:2787-2824`); a failed read answers a coded 503 and no stored data stands in for it.

---

## Optional sign-in (saved space)

Only when the player presses "Save across devices" (never automatically): the browser asks the server for a challenge text, the player signs it with a Posting key in their wallet (no transaction, nothing broadcast), and the server reads the account's public Posting authority from the Hive node: one `condenser_api.get_accounts` for the account and, if it delegates Posting authority, one more for the delegates (at most eight; `ceres/hive_authority.py`). Sign-in challenges, proofs and `/api/me` share a limit of 5 per minute and 20 per hour per client (`ceres/client_limits.py`, bucket `auth`). No history is read.

---

## Wallet, DEC and rewards

| Action | Calls | Code |
|---|---|---|
| Open wallet (`GET /api/wallet`) | In parallel, one attempt each: Main `/players/balances` 1, Land `/land/liquidity/region/<account>` 1, Land `/land/liquidity/pools` 1, Land `/land/stake/dec/overall` 1, Land `/land/stake/decstaked` 1 | `ceres/routes_wallet.py:261-272` |
| Claimable SPS (`GET /api/wallet/rewards`) | Main `/players/unclaimed_balances?token_type=SPS` 1, Main `/settings` 0–1 (only when a last claim date exists) | `routes_wallet.py:341-352` |
| Claim review (`POST /api/wallet/claim/preview`) | Main `/players/unclaimed_balances` 1, Main `/settings` 1 | `routes_wallet.py:411-428` |
| Claim check (`POST /api/wallet/claim/verify`) | Main `/transactions/lookup` 1 per press | `routes_wallet.py:431-455` |
| Regional DEC (`GET /api/dec`) | Land `/land/deeds/owned/<account>` 1, Land `/land/stake/decstaked` 1, Main `/players/balances` 1, then Land `/land/stake/dec/region` 1 per region with plots or stake (at most 150, 4 at once) | `ceres/routes_dec.py:71-95` |
| DEC stake/unstake preview (`POST /api/dec/preview`) | Land `/land/stake/dec/region` 1, Main `/players/balances` 1. The page sends at most one preview every 2 s. | `routes_dec.py:116-129`; page pacing `ceres/static/regional-dec.js:68` |
| DEC check (`POST /api/dec/verify`) | Main `/transactions/lookup` 1 per press | `routes_dec.py:206-227` |

Wallet pool swaps (`ceres/routes_wallet_lp.py`, per-client `wallet_lp` limit): a typed quote reads Land
`/land/liquidity/pools` once; preview and sign read pools, account balances and regional balances (concurrent, same-player
single flight; pools 15 s timeout, balances 10 s, one attempt each); verify and SPS funding each do one transaction lookup;
settle does one lookup and, when eligible, bridge discovery (≤101 history rows / 512 KiB per history call, then the matching
transaction confirmation). Plot-lock verification calls Main `/transactions/lookup` once per press (`ceres/routes_plot_lock.py`).

---

## Signing and checking results

| Action | Calls | Code |
|---|---|---|
| Queue a signed transaction (browser) | Hive `condenser_api.get_dynamic_global_properties` 1 (reused for 30 s within a routing run) | `ceres/static/transaction-queue.js:151`, `:9` |
| Broadcast (browser) | Hive `get_dynamic_global_properties` 1 + `condenser_api.broadcast_transaction` 1 per signed transaction. Never re-sent when the outcome is uncertain. | `transaction-queue.js:286-294` |
| Waiting for inclusion (browser) | Hive `condenser_api.get_transaction` about once a second per unresolved transaction, until it is included or its 10-minute window ends | `transaction-queue.js:244`, `:259-274`, expiry `:8` |
| Check a change (`POST /api/op-verify`) | Hive `condenser_api.get_transaction` 1; Main `/transactions/lookup` 1 per operation; for stake and worksite changes, 1 fresh read per affected plot (its assets, or the account bundle for worksite facts). Once after signing, then once per "Check again". | `ceres/routes_op_preview.py:392-519`, `ceres/capture.py:180-193`; page `ceres/static/operations.js:1299` |
| Pending wallet request check (`POST /api/op-inclusion`) | Hive `condenser_api.get_transaction` 1 per check when the request has a transaction id; none without one (no history read). The page checks at most 30 times, 1 s apart, while waiting for room in Hive's per-block limit, and re-checks every 45 s while anything is unresolved. | `routes_op_preview.py:355-370`; page `ceres/static/broadcast-gate.js:375-395`, `:284` |
| Combine review / build | Main `/cards/collection/<account>` 1 on build | `ceres/routes_combine.py:222-240` |

---

## Resource routing (off by default)

The routing screens read Land `/land/liquidity/pools` 1, Main `/settings` 1, Main `/players/balances` 1 and
the harvest region reads above per preview (`ceres/routes_harvest_routing.py:59-82`). A routing run is
metered before every call and refuses anything past its ceiling. The ceiling is computed from the compiled plan
(`ceres/routing_plan.py`) and stored with the run, so it grows with the number of operations; per-kind
allowances include game verdicts, Hive probes, Hive head reads, liquidity context, bridge history and manual retries
(`ceres/routing_runs_v2.py:40-42`, ceiling `ceres/routing_plan.py:37-47`; bridge history `ceres/harvest_routing_bridge.py:25`).

---

## Present in the code but never called

`LandClient.project_history` (`/land/projects/deed/<plot>/list`, `land_analysis.py:520`) and
`LandClient.stake_evp_pending` (`/land/stake/evp/pending-claim`, `:592`) have no caller.
