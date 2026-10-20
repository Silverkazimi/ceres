# Testing

```
python3 -m pip install -r requirements-ceres.txt -r requirements-dev.txt
python3 -m pytest -m "not browser and not live" -q
```

- The default selection needs Python and Node.js (many front-end tests run the page's JavaScript through
  Node) but no browser and no network. CI runs exactly this selection with outbound network refused.
- Browser page tests (headless Chromium) are not part of the first public release; they depend on recorded
  fixtures that are kept private until synthetic ones exist.
- `-m live` tests talk to the game's API directly and are maintainer-only as well.
