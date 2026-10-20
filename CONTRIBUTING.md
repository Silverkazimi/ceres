# Contributing

Thanks for your interest in Ceres.

- **Bugs and ideas:** open an issue. Include what you did, what you expected and what happened. Do not post account keys, wallet data or HiveAuth tokens.
- **Security issues:** use the repository's private vulnerability reporting; see [SECURITY.md](SECURITY.md).
- **Changes:** keep pull requests focused and describe how you checked the change.
- **Run the non-browser tests:** install development requirements, then run:

  ```sh
  python3 -m pip install -r requirements-dev.txt
  python3 -m pytest -m "not browser and not live" -q
  ```

The browser test suite is being repaired for 1.0.0. Some browser tests need synthetic recorded data that is still being built; those tests skip until that data is available. Do not describe skipped browser tests as passing coverage.

Ceres reads the game's public API. Changes that add outbound calls should document their trigger and rate in `docs/API-CALLS.md`.
