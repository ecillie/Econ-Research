#!/usr/bin/env python3
"""Compatibility entry point for the modular monthly route-exit pipeline."""

from __future__ import annotations

import sys
import zipfile

import requests

# Re-export the former script API so existing notebooks keep working.
from airline_research.bts import SUPPORTED_SUFFIXES, webform_state
from airline_research.route_exits.analysis import *  # noqa: F401,F403
from airline_research.route_exits.cli import build_parser, main, parse_args
from airline_research.route_exits.config import *  # noqa: F401,F403
from airline_research.route_exits.data import *  # noqa: F401,F403
from airline_research.route_exits.download import *  # noqa: F401,F403
from airline_research.route_exits.reporting import *  # noqa: F401,F403


def hidden_fields(page: str) -> dict[str, str]:
    return webform_state(page, form_name="BTS download form")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileNotFoundError,
        ValueError,
        KeyError,
        RuntimeError,
        requests.RequestException,
        zipfile.BadZipFile,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
