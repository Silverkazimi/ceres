# Third-party notices

- **Splinterlands game artwork** (card art, land, worksites, terrain, icons): property of its owners. Ceres
  displays it for the player's own account and does not license it.
- **HiveAuth worker** (`ceres/static/vendor/hiveauth-worker.js`): bundled with its dependencies; their
  licences are listed in `ceres/static/vendor/hiveauth-LICENSES.txt` and its provenance in
  `ceres/static/vendor/hiveauth-manifest.json`.
- **Python dependencies**: see `requirements-ceres.txt`; each package keeps its own licence. Notably **python-ecdsa** (Brian Warner and contributors, MIT) verifies the optional sign-in's Hive signatures, and **six** (Benjamin Peterson, MIT) is its only dependency.
- **qrcode-generator** (Kazuhiko Arase, MIT), bundled inside the HiveAuth worker: its licence text is in
  `ceres/static/vendor/hiveauth-LICENSES.txt`.
- **Wallet logos** (`ceres/static/vendor/wallet/*.svg`): the logos of Hive Keychain and HiveAuth, shown only to identify
  those wallets; they belong to their projects. Their source addresses and hashes are in
  `ceres/static/vendor/wallet/PROVENANCE.json`.
- **customSelect** (`ceres/static/customSelect.js`): a dropdown component originally written by this project's author for
  a sibling project and modified here; it is covered by this project's licence.
