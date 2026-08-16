"""Download official BTS inputs used by the quarterly ULCC pipeline."""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Sequence

import pandas as pd
import requests

from src.bts import stream_response_to_zip, valid_zip, webform_state


DB1B_LAST_QUARTER = pd.Period("2025Q2", freq="Q")
DB1B_URL = (
    "https://transtats.bts.gov/PREZIP/"
    "Origin_and_Destination_Survey_DB1BMarket_{year}_{quarter}.zip"
)
T100_FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx"


def requested_period(args: argparse.Namespace) -> tuple[pd.Period, pd.Period]:
    if not args.start_quarter or not args.end_quarter:
        raise ValueError("--download requires both --start-quarter and --end-quarter.")
    start = pd.Period(args.start_quarter, freq="Q")
    end = pd.Period(args.end_quarter, freq="Q")
    if end < start:
        raise ValueError("--end-quarter must not precede --start-quarter.")
    return start, end


def first_directory(inputs: Sequence[str], label: str) -> Path:
    if len(inputs) != 1:
        raise ValueError(
            f"--download expects one {label} directory, not multiple paths."
        )
    path = Path(inputs[0]).expanduser()
    if path.exists() and not path.is_dir():
        raise ValueError(f"Download destination is not a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_db1b(
    session: requests.Session,
    directory: Path,
    start: pd.Period,
    end: pd.Period,
) -> None:
    available_end = min(end, DB1B_LAST_QUARTER)
    if start > available_end:
        raise ValueError(
            "Quarterly DB1B is only available through 2025Q2. "
            "Use a start quarter no later than 2025Q2."
        )
    if end > DB1B_LAST_QUARTER:
        print(
            "Note: DB1B ended after 2025Q2; fare outcomes will stop there. "
            "Later T-100 operating quarters can still be included."
        )
    for quarter in pd.period_range(start, available_end, freq="Q"):
        destination = directory / f"DB1BMarket_{quarter}.zip"
        if valid_zip(destination):
            print(f"Using existing {destination}")
            continue
        url = DB1B_URL.format(year=quarter.year, quarter=quarter.quarter)
        print(f"Downloading DB1B {quarter} ...", flush=True)
        response = session.get(url, stream=True, timeout=(30, 900))
        stream_response_to_zip(response, destination)


def download_t100_year(
    session: requests.Session, directory: Path, year: int
) -> None:
    destination = directory / f"T100_Domestic_Segment_{year}.zip"
    if valid_zip(destination):
        print(f"Using existing {destination}")
        return
    params = {"gnoyr_VQ": "FIM", "QO_fu146_anzr": "Nv4%Pn44vr45"}
    print(f"Requesting T-100 Domestic Segment {year} ...", flush=True)
    landing = session.get(T100_FORM_URL, params=params, timeout=(30, 120))
    landing.raise_for_status()
    payload = webform_state(landing.text, form_name="BTS T-100 form")
    payload.update(
        {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "txtSearch": "",
            "btnDownload": "Download",
            "cboGeography": "All",
            "cboYear": str(year),
            "cboPeriod": "All",
            "chkAllVars": "on",
            "UNIQUE_CARRIER": "on",
            "UNIQUE_CARRIER_NAME": "on",
            "ORIGIN_AIRPORT_ID": "on",
            "ORIGIN": "on",
            "DEST_AIRPORT_ID": "on",
            "DEST": "on",
            "MONTH": "on",
        }
    )
    response = session.post(
        T100_FORM_URL,
        params=params,
        data=payload,
        headers={"Referer": landing.url},
        stream=True,
        timeout=(30, 1800),
    )
    stream_response_to_zip(response, destination)


def download_inputs(args: argparse.Namespace) -> None:
    """Download all missing T-100 and DB1B archives requested by the CLI."""
    start, end = requested_period(args)
    t100_directory = first_directory(args.t100, "T-100")
    db1b_directory = first_directory(args.db1b, "DB1B")
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "EconResearch-ULCCExit/1.0 (BTS public-data download)"}
    )
    for year in range(start.year, end.year + 1):
        for attempt in range(1, 4):
            try:
                download_t100_year(session, t100_directory, year)
                break
            except (requests.RequestException, RuntimeError) as exc:
                if attempt == 3:
                    raise RuntimeError(
                        f"Could not download T-100 {year} after 3 attempts: {exc}"
                    ) from exc
                print(f"T-100 {year} attempt {attempt} failed; retrying ...")
                time.sleep(2**attempt)
    download_db1b(session, db1b_directory, start, end)
