"""Chunked input loading and standardization for the quarterly ULCC pipeline."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Sequence

import numpy as np
import pandas as pd

from airline_research.bts import (
    canonical_airport_pair,
    discover_files,
    normalize_columns,
    read_table_chunks,
)


ALIASES = {
    "year": ("YEAR",),
    "quarter": ("QUARTER",),
    "origin": ("ORIGIN", "ORIGIN_AIRPORT"),
    "destination": ("DEST", "DESTINATION", "DEST_AIRPORT"),
    "carrier": (
        "OP_UNIQUE_CARRIER",
        "UNIQUE_CARRIER",
        "REPORTING_CARRIER",
        "TK_CARRIER",
        "TKCARRIER",
        "RPCARRIER",
        "OPCARRIER",
        "CARRIER",
    ),
    "departures": (
        "DEPARTURES_PERFORMED",
        "DEPARTURES_SCHEDULED",
        "DEPARTURES",
    ),
    "seats": ("SEATS",),
    "t100_passengers": ("PASSENGERS",),
    "fare": ("MARKET_FARE", "MKT_FARE", "MKTFARE", "ITIN_FARE", "FARE"),
    "db1b_passengers": ("PASSENGERS",),
    "distance": (
        "MARKET_MILES_FLOWN",
        "MKT_MILES_FLW",
        "MKTMILESFLOWN",
        "MKT_DISTANCE",
        "MKTDISTANCE",
        "DISTANCE",
        "NONSTOP_MILES",
        "NONSTOPMILES",
    ),
    "arr_delay": ("ARR_DELAY", "ARRIVAL_DELAY"),
    "cancelled": ("CANCELLED", "CANCELLATIONS"),
}

T100_LOGICAL_COLUMNS = {
    "year",
    "quarter",
    "origin",
    "destination",
    "carrier",
    "departures",
    "seats",
    "t100_passengers",
    "distance",
    "arr_delay",
    "cancelled",
}
DB1B_LOGICAL_COLUMNS = {
    "year",
    "quarter",
    "origin",
    "destination",
    "carrier",
    "fare",
    "db1b_passengers",
    "distance",
}


def _wanted_columns(logical_columns: set[str]) -> set[str]:
    return {
        alias
        for logical_name in logical_columns
        for alias in ALIASES[logical_name]
    }


T100_COLUMNS = _wanted_columns(T100_LOGICAL_COLUMNS)
DB1B_COLUMNS = _wanted_columns(DB1B_LOGICAL_COLUMNS)


def choose_column(
    frame: pd.DataFrame, logical_name: str, *, required: bool = False
) -> str | None:
    for alias in ALIASES[logical_name]:
        if alias in frame.columns:
            return alias
    if required:
        raise ValueError(
            f"Missing {logical_name!r}; expected one of {ALIASES[logical_name]}. "
            f"Available columns include: {list(frame.columns[:30])}"
        )
    return None


def quarter_from_filename(path: Path) -> pd.Period | None:
    match = re.search(r"(20\d{2})[^0-9]?[Qq]([1-4])", path.name)
    if match:
        return pd.Period(f"{match.group(1)}Q{match.group(2)}", freq="Q")
    return None


def files_for_quarter_range(
    inputs: Sequence[str],
    *,
    kind: str,
    start: str | None,
    end: str | None,
) -> list[Path]:
    """Skip archives whose filename is provably outside the requested period."""
    files = discover_files(inputs, label=f"{kind} input")
    start_period = pd.Period(start, freq="Q") if start else None
    end_period = pd.Period(end, freq="Q") if end else None
    if start_period is None and end_period is None:
        return files
    selected: list[Path] = []
    for path in files:
        file_quarter = quarter_from_filename(path)
        if file_quarter is not None:
            if start_period is not None and file_quarter < start_period:
                continue
            if end_period is not None and file_quarter > end_period:
                continue
        elif kind == "t100":
            year_match = re.search(r"(?<!\d)(20\d{2})(?!\d)", path.name)
            if year_match:
                year = int(year_match.group(1))
                if start_period is not None and year < start_period.year:
                    continue
                if end_period is not None and year > end_period.year:
                    continue
        selected.append(path)
    if not selected:
        raise FileNotFoundError(f"No {kind} files overlap the requested period.")
    return selected


def make_quarter(frame: pd.DataFrame, source: Path) -> pd.Series:
    year_col = choose_column(frame, "year", required=False)
    quarter_col = choose_column(frame, "quarter", required=False)
    if year_col and quarter_col:
        year = pd.to_numeric(frame[year_col], errors="coerce").astype("Int64")
        quarter = pd.to_numeric(frame[quarter_col], errors="coerce").astype("Int64")
        return pd.Series(
            pd.PeriodIndex(year.astype(str) + "Q" + quarter.astype(str), freq="Q"),
            index=frame.index,
        )
    inferred = quarter_from_filename(source)
    if inferred is None:
        raise ValueError(
            f"{source}: need YEAR and QUARTER columns or a YYYYQ# filename token."
        )
    return pd.Series(inferred, index=frame.index, dtype="period[Q-DEC]")


def canonical_market(origin: pd.Series, destination: pd.Series) -> pd.DataFrame:
    airport_1, airport_2, market = canonical_airport_pair(origin, destination)
    return pd.DataFrame(
        {"airport_1": airport_1, "airport_2": airport_2, "market": market}
    )


def numeric(frame: pd.DataFrame, logical_name: str, default: float = 0) -> pd.Series:
    column = choose_column(frame, logical_name, required=False)
    if column is None:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(default)


def standardize_t100(frame: pd.DataFrame, source: Path) -> pd.DataFrame:
    frame = normalize_columns(frame)
    origin = choose_column(frame, "origin", required=True)
    destination = choose_column(frame, "destination", required=True)
    carrier = choose_column(frame, "carrier", required=True)
    market = canonical_market(frame[origin], frame[destination])
    result = market.assign(
        quarter=make_quarter(frame, source),
        carrier=frame[carrier].astype("string").str.strip().str.upper(),
        departures=numeric(frame, "departures"),
        seats=numeric(frame, "seats"),
        passengers=numeric(frame, "t100_passengers"),
        distance=numeric(frame, "distance", np.nan),
        arr_delay=numeric(frame, "arr_delay", np.nan),
        cancelled=numeric(frame, "cancelled", np.nan),
    )
    return result.dropna(subset=["quarter", "airport_1", "airport_2", "carrier"])


def standardize_db1b(frame: pd.DataFrame, source: Path) -> pd.DataFrame:
    frame = normalize_columns(frame)
    origin = choose_column(frame, "origin", required=True)
    destination = choose_column(frame, "destination", required=True)
    carrier = choose_column(frame, "carrier", required=False)
    fare = choose_column(frame, "fare", required=True)
    passengers = choose_column(frame, "db1b_passengers", required=False)
    market = canonical_market(frame[origin], frame[destination])
    result = market.assign(
        quarter=make_quarter(frame, source),
        ticket_carrier=(
            frame[carrier].astype("string").str.strip().str.upper()
            if carrier
            else pd.Series(pd.NA, index=frame.index, dtype="string")
        ),
        fare=pd.to_numeric(frame[fare], errors="coerce"),
        fare_passengers=(
            pd.to_numeric(frame[passengers], errors="coerce").fillna(1)
            if passengers
            else 1.0
        ),
        db1b_distance=numeric(frame, "distance", np.nan),
    )
    return result.dropna(subset=["quarter", "airport_1", "airport_2", "fare"])


def _read_kind(path: Path, kind: str) -> Iterable[pd.DataFrame]:
    columns = T100_COLUMNS if kind == "t100" else DB1B_COLUMNS
    standardizer = standardize_t100 if kind == "t100" else standardize_db1b
    for raw_frame in read_table_chunks(path, wanted_columns=columns):
        yield standardizer(raw_frame, path)


def load_standardized(inputs: Sequence[str], kind: str) -> pd.DataFrame:
    if kind not in {"t100", "db1b"}:
        raise ValueError("kind must be 't100' or 'db1b'.")
    pieces = [
        frame
        for path in discover_files(inputs, label=kind)
        for frame in _read_kind(path, kind)
    ]
    if not pieces:
        raise ValueError(f"No usable {kind} records found.")
    return pd.concat(pieces, ignore_index=True)


def filter_quarters(
    frame: pd.DataFrame, start: str | None, end: str | None
) -> pd.DataFrame:
    keep = pd.Series(True, index=frame.index)
    if start:
        keep &= frame["quarter"] >= pd.Period(start, freq="Q")
    if end:
        keep &= frame["quarter"] <= pd.Period(end, freq="Q")
    return frame.loc[keep].copy()


def aggregate_t100(t100: pd.DataFrame) -> pd.DataFrame:
    keys = ["market", "airport_1", "airport_2", "carrier", "quarter"]
    grouped = (
        t100.groupby(keys, observed=True, as_index=False)
        .agg(
            departures=("departures", "sum"),
            seats=("seats", "sum"),
            passengers=("passengers", "sum"),
            distance=("distance", "mean"),
            arr_delay_total=("arr_delay", "sum"),
            cancellations=("cancelled", "sum"),
        )
        .sort_values(keys)
    )
    grouped["active"] = (
        (grouped["departures"] > 0)
        | (grouped["seats"] > 0)
        | (grouped["passengers"] > 0)
    )
    return grouped


def load_t100_aggregated(
    inputs: Sequence[str],
    start: str | None,
    end: str | None,
) -> tuple[pd.DataFrame, int]:
    pieces: list[pd.DataFrame] = []
    row_count = 0
    for path in files_for_quarter_range(
        inputs, kind="t100", start=start, end=end
    ):
        for standardized in _read_kind(path, "t100"):
            standardized = filter_quarters(standardized, start, end)
            row_count += len(standardized)
            if not standardized.empty:
                pieces.append(aggregate_t100(standardized))
    if not pieces:
        raise ValueError("No usable T-100 records found in the requested period.")
    combined = pd.concat(pieces, ignore_index=True)
    keys = ["market", "airport_1", "airport_2", "carrier", "quarter"]
    final = (
        combined.groupby(keys, observed=True, as_index=False)
        .agg(
            departures=("departures", "sum"),
            seats=("seats", "sum"),
            passengers=("passengers", "sum"),
            distance=("distance", "mean"),
            arr_delay_total=("arr_delay_total", "sum"),
            cancellations=("cancellations", "sum"),
        )
        .sort_values(keys)
    )
    final["active"] = (
        (final["departures"] > 0)
        | (final["seats"] > 0)
        | (final["passengers"] > 0)
    )
    return final, row_count


def load_db1b_aggregated(
    inputs: Sequence[str],
    start: str | None,
    end: str | None,
    fare_min: float,
    fare_max: float,
) -> tuple[pd.DataFrame, int]:
    pieces: list[pd.DataFrame] = []
    row_count = 0
    keys = ["market", "airport_1", "airport_2", "quarter"]
    for path in files_for_quarter_range(
        inputs, kind="db1b", start=start, end=end
    ):
        for standardized in _read_kind(path, "db1b"):
            standardized = filter_quarters(standardized, start, end)
            row_count += len(standardized)
            clean = standardized[
                standardized["fare"].between(fare_min, fare_max, inclusive="both")
                & (standardized["fare_passengers"] > 0)
            ].copy()
            if clean.empty:
                continue
            clean["fare_weight"] = clean["fare"] * clean["fare_passengers"]
            has_distance = clean["db1b_distance"].notna()
            clean["distance_weight"] = (
                clean["db1b_distance"] * clean["fare_passengers"]
            )
            clean["distance_passengers"] = clean["fare_passengers"].where(
                has_distance, 0
            )
            pieces.append(
                clean.groupby(keys, observed=True, as_index=False).agg(
                    fare_weight=("fare_weight", "sum"),
                    sampled_passengers=("fare_passengers", "sum"),
                    db1b_records=("fare", "size"),
                    distance_weight=("distance_weight", "sum"),
                    distance_passengers=("distance_passengers", "sum"),
                )
            )
    if not pieces:
        raise ValueError("No usable DB1B fare records found in the requested period.")
    combined = pd.concat(pieces, ignore_index=True)
    final = combined.groupby(keys, observed=True, as_index=False).agg(
        fare_weight=("fare_weight", "sum"),
        sampled_passengers=("sampled_passengers", "sum"),
        db1b_records=("db1b_records", "sum"),
        distance_weight=("distance_weight", "sum"),
        distance_passengers=("distance_passengers", "sum"),
    )
    final["average_fare"] = final["fare_weight"] / final["sampled_passengers"]
    final["log_average_fare"] = np.log(final["average_fare"])
    final["db1b_distance"] = np.where(
        final["distance_passengers"] > 0,
        final["distance_weight"] / final["distance_passengers"],
        np.nan,
    )
    return (
        final.drop(columns=["fare_weight", "distance_weight", "distance_passengers"]),
        row_count,
    )


def aggregate_db1b(
    db1b: pd.DataFrame, fare_min: float, fare_max: float
) -> pd.DataFrame:
    """Aggregate an already standardized DB1B frame (useful for testing)."""
    clean = db1b[
        db1b["fare"].between(fare_min, fare_max, inclusive="both")
        & (db1b["fare_passengers"] > 0)
    ].copy()
    clean["fare_weight"] = clean["fare"] * clean["fare_passengers"]
    grouped = clean.groupby(
        ["market", "airport_1", "airport_2", "quarter"],
        observed=True,
        as_index=False,
    ).agg(
        fare_weight=("fare_weight", "sum"),
        sampled_passengers=("fare_passengers", "sum"),
        median_fare=("fare", "median"),
        db1b_records=("fare", "size"),
        db1b_distance=("db1b_distance", "mean"),
    )
    grouped["average_fare"] = grouped["fare_weight"] / grouped["sampled_passengers"]
    grouped["log_average_fare"] = np.log(grouped["average_fare"])
    return grouped.drop(columns="fare_weight")
