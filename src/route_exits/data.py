"""Chunked loaders for operator- and marketing-carrier monthly route data."""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import BinaryIO, Iterable

import numpy as np
import pandas as pd

from airline_research.bts import (
    canonical_airport_pair,
    clean_code,
    discover_files,
    normalize_columns,
    read_table_chunks,
)

from .config import (
    KNOWN_CARRIER_NAMES,
    MARKETING_ALIASES,
    MARKETING_COLUMNS,
    OPTIONAL_COLUMNS,
    REQUIRED_COLUMNS,
)


T100_COLUMNS = REQUIRED_COLUMNS | OPTIONAL_COLUMNS
MARKETING_INPUT_COLUMNS = {
    alias for aliases in MARKETING_ALIASES.values() for alias in aliases
}


def files_for_month_range(
    inputs: list[str | Path],
    *,
    label: str,
    start: pd.Period | None,
    end: pd.Period | None,
) -> list[Path]:
    """Skip monthly or annual archives outside the requested sample."""
    files = discover_files(inputs, label=label)
    if start is None and end is None:
        return files
    selected: list[Path] = []
    for path in files:
        month_match = re.search(
            r"(?<!\d)(20\d{2})[_-](1[0-2]|0?[1-9])(?!\d)", path.name
        )
        if month_match:
            period = pd.Period(
                f"{month_match.group(1)}-{int(month_match.group(2)):02d}", freq="M"
            )
            if start is not None and period < start:
                continue
            if end is not None and period > end:
                continue
        else:
            year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", path.name)
            if year_match:
                year = int(year_match.group(1))
                if start is not None and year < start.year:
                    continue
                if end is not None and year > end.year:
                    continue
        selected.append(path)
    if not selected:
        raise FileNotFoundError(f"No {label} files overlap the requested period.")
    return selected


def selected_columns(all_columns: Iterable[object]) -> list[str]:
    normalized = {str(column).strip().upper() for column in all_columns}
    missing = REQUIRED_COLUMNS - normalized
    if missing:
        raise ValueError(f"T-100 file is missing required columns: {sorted(missing)}")
    return sorted((REQUIRED_COLUMNS | OPTIONAL_COLUMNS) & normalized)


def read_csv_chunks(
    stream_or_path: str | Path | BinaryIO, *, chunksize: int = 250_000
) -> Iterable[pd.DataFrame]:
    """Compatibility helper for direct CSV streams used by older notebooks."""
    header = pd.read_csv(stream_or_path, nrows=0)
    column_map = {
        str(column).strip().upper(): column for column in header.columns
    }
    columns = [column_map[column] for column in selected_columns(header.columns)]
    if hasattr(stream_or_path, "seek"):
        stream_or_path.seek(0)
    yield from pd.read_csv(
        stream_or_path,
        usecols=columns,
        chunksize=chunksize,
        low_memory=False,
    )


def read_file(path: Path) -> Iterable[pd.DataFrame]:
    for frame in read_table_chunks(path, wanted_columns=T100_COLUMNS):
        frame = normalize_columns(frame)
        columns = selected_columns(frame.columns)
        yield frame[columns]


def _resolve_marketing_columns(frame: pd.DataFrame) -> pd.DataFrame:
    frame = normalize_columns(frame)
    resolved: dict[str, str] = {}
    for logical, aliases in MARKETING_ALIASES.items():
        if actual := next((alias for alias in aliases if alias in frame.columns), None):
            resolved[logical] = actual
    missing = MARKETING_COLUMNS - set(resolved)
    if missing:
        raise ValueError(f"Marketing-carrier file is missing columns: {sorted(missing)}")
    return frame[list(resolved.values())].rename(
        columns={actual: logical for logical, actual in resolved.items()}
    )


def read_marketing_file(path: Path) -> Iterable[pd.DataFrame]:
    for frame in read_table_chunks(path, wanted_columns=MARKETING_INPUT_COLUMNS):
        yield _resolve_marketing_columns(frame)


