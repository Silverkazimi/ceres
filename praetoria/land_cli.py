"""Deprecated compatibility shim for the canonical Land command."""

from __future__ import annotations

import sys

from .cli import main as canonical_main


def main(argv: list[str] | None = None) -> int:
    print("NOTICE: praetoria.land_cli is deprecated; use python3 -m praetoria land.", file=sys.stderr)
    forwarded = sys.argv[1:] if argv is None else argv
    return canonical_main(["land", *forwarded])


if __name__ == "__main__":
    raise SystemExit(main())
