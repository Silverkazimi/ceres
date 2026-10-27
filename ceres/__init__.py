"""Ceres — read-only live land board for Splinterlands (FastAPI).

Ceres is a consumer of Praetoria's read-only Land analysis report; it adds no
mutation surface to Praetoria and holds no credentials. See docs/ceres.md.
"""

from __future__ import annotations


SCHEMA_BOARD = "ceres.board.v1"
__version__ = "1.0.1"
