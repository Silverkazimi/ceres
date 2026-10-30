# Contributing

Thanks for your interest in Ceres.

- **Bugs and ideas:** open an issue. Include what you did, what you expected and what happened. Do not post account keys, wallet data or HiveAuth tokens.
- **Security issues:** use the repository's private vulnerability reporting; see [SECURITY.md](SECURITY.md).
- **Changes:** keep pull requests focused and describe how you checked the change.
- **Run the non-browser tests:** install development requirements, then run:

  ```sh
  python3 -m pip install -r requirements-ceres.txt -r requirements-dev.txt
  python3 -m pytest -m "not browser and not live" -q
  ```

CI also runs the browser tests (headless Chromium, after `python -m playwright install --with-deps chromium`) against a synthetic recording; see [docs/testing.md](docs/testing.md). The required `browser` job runs every browser test except the ones listed in `tests/ceres/browser_extended.txt`. Those older guards run in the separate `browser-extended` job, which is not required and currently fails because they do not match the current screens yet. Do not describe the listed tests as passing coverage.

Ceres reads the game's public API. Changes that add outbound calls should document their trigger and rate in `docs/API-CALLS.md`.
