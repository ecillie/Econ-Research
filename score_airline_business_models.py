#!/usr/bin/env python3
"""Build and score the Lohmann-Koo airline business-model spectrum."""

from __future__ import annotations

import sys

from src.business_model.cli import main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

