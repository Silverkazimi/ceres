# Changelog

## 1.0.0

First stable release of Ceres.

- Cards opens on the Market when no player is loaded, shows the player's avatar, and the Status filter takes one choice.
- Choosing a totem, title or power core always takes a free copy; Fill plot never reuses an item staged on another plot.
- HiveAuth sign-in always offers a way to a fresh QR code.
- Editor fixes: cap-absorbed moves no longer look like gains, an implausible plot capacity no longer freezes the board, dropdowns no longer close right after opening.
- CI runs the full non-browser suite and a required core browser suite; older review guards that do not match the current screens yet run in a separate, visible `browser-extended` job.

## 1.0.0-rc1

First public release candidate for Ceres.

- Land board with a worker picker, staged Changes and a signing screen for reviewing and signing supported actions.
- Wallet drawer with token swaps and SPS claims.
- `/cards` My cards collection with filters; Market/shop listings with cart and checkout; Combine and Find combinables; and My purchases.
- Optional Posting-key sign-in syncs staged changes and routing setup to a per-player saved space on the Ceres server (readable by the server operator, never holding keys). Without sign-in, these remain in the browser on that device.
- After each action, Ceres checks the available game and chain results. It reports the result when verified and keeps uncertain outcomes unresolved rather than presenting them as confirmed.
