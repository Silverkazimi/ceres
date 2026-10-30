# Ceres

Ceres is a local-first companion for Splinterlands Land and cards. It shows a live Land board, helps plan changes, and provides card collection and market tools. It runs on your computer by default; you choose when to connect a wallet and sign an action.

## What you can do

- View plots, workers, worksites and harvest information on the Land board. Pick workers and stage Land changes, then review them on the Changes signing screen.
- Open the wallet drawer to view balances, swap supported tokens and claim SPS.
- Use **My cards** at `/cards` to browse your collection with filters. Explore the **Market/shop**, add listings to a cart and review checkout; combine cards, find combinable sets, and review **My purchases**. Checkout signing depends on the server configuration: by default it is limited to the account selected with `--player`; `CERES_MARKET_BUY=on` enables it for everyone.
- Sign supported transactions with Hive Keychain or HiveAuth. Ceres does not hold your Hive private keys; HiveAuth session credentials are stored in your browser until expiry or disconnection. Ceres checks available game and chain results after signing. Some recovery paths need your attention or lose pending state on reload; see the signing limitations in [SECURITY.md](SECURITY.md).

## Your data and sign-in

**Without signing in:** Enter a Hive account name to view that player's Land board and cards. The optional `--player` command-line setting supplies a default account for API requests that omit one; it does not sign you in. You can stage changes and configure harvest routing, with those drafts saved only in this browser on this device until you sign in to sync them. Reading, updating or exporting the player's saved server space requires sign-in; executing game transactions requires a connected wallet and its signature, even when you are not signed in to Ceres.

**Login mode:** Choose **Sign in** in the account menu and use Hive Keychain or HiveAuth to sign a short login message with your Hive Posting key; this sends no transaction and costs nothing. Ceres syncs staged changes and routing setup with that player's saved space on the Ceres server you use, making them available when you sign in to the same account on that server from another device. Login also authorizes the saved-space export API. The session expires after 30 minutes without authenticated requests or 12 hours total, and a server restart ends it. With browser session storage available, login survives page refreshes in the same tab; closing the tab normally clears it. Choose **Sign out** in the account menu to end the login without deleting your drafts or saved server space.

Transaction signing is separate from login: each transaction still needs a wallet signature, and signing in does not bypass the server's action restrictions. The server stores saved-space data as readable JSON, so its operator can read it; your Hive private key stays in your wallet. Deleting saved server data requires a fresh Posting-key wallet signature, whether or not you are signed in. See [SECURITY.md](SECURITY.md) for details.

## Install and run

Requires Python 3.11 or newer (available as `python3`), Git and a browser. On Windows, use WSL. In a terminal:

```sh
git clone https://github.com/Silverkazimi/ceres.git
cd ceres
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-ceres.txt
python3 -m ceres
```

Open `http://127.0.0.1:27390/`. See [installation](docs/INSTALL.md) and [configuration](docs/CONFIGURATION.md) for more detail. Releases and their notes: [GitHub Releases](https://github.com/Silverkazimi/ceres/releases) and the [CHANGELOG](CHANGELOG.md).

## Artwork

Ceres fetches game artwork from the official image source when it is first needed. Land artwork is kept in `ceres/static/assets/` in the local checkout; card artwork is cached under `~/.ceres/card-art/` by default. Artwork belongs to its owners and is not covered by this project's licence; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Support

For bugs and ideas, open an issue in this repository; see [CONTRIBUTING.md](CONTRIBUTING.md). Report vulnerabilities through the private reporting channel described in [SECURITY.md](SECURITY.md).

## Security and licence

Ceres binds to your own machine by default. Read [SECURITY.md](SECURITY.md) before making it reachable from the internet. Ceres is licensed under MIT; see [LICENSE](LICENSE).