def standardize_chunk(
    frame: pd.DataFrame,
    *,
    directional: bool,
    service_classes: set[str] | None,
    start_month: pd.Period | None,
    end_month: pd.Period | None,
) -> pd.DataFrame:
    frame = normalize_columns(frame)
    if service_classes is not None and "CLASS" in frame.columns:
        frame = frame[clean_code(frame["CLASS"]).isin(service_classes)].copy()
    year = pd.to_numeric(frame["YEAR"], errors="coerce").astype("Int64")
    month_number = pd.to_numeric(frame["MONTH"], errors="coerce").astype("Int64")
    frame["month"] = pd.PeriodIndex(
        year.astype(str) + "-" + month_number.astype(str), freq="M"
    )
    if start_month is not None:
        frame = frame[frame["month"] >= start_month]
    if end_month is not None:
        frame = frame[frame["month"] <= end_month]

    origin = clean_code(frame["ORIGIN"])
    destination = clean_code(frame["DEST"])
    airport_1, airport_2, route = canonical_airport_pair(
        origin, destination, directional=directional
    )
    origin_city = (
        frame["ORIGIN_CITY_NAME"].astype("string").str.strip()
        if "ORIGIN_CITY_NAME" in frame.columns
        else pd.Series(pd.NA, index=frame.index, dtype="string")
    )
    destination_city = (
        frame["DEST_CITY_NAME"].astype("string").str.strip()
        if "DEST_CITY_NAME" in frame.columns
        else pd.Series(pd.NA, index=frame.index, dtype="string")
    )
    if directional:
        city_1, city_2 = origin_city, destination_city
    else:
        forward = origin <= destination
        city_1 = origin_city.where(forward, destination_city)
        city_2 = destination_city.where(forward, origin_city)

    result = pd.DataFrame(
        {
            "route": route,
            "airport_1": airport_1,
            "airport_2": airport_2,
            "city_1": city_1,
            "city_2": city_2,
            "carrier": clean_code(frame["UNIQUE_CARRIER"]),
            "carrier_name": (
                frame["UNIQUE_CARRIER_NAME"].astype("string").str.strip()
                if "UNIQUE_CARRIER_NAME" in frame.columns
                else pd.Series(pd.NA, index=frame.index, dtype="string")
            ),
            "month": frame["month"],
            "departures": pd.to_numeric(
                frame["DEPARTURES_PERFORMED"], errors="coerce"
            ).fillna(0),
            "seats": pd.to_numeric(frame["SEATS"], errors="coerce").fillna(0),
            "passengers": pd.to_numeric(
                frame["PASSENGERS"], errors="coerce"
            ).fillna(0),
        }
    )
    return result[
        result["airport_1"].notna()
        & result["airport_2"].notna()
        & (result["airport_1"] != result["airport_2"])
        & result["carrier"].notna()
    ]


def first_nonempty(values: pd.Series):
    values = values.dropna()
    if values.empty:
        return pd.NA
    nonempty = values[values.astype(str).str.len() > 0]
    return nonempty.iloc[0] if len(nonempty) else pd.NA


def load_monthly_routes(args: argparse.Namespace) -> tuple[pd.DataFrame, int]:
    start = pd.Period(args.start_month, freq="M") if args.start_month else None
    end = pd.Period(args.end_month, freq="M") if args.end_month else None
    service_classes = (
        None
        if args.all_service_classes
        else {value.strip().upper() for value in args.service_classes}
    )
    pieces: list[pd.DataFrame] = []
    rows_read = 0
    keys = ["route", "airport_1", "airport_2", "carrier", "month"]
    for path in files_for_month_range(
        args.t100,
        label="T-100 input",
        start=start,
        end=end,
    ):
        for raw in read_file(path):
            rows_read += len(raw)
            clean = standardize_chunk(
                raw,
                directional=args.directional,
                service_classes=service_classes,
                start_month=start,
                end_month=end,
            )
            if clean.empty:
                continue
            pieces.append(
                clean.groupby(keys, observed=True, as_index=False).agg(
                    city_1=("city_1", "first"),
                    city_2=("city_2", "first"),
                    carrier_name=("carrier_name", "first"),
                    departures=("departures", "sum"),
                    seats=("seats", "sum"),
                    passengers=("passengers", "sum"),
                )
            )
    if not pieces:
        raise ValueError("No qualifying T-100 records were found.")
    combined = pd.concat(pieces, ignore_index=True)
    monthly = combined.groupby(keys, observed=True, as_index=False).agg(
        city_1=("city_1", "first"),
        city_2=("city_2", "first"),
        carrier_name=("carrier_name", "first"),
        departures=("departures", "sum"),
        seats=("seats", "sum"),
        passengers=("passengers", "sum"),
    )
    monthly["active"] = (
        (monthly["departures"] >= args.min_active_departures)
        & (monthly["seats"] > 0)
    )
    return monthly.sort_values(keys), rows_read


