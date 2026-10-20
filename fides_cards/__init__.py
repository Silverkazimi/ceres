"""Card game-action adapter for Fides.

This package builds explicit card-combine operations and verifies their game effect;
the generic Fides core remains unaware of Splinterlands.
"""

from .ops import CombineError, CombineIntent, build_combine_op, survivor_rule
from .verify import CardVerificationSpec, CombineVerificationSpec

__all__ = [
    "CardVerificationSpec",
    "CombineError",
    "CombineIntent",
    "CombineVerificationSpec",
    "build_combine_op",
    "survivor_rule",
]
