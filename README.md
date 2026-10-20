# Ceres

Ceres is a local-first companion for Splinterlands Land and cards. It shows a live Land board, helps plan changes, and provides card collection and market tools. It runs on your computer by default; you choose when to connect a wallet and sign an action.

## What you can do

- View plots, workers, worksites and harvest information on the Land board. Pick workers and stage Land changes, then review them on the Changes signing screen.
- Open the wallet drawer to view balances, swap supported tokens and claim SPS.
- Use **My cards** at `/cards` to browse your collection with filters. Explore the **Market/shop**, add listings to a cart and review checkout; combine cards, find combinable sets, and review **My purchases**.
- Sign supported transactions with Hive Keychain or HiveAuth. Ceres does not hold your keys. Each action is checked for its result after signing; if the result is unclear, Ceres keeps it unresolved instead of treating it as confirmed.

## Your data and sign-in

Ceres works without an account login. In this local-first mode, staged changes and routing setup are saved in your browser on that device.

You can optionally sign in with your Hive Posting key. This signs a short login message, not a transaction. Ceres then syncs staged changes and routing setup to a saved space for that player on the Ceres server you use, so they can follow you between devices. That space is not encrypted for you: the server operator can read it (it never holds keys). The server never receives your private key. See [SECURITY.md](SECURITY.md) for details.

## Install and run

Requires Python 3.11 or newer and a browser. On Windows, use WSL. In a terminal:

```sh
git clone https://github.com/Silverkazimi/ceres.git
cd ceres
python3 -m venv .venv
. .venv/bin/activate
python3 -m pip install -r requirements-ceres.txt
python3 -m ceres
```

Open `http://127.0.0.1:27390/`. See [installation](docs/INSTALL.md) and [configuration](docs/CONFIGURATION.md) for more detail. Ceres is at **1.0.0 release candidate**; the final 1.0.0 release will follow once all tests are green.

## Artwork

Ceres fetches the game artwork it needs from the official image source on first run and keeps it in the local checkout. Artwork belongs to its owners and is not covered by this project's licence; see [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Security and licence

Ceres binds to your own machine by default. Read [SECURITY.md](SECURITY.md) before making it reachable from the internet. Ceres is licensed under MIT; see [LICENSE](LICENSE).
