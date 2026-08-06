#!/usr/bin/env python3
"""Build an airport-location dimension for the route-level DiD datasets.

The script discovers every origin and destination in the supplied BTS files,
downloads (or reuses) the official BTS Master Coordinate table, and writes one
row per stable BTS AirportID. Airport codes are used only when an input does
not contain a BTS identifier.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import BinaryIO, Iterable, Sequence

import pandas as pd
import requests


BTS_FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx"
BTS_FORM_PARAMS = {"gnoyr_VQ": "FLL", "QO_fu146_anzr": ""}
BTS_MASTER_URL = f"{BTS_FORM_URL}?gnoyr_VQ=FLL"
SUPPORTED_SUFFIXES = (".csv", ".csv.gz", ".zip", ".parquet")

MASTER_FIELDS = (
    "AIRPORT_SEQ_ID",
    "AIRPORT_ID",
    "AIRPORT",
    "DISPLAY_AIRPORT_NAME",
    "DISPLAY_AIRPORT_CITY_NAME_FULL",
    "AIRPORT_WAC",
    "AIRPORT_COUNTRY_NAME",
    "AIRPORT_COUNTRY_CODE_ISO",
    "AIRPORT_STATE_NAME",
    "AIRPORT_STATE_CODE",
    "AIRPORT_STATE_FIPS",
    "CITY_MARKET_ID",
    "DISPLAY_CITY_MARKET_NAME_FULL",
    "LATITUDE",
    "LONGITUDE",
    "UTC_LOCAL_TIME_VARIATION",
    "AIRPORT_START_DATE",
    "AIRPORT_THRU_DATE",
    "AIRPORT_IS_CLOSED",
    "AIRPORT_IS_LATEST",
)

INPUT_FIELDS = {
    "YEAR",
    "ORIGIN",
    "ORIGIN_AIRPORT",
    "ORIGIN_AIRPORT_ID",
    "ORIGIN_AIRPORT_SEQ_ID",
    "ORIGIN_CITY_MARKET_ID",
    "ORIGIN_CITY_NAME",
    "ORIGIN_STATE",
    "ORIGIN_STATE_ABR",
    "ORIGIN_COUNTRY",
    "DEST",
    "DESTINATION",
    "DEST_AIRPORT",
    "DEST_AIRPORT_ID",
    "DEST_AIRPORT_SEQ_ID",
    "DEST_CITY_MARKET_ID",
    "DEST_CITY_NAME",
    "DEST_STATE",
    "DEST_STATE_ABR",
    "DEST_COUNTRY",
    "AIRPORT_1",
    "AIRPORT_2",
}

ENDPOINTS = (
    {
        "code": ("ORIGIN", "ORIGIN_AIRPORT"),
        "airport_id": ("ORIGIN_AIRPORT_ID",),
        "airport_seq_id": ("ORIGIN_AIRPORT_SEQ_ID",),
        "city_market_id": ("ORIGIN_CITY_MARKET_ID",),
        "city_name": ("ORIGIN_CITY_NAME",),
        "state_code": ("ORIGIN_STATE", "ORIGIN_STATE_ABR"),
        "country_code": ("ORIGIN_COUNTRY",),
    },
    {
        "code": ("DEST", "DESTINATION", "DEST_AIRPORT"),
        "airport_id": ("DEST_AIRPORT_ID",),
        "airport_seq_id": ("DEST_AIRPORT_SEQ_ID",),
        "city_market_id": ("DEST_CITY_MARKET_ID",),
        "city_name": ("DEST_CITY_NAME",),
        "state_code": ("DEST_STATE", "DEST_STATE_ABR"),
        "country_code": ("DEST_COUNTRY",),
    },
    {
        "code": ("AIRPORT_1",),
        "airport_id": (),
        "airport_seq_id": (),
        "city_market_id": (),
        "city_name": (),
        "state_code": (),
        "country_code": (),
    },
    {
        "code": ("AIRPORT_2",),
        "airport_id": (),
        "airport_seq_id": (),
        "city_market_id": (),
        "city_name": (),
        "state_code": (),
        "country_code": (),
    },
)


def normalize_column_name(value: object) -> str:
    value = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(value).strip())
    return re.sub(r"[^A-Z0-9]+", "_", value.upper()).strip("_")


def _hidden_fields(page: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for name in ("__VIEWSTATE", "__VIEWSTATEGENERATOR", "__EVENTVALIDATION"):
        match = re.search(
            rf'(?:name|id)="{re.escape(name)}"[^>]*value="([^"]*)"',
            page,
            flags=re.IGNORECASE,
        )
        if match is None:
            raise RuntimeError(f"The BTS page did not contain {name}.")
        fields[name] = html.unescape(match.group(1))
    return fields


def _valid_master_archive(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size == 0:
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            member = next(
                name
                for name in archive.namelist()
                if name.lower().endswith((".csv", ".txt"))
            )
            with archive.open(member) as stream:
                columns = {
                    normalize_column_name(column)
                    for column in pd.read_csv(stream, nrows=0).columns
                }
        return set(MASTER_FIELDS).issubset(columns)
    except (OSError, StopIteration, zipfile.BadZipFile, pd.errors.ParserError):
        return False


def download_master_coordinate(
    destination: Path, *, refresh: bool = False
) -> str:
    """Download the official BTS Master Coordinate table when needed."""
    if not refresh and _valid_master_archive(destination):
        return "reused"

    destination.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers.update(
        {"User-Agent": "EconResearch-AirportLocations/1.0 (BTS public data)"}
    )
    landing = session.get(
        BTS_FORM_URL, params=BTS_FORM_PARAMS, timeout=(30, 180)
    )
    landing.raise_for_status()
    payload = _hidden_fields(landing.text)
    payload.update(
        {
            "__EVENTTARGET": "",
            "__EVENTARGUMENT": "",
            "__LASTFOCUS": "",
            "txtSearch": "",
            "btnDownload": "Download",
            "cboGeography": "All",
            "cboYear": "All",
            "cboPeriod": "All",
            **{field: "on" for field in MASTER_FIELDS},
        }
    )
    response = session.post(
        BTS_FORM_URL,
        params=BTS_FORM_PARAMS,
        data=payload,
        headers={"Referer": landing.url},
        timeout=(30, 900),
    )
    response.raise_for_status()
    if not response.content.startswith(b"PK"):
        content_type = response.headers.get("Content-Type", "unknown")
        raise RuntimeError(
            f"BTS returned {content_type} instead of a ZIP archive."
        )

    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(response.content)
    if not _valid_master_archive(temporary):
        temporary.unlink(missing_ok=True)
        raise RuntimeError("The downloaded BTS Master Coordinate archive is invalid.")
    temporary.replace(destination)
    return "downloaded"


def discover_files(inputs: Sequence[Path]) -> list[Path]:
    files: list[Path] = []
    for path in inputs:
        path = path.expanduser()
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and candidate.name.lower().endswith(SUPPORTED_SUFFIXES)
            )
        elif path.is_file() and path.name.lower().endswith(SUPPORTED_SUFFIXES):
            files.append(path)
        else:
            raise FileNotFoundError(f"Input does not exist or is unsupported: {path}")
    result = sorted({path.resolve() for path in files})
    if not result:
        raise FileNotFoundError("No supported airport input files were found.")
    return result


def _selected_columns(columns: Iterable[object]) -> list[object]:
    return [
        column
        for column in columns
        if normalize_column_name(column) in INPUT_FIELDS
    ]


def _csv_chunks(
    stream: BinaryIO | Path, *, chunksize: int
) -> Iterable[pd.DataFrame]:
    header = pd.read_csv(stream, nrows=0)
    selected = _selected_columns(header.columns)
    if hasattr(stream, "seek"):
        stream.seek(0)
    if not selected:
        return
    yield from pd.read_csv(
        stream,
        usecols=selected,
        chunksize=chunksize,
        low_memory=False,
    )


def read_input_chunks(path: Path, *, chunksize: int) -> Iterable[pd.DataFrame]:
    lower = path.name.lower()
    if lower.endswith(".parquet"):
        frame = pd.read_parquet(path)
        selected = _selected_columns(frame.columns)
        if selected:
            yield frame[selected]
        return
    if lower.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            for member in archive.namelist():
                if not member.lower().endswith((".csv", ".txt")):
                    continue
                with archive.open(member) as stream:
                    yield from _csv_chunks(stream, chunksize=chunksize)
        return
    yield from _csv_chunks(path, chunksize=chunksize)


def _first_column(frame: pd.DataFrame, aliases: Sequence[str]) -> str | None:
    return next((alias for alias in aliases if alias in frame.columns), None)


def _string_series(frame: pd.DataFrame, aliases: Sequence[str]) -> pd.Series:
    column = _first_column(frame, aliases)
    if column is None:
        return pd.Series(pd.NA, index=frame.index, dtype="string")
    values = frame[column].astype("string").str.strip().str.upper()
    return values.mask(values.isin(("", "NAN", "NONE", "<NA>")))


def _numeric_series(frame: pd.DataFrame, aliases: Sequence[str]) -> pd.Series:
    column = _first_column(frame, aliases)
    if column is None:
        return pd.Series(pd.NA, index=frame.index, dtype="Int64")
    return pd.to_numeric(frame[column], errors="coerce").astype("Int64")


def _source_dataset(path: Path) -> str:
    lower_parts = [part.lower() for part in path.parts]
    for label in ("db1b", "t100", "marketing_on_time", "expanded_dataset"):
        if label in lower_parts:
            return label
    return path.parent.name or "input"


def _year_series(frame: pd.DataFrame, path: Path) -> pd.Series:
    if "YEAR" in frame:
        return pd.to_numeric(frame["YEAR"], errors="coerce").astype("Int64")
    match = re.search(r"(?<!\d)(20\d{2})(?!\d)", path.name)
    value = int(match.group(1)) if match else pd.NA
    return pd.Series(value, index=frame.index, dtype="Int64")


def _endpoint_records(
    frame: pd.DataFrame, path: Path, spec: dict[str, Sequence[str]]
) -> pd.DataFrame:
    code_column = _first_column(frame, spec["code"])
    id_column = _first_column(frame, spec["airport_id"])
    if code_column is None and id_column is None:
        return pd.DataFrame()

    endpoint = pd.DataFrame(
        {
            "input_airport_code": _string_series(frame, spec["code"]),
            "input_airport_id": _numeric_series(frame, spec["airport_id"]),
            "input_airport_seq_id": _numeric_series(
                frame, spec["airport_seq_id"]
            ),
            "input_city_market_id": _numeric_series(
                frame, spec["city_market_id"]
            ),
            "input_city_name": _string_series(frame, spec["city_name"]),
            "input_state_code": _string_series(frame, spec["state_code"]),
            "input_country_code": _string_series(frame, spec["country_code"]),
            "year": _year_series(frame, path),
        }
    )
    endpoint["input_airport_code"] = endpoint["input_airport_code"].where(
        endpoint["input_airport_code"].str.fullmatch(r"[A-Z0-9]{3}", na=False)
    )
    endpoint = endpoint[
        endpoint["input_airport_id"].notna()
        | endpoint["input_airport_code"].notna()
    ].copy()
    if endpoint.empty:
        return endpoint
    endpoint["source_dataset"] = _source_dataset(path)
    endpoint["source_file"] = str(path)
    identity = [
        "input_airport_code",
        "input_airport_id",
        "input_airport_seq_id",
        "input_city_market_id",
        "input_city_name",
        "input_state_code",
        "input_country_code",
        "year",
        "source_dataset",
        "source_file",
    ]
    return (
        endpoint.groupby(identity, dropna=False, observed=True)
        .size()
        .rename("source_record_count")
        .reset_index()
    )


def collect_observed_airports(
    files: Sequence[Path], *, chunksize: int
) -> tuple[pd.DataFrame, int]:
    pieces: list[pd.DataFrame] = []
    rows_scanned = 0
    for index, path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] Reading {path}", flush=True)
        for raw in read_input_chunks(path, chunksize=chunksize):
            rows_scanned += len(raw)
            frame = raw.copy()
            frame.columns = [normalize_column_name(column) for column in frame.columns]
            for spec in ENDPOINTS:
                endpoint = _endpoint_records(frame, path, spec)
                if not endpoint.empty:
                    pieces.append(endpoint)
    if not pieces:
        raise ValueError("The inputs contained no recognizable airport columns.")
    return pd.concat(pieces, ignore_index=True), rows_scanned


def load_master_coordinate(path: Path) -> pd.DataFrame:
    with zipfile.ZipFile(path) as archive:
        member = next(
            name
            for name in archive.namelist()
            if name.lower().endswith((".csv", ".txt"))
        )
        with archive.open(member) as stream:
            master = pd.read_csv(stream, low_memory=False)
    master.columns = [normalize_column_name(column) for column in master.columns]
    for column in (
        "AIRPORT_SEQ_ID",
        "AIRPORT_ID",
        "AIRPORT_WAC",
        "AIRPORT_STATE_FIPS",
        "CITY_MARKET_ID",
        "AIRPORT_IS_CLOSED",
        "AIRPORT_IS_LATEST",
    ):
        master[column] = pd.to_numeric(master[column], errors="coerce").astype(
            "Int64"
        )
    for column in ("LATITUDE", "LONGITUDE"):
        master[column] = pd.to_numeric(master[column], errors="coerce")
    for column in (
        "AIRPORT",
        "AIRPORT_COUNTRY_CODE_ISO",
        "AIRPORT_STATE_CODE",
    ):
        master[column] = master[column].astype("string").str.strip().str.upper()
    return master


def _latest_master(master: pd.DataFrame) -> pd.DataFrame:
    latest = master[master["AIRPORT_IS_LATEST"].eq(1)].copy()
    fallback = (
        master.sort_values("AIRPORT_SEQ_ID")
        .dropna(subset=["AIRPORT_ID"])
        .drop_duplicates("AIRPORT_ID", keep="last")
    )
    missing_ids = fallback[~fallback["AIRPORT_ID"].isin(latest["AIRPORT_ID"])]
    latest = pd.concat([latest, missing_ids], ignore_index=True)
    return (
        latest.sort_values("AIRPORT_SEQ_ID")
        .drop_duplicates("AIRPORT_ID", keep="last")
        .reset_index(drop=True)
    )


def _unique_code_map(latest: pd.DataFrame) -> dict[str, int]:
    candidates = latest.dropna(subset=["AIRPORT", "AIRPORT_ID"])
    return {
        str(code): int(ids.iloc[0])
        for code, ids in candidates.groupby("AIRPORT", observed=True)[
            "AIRPORT_ID"
        ]
        if ids.nunique() == 1
    }


def _join_values(values: pd.Series) -> str | pd.NA:
    clean = sorted(
        {
            str(value).strip()
            for value in values.dropna()
            if str(value).strip() not in ("", "<NA>")
        }
    )
    return "|".join(clean) if clean else pd.NA


def _join_integer_values(values: pd.Series) -> str | pd.NA:
    clean = sorted({int(value) for value in values.dropna()})
    return "|".join(str(value) for value in clean) if clean else pd.NA


def build_airport_dimension(
    observed: pd.DataFrame, master: pd.DataFrame, *, retrieved_at: str
) -> pd.DataFrame:
    latest = _latest_master(master)
    code_map = _unique_code_map(latest)
    observed = observed.copy()
    resolved_from_code = observed["input_airport_code"].map(code_map).astype("Int64")
    observed["resolved_airport_id"] = observed["input_airport_id"].fillna(
        resolved_from_code
    )
    observed["airport_key"] = observed["resolved_airport_id"].map(
        lambda value: f"id:{int(value)}" if pd.notna(value) else pd.NA
    )
    missing_key = observed["airport_key"].isna()
    observed.loc[missing_key, "airport_key"] = (
        "code:" + observed.loc[missing_key, "input_airport_code"].astype("string")
    )

    summary_rows: list[dict[str, object]] = []
    for key, group in observed.groupby("airport_key", observed=True, sort=True):
        resolved_id = group["resolved_airport_id"].dropna()
        airport_id = int(resolved_id.iloc[0]) if not resolved_id.empty else pd.NA
        years = group["year"].dropna()
        summary_rows.append(
            {
                "airport_key": key,
                "AIRPORT_ID": airport_id,
                "input_airport_codes": _join_values(group["input_airport_code"]),
                "input_airport_seq_ids": _join_integer_values(
                    group["input_airport_seq_id"]
                ),
                "input_city_market_ids": _join_integer_values(
                    group["input_city_market_id"]
                ),
                "input_city_names": _join_values(group["input_city_name"]),
                "input_state_codes": _join_values(group["input_state_code"]),
                "input_country_codes": _join_values(
                    group["input_country_code"]
                ),
                "first_year_observed": int(years.min()) if not years.empty else pd.NA,
                "last_year_observed": int(years.max()) if not years.empty else pd.NA,
                "source_datasets": _join_values(group["source_dataset"]),
                "source_files": _join_values(group["source_file"]),
                "source_record_count": int(group["source_record_count"].sum()),
                "location_match_method": (
                    "bts_airport_id"
                    if group["input_airport_id"].notna().any()
                    else (
                        "unique_airport_code" if pd.notna(airport_id) else "unmatched_code"
                    )
                ),
            }
        )

    summary = pd.DataFrame(summary_rows)
    result = summary.merge(
        latest,
        on="AIRPORT_ID",
        how="left",
        validate="many_to_one",
        indicator=True,
    )
    result["airport_code"] = result["AIRPORT"].fillna(
        result["input_airport_codes"].str.split("|").str[0]
    )
    valid_coordinates = (
        result["LATITUDE"].between(-90, 90, inclusive="both")
        & result["LONGITUDE"].between(-180, 180, inclusive="both")
    )
    result["location_status"] = "matched_missing_coordinates"
    result.loc[result["_merge"].eq("left_only"), "location_status"] = "unmatched"
    result.loc[valid_coordinates, "location_status"] = "matched"
    result["coordinate_source"] = "BTS Master Coordinate"
    result["coordinate_source_url"] = BTS_MASTER_URL
    result["source_retrieved_at_utc"] = retrieved_at

    renamed = result.drop(columns="_merge").rename(
        columns={
            "AIRPORT_ID": "bts_airport_id",
            "AIRPORT_SEQ_ID": "bts_airport_seq_id",
            "DISPLAY_AIRPORT_NAME": "airport_name",
            "DISPLAY_AIRPORT_CITY_NAME_FULL": "airport_city_name",
            "AIRPORT_WAC": "world_area_code",
            "AIRPORT_COUNTRY_NAME": "country_name",
            "AIRPORT_COUNTRY_CODE_ISO": "country_iso",
            "AIRPORT_STATE_NAME": "state_name",
            "AIRPORT_STATE_CODE": "state_code",
            "AIRPORT_STATE_FIPS": "state_fips",
            "CITY_MARKET_ID": "bts_city_market_id",
            "DISPLAY_CITY_MARKET_NAME_FULL": "city_market_name",
            "LATITUDE": "latitude",
            "LONGITUDE": "longitude",
            "UTC_LOCAL_TIME_VARIATION": "utc_offset",
            "AIRPORT_START_DATE": "airport_start_date",
            "AIRPORT_THRU_DATE": "airport_end_date",
            "AIRPORT_IS_CLOSED": "is_closed",
            "AIRPORT_IS_LATEST": "is_latest",
        }
    )
    columns = [
        "bts_airport_id",
        "bts_airport_seq_id",
        "airport_code",
        "airport_name",
        "airport_city_name",
        "bts_city_market_id",
        "city_market_name",
        "state_code",
        "state_name",
        "state_fips",
        "country_iso",
        "country_name",
        "world_area_code",
        "latitude",
        "longitude",
        "utc_offset",
        "airport_start_date",
        "airport_end_date",
        "is_closed",
        "is_latest",
        "location_match_method",
        "location_status",
        "input_airport_codes",
        "input_airport_seq_ids",
        "input_city_market_ids",
        "input_city_names",
        "input_state_codes",
        "input_country_codes",
        "first_year_observed",
        "last_year_observed",
        "source_datasets",
        "source_files",
        "source_record_count",
        "coordinate_source",
        "coordinate_source_url",
        "source_retrieved_at_utc",
    ]
    return renamed[columns].sort_values(
        ["location_status", "airport_code"], na_position="last"
    ).reset_index(drop=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Collect airports used by BTS route data and attach official BTS "
            "Master Coordinate locations."
        )
    )
    parser.add_argument(
        "--input",
        nargs="+",
        type=Path,
        default=[Path("data/raw/t100"), Path("data/raw/db1b")],
        help=(
            "Input files/directories. Defaults to data/raw/t100 and data/raw/db1b."
        ),
    )
    parser.add_argument(
        "--master-cache",
        type=Path,
        default=Path("data/reference/bts_master_coordinate.zip"),
        help="Cached official BTS Master Coordinate ZIP.",
    )
    parser.add_argument(
        "--refresh-master",
        action="store_true",
        help="Redownload the BTS Master Coordinate table even when cached.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/airport_locations.csv"),
    )
    parser.add_argument(
        "--diagnostics",
        type=Path,
        default=Path("output/airport_locations_diagnostics.json"),
    )
    parser.add_argument("--chunksize", type=int, default=250_000)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Return an error after writing outputs if any location is missing.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.chunksize < 1:
        raise ValueError("--chunksize must be positive.")
    files = discover_files(args.input)
    print(f"Found {len(files)} input files.")
    master_action = download_master_coordinate(
        args.master_cache, refresh=args.refresh_master
    )
    print(f"BTS Master Coordinate: {master_action} {args.master_cache}")

    observed, rows_scanned = collect_observed_airports(
        files, chunksize=args.chunksize
    )
    master = load_master_coordinate(args.master_cache)
    retrieved_at = datetime.fromtimestamp(
        args.master_cache.stat().st_mtime, tz=timezone.utc
    ).isoformat()
    airports = build_airport_dimension(
        observed, master, retrieved_at=retrieved_at
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.diagnostics.parent.mkdir(parents=True, exist_ok=True)
    airports.to_csv(args.output, index=False)
    status_counts = {
        str(key): int(value)
        for key, value in airports["location_status"].value_counts().items()
    }
    diagnostics = {
        "airport_rows": int(len(airports)),
        "input_files": len(files),
        "input_rows_scanned": int(rows_scanned),
        "location_status": status_counts,
        "master_coordinate_action": master_action,
        "master_coordinate_cache": str(args.master_cache.resolve()),
        "output": str(args.output.resolve()),
        "note": (
            "source_record_count counts endpoint rows in source files; it is an "
            "audit statistic, not a passenger or flight measure."
        ),
    }
    args.diagnostics.write_text(
        json.dumps(diagnostics, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(diagnostics, indent=2, sort_keys=True))
    if args.strict and status_counts.get("matched", 0) != len(airports):
        raise RuntimeError("At least one airport lacks a validated location.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (
        FileNotFoundError,
        ValueError,
        RuntimeError,
        zipfile.BadZipFile,
        requests.RequestException,
    ) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
