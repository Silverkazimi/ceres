# Installing Ceres

## Requirements

- Python 3.10 or newer on macOS or Linux (on Windows use WSL: Ceres uses Unix file locking).
- About 2 GB of free memory for large accounts (a board read runs in a separate worker capped at 2048 MB
  by default; see CONFIGURATION).
- A browser. To sign transactions: the Hive Keychain browser extension, the Hive Keychain mobile app's
  built-in browser, or a HiveAuth wallet app.

## Steps

```
git clone https://github.com/Silverkazimi/ceres.git ceres   # the repository's own address
cd ceres
python3 -m venv .venv && . .venv/bin/activate      # optional, recommended
python3 -m pip install -r requirements-ceres.txt
python3 -m ceres
```

Then open `http://127.0.0.1:27390/`. The start page asks for an account name. You can also open
`http://127.0.0.1:27390/?player=<account>` directly.

Ceres keeps its working files on your machine only: `~/.ceres/` (logs, the latest snapshot of each account it
read, the request journal, routing evidence), `~/.praetoria/cache/` (cached game reference data) and, for artwork,
`ceres/static/assets/` inside the checkout (images are downloaded there the first time they are needed; see
"Artwork" below). Nothing is sent anywhere except the game's public APIs, the Hive node and (for a wallet session) the HiveAuth relay in your browser, and the game's image host for artwork; see docs/API-CALLS.md.

## Checking it runs

```
curl -s http://127.0.0.1:27390/api/health
```

It answers with the running version.

## Running as a service

Any process supervisor works. A minimal systemd user unit:

```
[Service]
WorkingDirectory=/path/to/ceres
ExecStart=/path/to/ceres/.venv/bin/python -m ceres --host 127.0.0.1 --port 27390 --no-reload-watch
Environment=CERES_DEV_WATCH=0
Restart=always
TimeoutStopSec=10
```

`--no-reload-watch` and `CERES_DEV_WATCH=0` switch off the developer auto-reload, which you don't want
in a long-running service.

## Updating

`git pull`, reinstall the requirements if `requirements-ceres.txt` changed, restart, and reload open tabs.

## Artwork

This repository contains no Splinterlands artwork. The first time the page needs an image, Ceres downloads it from the game's own image host
(a short list of images, each checked against a recorded SHA-256, from `https://d36mxiodymuqjm.cloudfront.net` only) and keeps it in
`ceres/static/assets/` on your own machine. If you are offline, or an image is not on the list, that image is simply missing; nothing else
breaks. The artwork belongs to its owners (see `THIRD_PARTY_NOTICES.md`).
