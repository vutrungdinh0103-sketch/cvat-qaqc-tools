"""Entry point cho CLI mới: ``python -m qaqc ...``."""

from __future__ import annotations

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
