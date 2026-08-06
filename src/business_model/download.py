"""Download public BTS files used by the business-model spectrum pipeline."""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import requests

from ..bts import normalize_column_name, stream_response_to_zip, valid_zip, webform_state


BTS_FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx"
DB1B_URL = (
    "https://transtats.bts.gov/PREZIP/"
    "Origin_and_Destination_Survey_DB1BMarket_{year}_{quarter}.zip"
)


@dataclass(frozen=True)
class DownloadSpec:
    """One BTS web-form table and its required output columns."""

    key: str
    form_code: str
    table_parameter: str
    subdirectory: str
    filename: str
    fields: tuple[str, ...]
    required_aliases: tuple[tuple[str, ...], ...]
    monthly: bool = False


SPECS = {
    "t100": DownloadSpec(
        key="t100",
        form_code="FIM",
        table_parameter="Nv4 Pn44vr45",
        subdirectory="t100",
        filename="T100_Domestic_Segment_{year}.zip",
        fields=(
            "DEPARTURES_PERFORMED",
            "SEATS",
            "PASSENGERS",
            "DISTANCE",
            "AIR_TIME",
            "UNIQUE_CARRIER",
            "UNIQUE_CARRIER_NAME",
            "ORIGIN",
            "DEST",
            "YEAR",
            "MONTH",
            "CLASS",
        ),
        required_aliases=(
            ("YEAR",),
            ("UNIQUE_CARRIER",),
            ("DEPARTURES_PERFORMED",),
            ("AIR_TIME",),
        ),
    ),
    "on_time": DownloadSpec(
        key="on_time",
        form_code="FGK",
        table_parameter="b0-gvzr",
        subdirectory="business_model/on_time",
        filename="Marketing_Carrier_On_Time_Full_{year}_{month}.zip",
        fields=(
            "YEAR",
            "MONTH",
            "MKT_UNIQUE_CARRIER",
            "OP_UNIQUE_CARRIER",
            "ORIGIN",
            "DEST",
            "CANCELLED",
            "DIVERTED",
            "DEP_DEL15",
            "ARR_DEL15",
            "DEP_DELAY_NEW",
            "ARR_DELAY_NEW",
        ),
        required_aliases=(
            ("YEAR",),
            ("MKT_UNIQUE_CARRIER", "MARKETING_AIRLINE_NETWORK"),
            ("OP_UNIQUE_CARRIER", "OPERATING_AIRLINE"),
            ("DEP_DEL15", "DEPDELAY15", "DEP_DELAY_NEW"),
            ("ARR_DEL15", "ARRDELAY15", "ARR_DELAY_NEW"),
        ),
        monthly=True,
    ),
    "p12": DownloadSpec(
        key="p12",
        form_code="FMI",
        table_parameter="Nv4 Pn44vr4 Sv0n0pvny",
        subdirectory="business_model/p12",
        filename="Schedule_P12_{year}.zip",
        fields=(
            "YEAR",
            "QUARTER",
            "UNIQUE_CARRIER",
            "UNIQUE_CARRIER_NAME",
            "REGION",
            "UNIQUE_CARRIER_ENTITY",
            "OP_EXPENSES",
            "TRANS_REV_PAX",
            "OP_REVENUES",
            "PROP_BAG",
            "RES_CANCEL_FEES",
        ),
        required_aliases=(
            ("YEAR",),
            ("UNIQUE_CARRIER", "UNIQUECARRIER"),
            ("OP_EXPENSES", "OPEXPENSES"),
            ("TRANS_REV_PAX", "TRANSREVPAX"),
            ("OP_REVENUES", "OPREVENUES"),
        ),
    ),
    "p6": DownloadSpec(
        key="p6",
        form_code="FME",
        table_parameter="Nv4 Pn44vr4 Sv0n0pvny",
        subdirectory="business_model/p6",
        filename="Schedule_P6_{year}.zip",
        fields=(
            "YEAR",
            "QUARTER",
            "UNIQUE_CARRIER",
            "UNIQUE_CARRIER_NAME",
            "REGION",
            "UNIQUE_CARRIER_ENTITY",
            "SALARIES_BENEFITS",
        ),
        required_aliases=(
            ("YEAR",),
            ("UNIQUE_CARRIER", "UNIQUECARRIER"),
            ("SALARIES_BENEFITS", "SALARIESBENEFITS"),
        ),
    ),
    "p10": DownloadSpec(
        key="p10",
        form_code="GDF",
        table_parameter="Nv4 Pn44vr4 Sv0n0pvny",
        subdirectory="business_model/p10",
        filename="Schedule_P10_{year}.zip",
        fields=(
            "YEAR",
            "UNIQUE_CARRIER",
            "UNIQUE_CARRIER_NAME",
            "ENTITY",
            "PILOTS_COPILOTS",
            "OTHER_FLT_PERS",
            "TOTAL",
        ),
        required_aliases=(
            ("YEAR",),
            ("UNIQUE_CARRIER", "UNIQUECARRIER"),
            ("PILOTS_COPILOTS", "PILOTSCOPILOTS"),
            ("OTHER_FLIGHT_PERSONNEL", "OTHERFLIGHTPERSONNEL", "OTHER_FLT_PERS"),
            ("TOTAL",),
        ),
    ),
    "b43": DownloadSpec(
        key="b43",
        form_code="GEH",
        table_parameter="Nv4 Pn44vr4 Sv0n0pvny",
        subdirectory="business_model/b43",
        filename="Schedule_B43_{year}.zip",
        fields=(
            "YEAR",
            "UNIQUE_CARRIER",
            "UNIQUE_CARRIER_NAME",
            "TAIL_NUMBER",
            "AIRCRAFT_STATUS",
            "OPERATING_STATUS",
            "MANUFACTURER",
            "AIRCRAFT_TYPE",
            "MODEL",
        ),
        required_aliases=(
            ("YEAR",),
            ("UNIQUE_CARRIER", "UNIQUECARRIER"),
            ("TAIL_NUMBER", "TAILNUMBER"),
            ("MODEL", "AIRCRAFT_TYPE", "AIRCRAFTTYPE"),
        ),
    ),
}


