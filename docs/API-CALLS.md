# What Ceres calls, and how often

> **Draft, source-verified 2026-09-25, line numbers rechecked 2026-09-27 against current HEAD.** Every row
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
  (`praetoria/land_analysis.py:74`, `ceres/routes_harvest.py:38`, `ceres/land_activity.py:28`).
- Only one account read (board load) runs at a time per server. The same client superseding it is instant;
  another client asking for the same account gets 409 and joins the read instead of starting one; another
  client asking for a different account gets 429 (`ceres/client_limits.py`, `capture_gate`).
- Every route that would call upstream is also capped per client (429 with `Retry-After`, `CERES_CLIENT_LIMITS`
  to disable) — see `ceres/client_limits.py`.
- Routing runs have their own persisted call ceiling, computed from the compiled plan (see "Resource routing" below).

---

## Board

### Open or refresh a board: `GET /api/board`, `POST /api/capture/refresh`

Runs `LandCapture.capture_quick` (`praetoria/land_analysis.py:1545`) in a worker process.

| Call | Code | Per load |
|---|---|---:|
| Land `GET /land/deeds?player=<account>`: plots, worksites and staking in one bundle | `land_analysis.py:1657` | 1 |
| Main `GET /cards/collection/<account>`: whole collection, no pagination | `praetoria/api.py:36` | 1 |
| Land `GET /land/stake/items/<type>/available`: unassigned totems, titles and power cores, 100 per page | `land_analysis.py:2035`, families at `:118`, page size `:131` | 3 + one per extra 100 items |
| Main `GET /players/item_details`: item catalogue (static item definitions; reused for up to 24 h, so a changed upstream definition can take that long to show) | `land_analysis.py:2073` | 0–1 |
| Main `GET /cards/get_details`: card stats catalogue | `ceres/capture.py:1251` | 0–1 (kept for a day) |
| Main `GET /players/balances?username=<account>` | `capture.py:1182` | 1 |
| Land `GET /land/stake/deeds/<plot>/assets`: re-read of plots whose earlier asset read failed | `capture.py:2470-2491`, cap `:2348` | 0–8 |

### Open one plot: `POST /api/plots/<plot>/expand`

`LandCapture.expand_deed` (`land_analysis.py:1876`):

| Call | Code | Per open |
|---|---|---:|
| Land `GET /land/deeds/<plot number>` | `:1895` | 1 |
| Land `GET /land/projects/deed/<plot>/active` | `:1897` | 1 |
| Land `GET /land/stake/deed/details/<plot>` | `:1898` | 1 |
| Land `GET /land/stake/deeds/<plot>/assets` | `:1908` | 1 |

### Worker picker: `GET /api/plots/<plot>/candidates`

No call when the board's card pool is available. If the card pool was too large to keep, one plot open (four
reads above) plus one collection read (`ceres/routes_candidates.py:151`).

### Avatar (browser)

Main `GET /players/player_avatar/<account>`, once per account shown in the header (`ceres/static/avatar.js:78`,
called from `ceres/static/board.js:786`; not repeated while the same account stays open).

---

## Harvest and taxes

| Action | Calls | Code |
|---|---|---|
| Plot harvest box (`GET /api/plots/<plot>/harvest`) | Land `…/projects/deed/<plot>/active` 1, Land `/land/liquidity/region/<account>` 1. Each is reused for 10 s unless `fresh=1`. | `ceres/routes_harvest.py:141-161`, cache `:37` |
| Keep/Castle taxes (`GET /api/plots/<plot>/taxes`) | Land `/land/resources/taxes/<plot>` 1, up to 2 attempts | `routes_harvest.py:179-191`, `:52` |
| Harvest preview / preflight, by region | Land `/land/deeds/owned/<account>` 1 (all regions only), `/land/liquidity/region/<account>` 1, `/land/resources/production/region/harvestable` 1 per region. Up to 2 attempts each. | `routes_harvest.py:264-286`, `:49` |
| Harvest preview / preflight, by plot | `/land/liquidity/region/<account>` 1, `…/active` 1 per plot, taxes 1 per Keep/Castle | `routes_harvest.py:317-356` |
| After signing (`POST /api/harvest/verify`) | Main `/transactions/lookup` 1 per operation; Land `/land/resources/fragment_history/<tx>` ≤1 per accepted member; Land `/land/resources/history/<tx>` ≤1 per member without direct amounts; Land `/land/resources/rewardactions/<plot>?limit=5` 1 per plot needing per-plot amounts. Lookups and fragment reads are metered (25 per action). | `routes_harvest.py:665-785`; one lookup per operation in `fides/game_verdict.py:110` |

---

## Staged-operation preview

Every preview of a staged change (`POST /api/op-preview`, `/api/plots/<plot>/op-preview`, `/api/plots/<plot>/worksite-preview`, `ceres/routes_op_preview.py`) re-reads each touched plot live from the game, in memory only: Land `/land/stake/deeds/<plot>/assets` and the plot's deed read, once per touched plot; a failed read answers a coded 503 and no stored data stands in for it.

---

## Optional sign-in (saved space)

