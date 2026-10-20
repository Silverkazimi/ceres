"""Fides -- a general-purpose Hive Keychain batch-transaction-builder and signer.

This package has zero knowledge of Splinterlands, land, decks, or cards. It accepts an
arbitrary list of Hive operations (today: custom_json only, see `op.Op`) and handles,
generically: batching them into Hive transactions, generating a human-review bundle,
generating a self-contained browser-side Keychain signing page, and tracking result state
via chain-plus-domain double verification. See docs/fides.md and
docs/plans/fides-batch-signing.md for the full design.

Fides never holds or requests key material of any kind, never calls `requestSignTx`, and
never broadcasts anything itself -- it writes a file; a human clicks Confirm in Keychain.
"""

from __future__ import annotations

__all__ = ["op", "ledger", "verify", "bundle"]