def _archive_columns(path: Path) -> set[str]:
    if not valid_zip(path):
        return set()
    with zipfile.ZipFile(path) as archive:
        members = [
            member
            for member in archive.namelist()
            if member.lower().endswith((".csv", ".txt"))
            and not member.startswith("__MACOSX/")
        ]
        if not members:
            return set()
        with archive.open(members[0]) as binary:
            text = io.TextIOWrapper(binary, encoding="utf-8-sig", errors="replace")
            header = next(csv.reader(text), [])
    return {normalize_column_name(column) for column in header}


def archive_has_required_columns(path: Path, spec: DownloadSpec) -> bool:
    """Validate both the ZIP container and the columns needed by the scorer."""
    columns = _archive_columns(path)
    return bool(columns) and all(
        any(normalize_column_name(alias) in columns for alias in aliases)
        for aliases in spec.required_aliases
    )


def _payload(spec: DownloadSpec, *, year: int, period: str) -> dict[str, str]:
    return {
        "__EVENTTARGET": "",
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "txtSearch": "",
        "btnDownload": "Download",
        "cboGeography": "All",
        "cboYear": str(year),
        "cboPeriod": period,
        **{field: "on" for field in spec.fields},
    }


def download_form_archive(
    session: requests.Session,
    spec: DownloadSpec,
    destination: Path,
    *,
    year: int,
    month: int | None = None,
    force: bool = False,
) -> str:
    """Download one year or month from a BTS TranStats form."""
    if not force and archive_has_required_columns(destination, spec):
        return "reused"
    destination.parent.mkdir(parents=True, exist_ok=True)
    params = {"QO_fu146_anzr": spec.table_parameter, "gnoyr_VQ": spec.form_code}
    landing = session.get(BTS_FORM_URL, params=params, timeout=(30, 180))
    landing.raise_for_status()
    payload = webform_state(landing.text, form_name=f"BTS {spec.key} form")
    payload.update(_payload(spec, year=year, period=str(month) if month else "All"))
    response = session.post(
        BTS_FORM_URL,
        params=params,
        data=payload,
        headers={"Referer": landing.url},
        stream=True,
        timeout=(30, 1800),
    )
    stream_response_to_zip(response, destination)
    if not archive_has_required_columns(destination, spec):
        raise RuntimeError(
            f"Downloaded {destination.name}, but it lacks columns required for {spec.key}."
        )
    return "downloaded"


