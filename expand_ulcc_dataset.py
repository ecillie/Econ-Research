#!/usr/bin/env python3
"""Compatibility entry point for the modular quarterly ULCC-exit pipeline."""

from __future__ import annotations

import sys

import requests

# Re-export the former script API so existing notebooks keep working.
from airline_research.bts import (
    SUPPORTED_SUFFIXES,
    read_table_chunks,
    webform_state,
)
from airline_research.ulcc.analysis import *  # noqa: F401,F403
from airline_research.ulcc.cli import build_parser, main, parse_args
from airline_research.ulcc.config import *  # noqa: F401,F403
from airline_research.ulcc.data import *  # noqa: F401,F403
from airline_research.ulcc.download import *  # noqa: F401,F403
from airline_research.ulcc.reporting import *  # noqa: F401,F403


def read_one(path, chunksize=250_000):
    yield from read_table_chunks(path, chunksize=chunksize)


def hidden_fields(page: str) -> dict[str, str]:
    return webform_state(page, form_name="BTS T-100 form")


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileNotFoundError,
        ValueError,
        KeyError,
        RuntimeError,
        requests.RequestException,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