Only when the player presses "Save across devices" (never automatically): the browser asks the server for a challenge text, the player signs it with a Posting key in their wallet (no transaction, nothing broadcast), and the server reads the account's public Posting authority from the Hive node: one `condenser_api.get_accounts` for the account and, if it delegates Posting authority, one more for the delegates (at most eight; `ceres/hive_authority.py`). Challenges are limited to 5 per minute and 20 per hour per client (`ceres/client_limits.py`, bucket `auth`). No history is read.

---

## Wallet, DEC and rewards

| Action | Calls | Code |
|---|---|---|
| Open wallet (`GET /api/wallet`) | In parallel, one attempt each: Main `/players/balances` 1, Land `/land/liquidity/region/<account>` 1, Land `/land/liquidity/pools` 1, Land `/land/liquidity/allrewards` 1, Land `/land/stake/dec/overall` 1, Land `/land/stake/decstaked` 1 | `ceres/routes_wallet.py:316-329` |
| Claimable SPS (`GET /api/wallet/rewards`) | Main `/players/unclaimed_balances?token_type=SPS` 1 | `routes_wallet.py:382-389` |
| Claim review (`POST /api/wallet/claim/preview`) | Main `/players/unclaimed_balances` 1, Main `/settings` 1 | `routes_wallet.py:446-461` |
| Claim check (`POST /api/wallet/claim/verify`) | Main `/transactions/lookup` 1 per press | `routes_wallet.py:465-486` |
| Regional DEC (`GET /api/dec`) | Land `/land/deeds/owned/<account>` 1, Land `/land/stake/decstaked` 1, Main `/players/balances` 1, then Land `/land/stake/dec/region` 1 per region with plots or stake (at most 150, 4 at once) | `ceres/routes_dec.py:69-95` |
| DEC stake/unstake preview (`POST /api/dec/preview`) | Land `/land/stake/dec/region` 1, Main `/players/balances` 1. The page sends at most one preview every 2 s. | `routes_dec.py:113-125`; page pacing `ceres/static/regional-dec.js:55-56` |
| DEC check (`POST /api/dec/verify`) | Main `/transactions/lookup` 1 per press | `routes_dec.py:201-222` |

Wallet pool swaps (`ceres/routes_wallet_lp.py`, per-client `wallet_lp` limit): a typed quote reads Land
`/land/liquidity/pools` once; preview and sign read pools, account balances and regional balances (concurrent, same-player
single flight; pools 15 s timeout, balances 10 s, one attempt each); verify and SPS funding each do one transaction lookup;
settle does one lookup and, when eligible, bridge discovery (≤101 history rows / 512 KiB per history call, then the matching
transaction confirmation). Plot-lock verification calls Main `/transactions/lookup` once per press (`ceres/routes_plot_lock.py`).

---

## Signing and checking results

| Action | Calls | Code |
|---|---|---|
| Queue a signed transaction (browser) | Hive `condenser_api.get_dynamic_global_properties` 1 (reused for 30 s within a routing run) | `ceres/static/transaction-queue.js:152`, `:9` |
| Broadcast (browser) | Hive `get_dynamic_global_properties` 1 + `condenser_api.broadcast_transaction` 1 per signed transaction. Never re-sent when the outcome is uncertain. | `transaction-queue.js:287-295` |
| Waiting for inclusion (browser) | Hive `condenser_api.get_transaction` about once a second per unresolved transaction, until it is included or its 10-minute window ends | `transaction-queue.js:245`, `:258-278`, expiry `:8` |
| Check a change (`POST /api/op-verify`) | Hive `condenser_api.get_transaction` 1; Main `/transactions/lookup` 1 per operation; for stake and worksite changes, 1 fresh read per affected plot (its assets, or the account bundle for worksite facts). Once after signing, then once per "Check again". | `ceres/routes_op_preview.py:394-500`, `ceres/capture.py:2905-2916`; page `ceres/static/operations.js:1282` |
| Pending wallet request check (`POST /api/op-inclusion`) | Hive `account_history_api.get_account_history` (limit 1000) 1 per check, only after a settle window. The page checks at most 10 times, 0.5 s apart, while waiting for room in Hive's per-block limit. | `routes_op_preview.py:302-349`; page `ceres/static/broadcast-gate.js:370-398` |
| Combine review / build | Main `/cards/collection/<account>` 1 on build | `ceres/routes_combine.py:209-265` |

---

## Resource routing (off by default)

The routing screens read Land `/land/liquidity/pools` 1, Main `/settings` 1, Main `/players/balances` 1 and
the harvest region reads above per preview (`ceres/routes_harvest_routing.py:47-89`). A routing run is
metered before every call and refuses anything past its ceiling. The ceiling is computed from the compiled plan
(`ceres/routing_plan.py`) and stored with the run, so it grows with the number of operations; per-kind
allowances include game verdicts, Hive probes, Hive head reads, liquidity context, bridge history and manual retries
(`ceres/routing_runs_v2.py:222`; bridge history `ceres/harvest_routing_bridge.py:24`).

---

## Present in the code but never called

`LandClient.project_history` (`/land/projects/deed/<plot>/list`, `land_analysis.py:518`) and
`LandClient.stake_evp_pending` (`/land/stake/evp/pending-claim`, `:590`) have no caller.
