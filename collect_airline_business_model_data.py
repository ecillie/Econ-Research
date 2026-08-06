#!/usr/bin/env python3
"""Download public inputs for the airline business-model spectrum."""

from __future__ import annotations

import sys

import requests

from src.business_model.download import main


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)

