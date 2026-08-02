"""Marketing-carrier data downloads for monthly route-exit analysis."""

from __future__ import annotations

import argparse
import re
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import requests

from airline_research.bts import (
    discover_files,
    stream_response_to_zip,
    valid_zip,
    webform_state,
)

from .config import MARKETING_FORM_PARAMS, MARKETING_FORM_URL


def download_marketing_month(
    session: requests.Session, directory: Path, year: int, month: int
) -> None:
    destination = directory / f"Marketing_Carrier_On_Time_{year}_{month}.zip"
    if valid_zip(destination):
        print(f"Using existing {destination}")
        return
    print(f"Downloading BTS marketing/operator mapping for {year}-{month:02d} ...")
    landing = session.get(
        MARKETING_FORM_URL,
        params=MARKETING_FORM_PARAMS,
        timeout=(30, 180),
    )
    landing.raise_for_status()
    payload = webform_state(landing.text, form_name="BTS download form")
    payload.update(
        {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "txtSearch": "",
            "btnDownload": "Download",
            "cboGeography": "All",
            "cboYear": str(year),
            "cboPeriod": str(month),
            "YEAR": "on",
            "MONTH": "on",
            "MKT_UNIQUE_CARRIER": "on",
            "OP_UNIQUE_CARRIER": "on",
            "ORIGIN": "on",
            "DEST": "on",
            "CANCELLED": "on",
        }
    )
    response = session.post(
        MARKETING_FORM_URL,
        params=MARKETING_FORM_PARAMS,
        data=payload,
        headers={"Referer": landing.url},
        stream=True,
        timeout=(30, 900),
    )
    stream_response_to_zip(response, destination)


def _inferred_year_range(args: argparse.Namespace) -> tuple[int, int]:
    if args.start_month and args.end_month:
        return (
            pd.Period(args.start_month, freq="M").year,
            pd.Period(args.end_month, freq="M").year,
        )
    t100_years = [
        int(match.group(1))
        for path in discover_files(args.t100, label="T-100 input")
        if (match := re.search(r"(20\d{2})", path.name))
    ]
    if not t100_years:
        raise ValueError("Could not infer years; provide --start-month and --end-month.")
    return min(t100_years), max(t100_years)


def ensure_marketing_data(args: argparse.Namespace) -> None:
    """Download missing monthly marketing-carrier archives in parallel."""
    start = pd.Period(args.start_month, freq="M") if args.start_month else None
    end = pd.Period(args.end_month, freq="M") if args.end_month else None
    first_year, last_year = _inferred_year_range(args)
    args.marketing_data.mkdir(parents=True, exist_ok=True)
    first_period = start or pd.Period(f"{first_year}-01", freq="M")
    last_period = end or pd.Period(f"{last_year}-12", freq="M")
    periods = list(pd.period_range(first_period, last_period, freq="M"))

    def fetch(period: pd.Period) -> None:
        session = requests.Session()
        session.headers.update(
            {"User-Agent": "EconResearch-CarrierExit/1.0 (BTS public data)"}
        )
        for attempt in range(1, 4):
            try:
                download_marketing_month(
                    session, args.marketing_data, period.year, period.month
                )
                return
            except (requests.RequestException, RuntimeError) as exc:
                if attempt == 3:
                    raise RuntimeError(
                        f"Could not download marketing data for {period}: {exc}"
                    ) from exc
                print(f"Download attempt {attempt} failed; retrying ...")
                time.sleep(2**attempt)

    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(fetch, periods))