def _standardize_marketing_chunk(
    raw: pd.DataFrame,
    *,
    directional: bool,
    start: pd.Period | None,
    end: pd.Period | None,
) -> pd.DataFrame:
    year = pd.to_numeric(raw["YEAR"], errors="coerce").astype("Int64")
    month_number = pd.to_numeric(raw["MONTH"], errors="coerce").astype("Int64")
    period = pd.Series(
        pd.PeriodIndex(year.astype(str) + "-" + month_number.astype(str), freq="M"),
        index=raw.index,
    )
    keep = pd.Series(True, index=raw.index)
    if start is not None:
        keep &= period >= start
    if end is not None:
        keep &= period <= end
    raw = raw.loc[keep]
    period = period.loc[keep]
    airport_1, airport_2, route = canonical_airport_pair(
        raw["ORIGIN"], raw["DEST"], directional=directional
    )
    cancelled = pd.to_numeric(raw["CANCELLED"], errors="coerce").fillna(0)
    clean = pd.DataFrame(
        {
            "route": route,
            "airport_1": airport_1,
            "airport_2": airport_2,
            "carrier": clean_code(raw["MARKETING_AIRLINE_NETWORK"]),
            "operator": clean_code(raw["OPERATING_AIRLINE"]),
            "month": period,
            "flights": 1,
            "performed_flights": (cancelled < 0.5).astype(int),
        }
    )
    return clean[
        clean["airport_1"].notna()
        & clean["airport_2"].notna()
        & (clean["airport_1"] != clean["airport_2"])
        & clean["carrier"].notna()
        & clean["operator"].notna()
    ]


def load_marketing_monthly_routes(
    args: argparse.Namespace,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    start = pd.Period(args.start_month, freq="M") if args.start_month else None
    end = pd.Period(args.end_month, freq="M") if args.end_month else None
    pieces: list[pd.DataFrame] = []
    rows_read = 0
    detail_keys = [
        "route",
        "airport_1",
        "airport_2",
        "carrier",
        "operator",
        "month",
    ]
    for path in files_for_month_range(
        [args.marketing_data],
        label="marketing-carrier input",
        start=start,
        end=end,
    ):
        for raw in read_marketing_file(path):
            rows_read += len(raw)
            clean = _standardize_marketing_chunk(
                raw, directional=args.directional, start=start, end=end
            )
            if not clean.empty:
                pieces.append(
                    clean.groupby(detail_keys, observed=True, as_index=False).agg(
                        flights=("flights", "sum"),
                        performed_flights=("performed_flights", "sum"),
                    )
                )
    if not pieces:
        raise ValueError("No usable marketing-carrier records were found.")
    operator_detail = pd.concat(pieces, ignore_index=True).groupby(
        detail_keys, observed=True, as_index=False
    ).agg(
        flights=("flights", "sum"),
        performed_flights=("performed_flights", "sum"),
    )
    monthly_keys = ["route", "airport_1", "airport_2", "carrier", "month"]
    monthly = operator_detail.groupby(
        monthly_keys, observed=True, as_index=False
    ).agg(
        departures=("performed_flights", "sum"),
        scheduled_flights=("flights", "sum"),
    )
    monthly["carrier_name"] = monthly["carrier"].map(KNOWN_CARRIER_NAMES)
    monthly["city_1"] = pd.NA
    monthly["city_2"] = pd.NA
    monthly["seats"] = np.nan
    monthly["passengers"] = np.nan
    monthly["active"] = monthly["departures"] >= args.min_active_departures
    return monthly.sort_values(monthly_keys), operator_detail, rows_read