def download_db1b_quarter(
    session: requests.Session,
    destination: Path,
    *,
    year: int,
    quarter: int,
    force: bool = False,
) -> str:
    if not force and valid_zip(destination):
        return "reused"
    destination.parent.mkdir(parents=True, exist_ok=True)
    response = session.get(
        DB1B_URL.format(year=year, quarter=quarter),
        stream=True,
        timeout=(30, 1800),
    )
    stream_response_to_zip(response, destination)
    return "downloaded"


def _attempt(action, description: str) -> str:
    for attempt in range(1, 4):
        try:
            return action()
        except (requests.RequestException, RuntimeError) as exc:
            if attempt == 3:
                raise RuntimeError(f"Could not download {description}: {exc}") from exc
            print(f"{description}: attempt {attempt} failed; retrying ...", flush=True)
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def collect(
    *,
    root: Path,
    start_year: int,
    end_year: int,
    datasets: Iterable[str],
    force: bool = False,
) -> dict[str, int]:
    """Collect the selected public datasets for an inclusive year range."""
    if end_year < start_year:
        raise ValueError("end year must not precede start year")
    requested = list(dict.fromkeys(datasets))
    invalid = set(requested) - ({"db1b"} | set(SPECS))
    if invalid:
        raise ValueError(f"Unknown datasets: {sorted(invalid)}")
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "EconResearch-AirlineBusinessModel/1.0 (BTS public-data download)"}
    )
    counts = {"downloaded": 0, "reused": 0}

    for key in requested:
        if key == "db1b":
            directory = root / "db1b"
            for year in range(start_year, end_year + 1):
                for quarter in range(1, 5):
                    destination = directory / f"DB1BMarket_{year}Q{quarter}.zip"
                    description = f"DB1B {year}Q{quarter}"
                    status = _attempt(
                        lambda d=destination, y=year, q=quarter: download_db1b_quarter(
                            session, d, year=y, quarter=q, force=force
                        ),
                        description,
                    )
                    counts[status] += 1
                    print(f"{status.capitalize()} {description}: {destination}")
            continue

        spec = SPECS[key]
        directory = root / spec.subdirectory
        for year in range(start_year, end_year + 1):
            months: Sequence[int | None] = range(1, 13) if spec.monthly else (None,)
            for month in months:
                destination = directory / spec.filename.format(year=year, month=month)
                description = f"{key} {year}" + (f"-{month:02d}" if month else "")
                status = _attempt(
                    lambda s=spec, d=destination, y=year, m=month: download_form_archive(
                        session, s, d, year=y, month=m, force=force
                    ),
                    description,
                )
                counts[status] += 1
                print(f"{status.capitalize()} {description}: {destination}")
    return counts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Download the BTS T-100, DB1B, on-time, and Form 41 files needed "
            "for the airline business-model spectrum."
        )
    )
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=["t100", "db1b", "on_time", "p12", "p6", "p10", "b43"],
        default=["t100", "db1b", "on_time", "p12", "p6", "p10", "b43"],
    )
    parser.add_argument(
        "--root",
        type=Path,
        default=Path("data/raw"),
        help="Raw-data root (default: data/raw).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even when an existing archive passes validation.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    counts = collect(
        root=args.root,
        start_year=args.start_year,
        end_year=args.end_year,
        datasets=args.datasets,
        force=args.force,
    )
    print(f"Collection complete: {counts['downloaded']} downloaded, {counts['reused']} reused.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, RuntimeError, requests.RequestException) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
