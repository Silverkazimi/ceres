# Changelog

## 1.0.0-rc1

First public release candidate for Ceres.

- Land board with a worker picker, staged Changes and a signing screen for reviewing and signing supported actions.
- Wallet drawer with token swaps and SPS claims.
- `/cards` My cards collection with filters; Market/shop listings with cart and checkout; Combine and Find combinables; and My purchases.
- Optional Posting-key sign-in syncs staged changes and routing setup to a per-player saved space on the Ceres server (readable by the server operator, never holding keys). Without sign-in, these remain in the browser on that device.
- After each action, Ceres checks the available game and chain results. It reports the result when verified and keeps uncertain outcomes unresolved rather than presenting them as confirmed.
