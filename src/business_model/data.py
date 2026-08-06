"""Load BTS inputs and construct the paper's 20 carrier-year measures."""

from __future__ import annotations

import calendar
import re
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from ..bts import clean_code, discover_files, normalize_columns, read_table_chunks
from .config import DEFAULT_CARRIER_NAMES, METRIC_SPECS


T100_COLUMNS = {
    "YEAR",
    "MONTH",
    "CLASS",
    "UNIQUE_CARRIER",
    "UNIQUE_CARRIER_NAME",
    "ORIGIN",
    "DEST",
    "DEPARTURES_PERFORMED",
    "SEATS",
    "PASSENGERS",
    "DISTANCE",
    "AIR_TIME",
}
DB1B_COLUMNS = {
    "YEAR",
    "QUARTER",
    "TK_CARRIER",
    "TKCARRIER",
    "RP_CARRIER",
    "RPCARRIER",
    "PASSENGERS",
    "MKT_FARE",
    "MKTFARE",
    "ORIGIN_COUNTRY",
    "ORIGINCOUNTRY",
    "DEST_COUNTRY",
    "DESTCOUNTRY",
}
ONTIME_COLUMNS = {
    "YEAR",
    "MONTH",
    "MKT_UNIQUE_CARRIER",
    "MARKETING_AIRLINE_NETWORK",
    "OP_UNIQUE_CARRIER",
    "OPERATING_AIRLINE",
    "CANCELLED",
    "DIVERTED",
    "DEP_DEL15",
    "DEPDELAY15",
    "DEP_DELAY_NEW",
    "ARR_DEL15",
    "ARRDELAY15",
    "ARR_DELAY_NEW",
}


def existing_files(inputs: Sequence[str | Path] | None, *, label: str) -> list[Path]:
    """Return supported files, allowing optional input directories to be absent."""
    if not inputs:
        return []
    existing = [Path(value).expanduser() for value in inputs if Path(value).expanduser().exists()]
    if not existing:
        return []
    return discover_files(existing, label=label)


def _files_in_year_range(
    files: Sequence[Path],
    *,
    start_year: int | None,
    end_year: int | None,
) -> list[Path]:
    """Skip files whose names unambiguously place them outside the sample."""
    selected: list[Path] = []
    for path in files:
        match = re.search(r"(?<!\d)(20\d{2})(?!\d)", path.name)
        if match:
            year = int(match.group(1))
            if start_year is not None and year < start_year:
                continue
            if end_year is not None and year > end_year:
                continue
        selected.append(path)
    return selected


def _first_column(frame: pd.DataFrame, aliases: Sequence[str]) -> str | None:
    return next((column for column in aliases if column in frame.columns), None)


def _numeric(frame: pd.DataFrame, aliases: Sequence[str], default=np.nan) -> pd.Series:
    column = _first_column(frame, aliases)
    if column is None:
        return pd.Series(default, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def _carrier(frame: pd.DataFrame) -> pd.Series:
    column = _first_column(
        frame,
        (
            "UNIQUE_CARRIER",
            "UNIQUECARRIER",
            "MKT_UNIQUE_CARRIER",
            "MARKETING_AIRLINE_NETWORK",
            "CARRIER",
        ),
    )
    if column is None:
        raise ValueError(f"No carrier column found; available columns: {list(frame.columns[:30])}")
    return clean_code(frame[column])


def _carrier_name(frame: pd.DataFrame, carrier: pd.Series) -> pd.Series:
    column = _first_column(
        frame, ("UNIQUE_CARRIER_NAME", "UNIQUECARRIERNAME", "CARRIER_NAME", "CARRIERNAME")
    )
    if column is not None:
        names = frame[column].astype("string").str.strip()
        return names.where(names.notna() & names.ne(""), carrier.map(DEFAULT_CARRIER_NAMES))
    return carrier.map(DEFAULT_CARRIER_NAMES).fillna(carrier)


def _year_filter(
    frame: pd.DataFrame,
    *,
    start_year: int | None,
    end_year: int | None,
) -> tuple[pd.DataFrame, pd.Series]:
    if "YEAR" not in frame:
        raise ValueError("Input file is missing YEAR.")
    year = pd.to_numeric(frame["YEAR"], errors="coerce").astype("Int64")
    keep = year.notna()
    if start_year is not None:
        keep &= year >= start_year
    if end_year is not None:
        keep &= year <= end_year
    return frame.loc[keep].copy(), year.loc[keep].astype(int)


def _carrier_filter(frame: pd.DataFrame, carriers: set[str] | None) -> pd.DataFrame:
    if carriers is None:
        return frame
    return frame[frame["carrier"].isin(carriers)].copy()


def _days_in_year(year: pd.Series) -> pd.Series:
    return year.map(lambda value: 366 if calendar.isleap(int(value)) else 365).astype(float)


def load_t100(
    inputs: Sequence[str | Path],
    *,
    start_year: int | None,
    end_year: int | None,
    carriers: set[str] | None,
    min_departures: float,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Aggregate scheduled domestic T-100 segments to carrier-year metrics."""
    files = _files_in_year_range(
        existing_files(inputs, label="T-100"),
        start_year=start_year,
        end_year=end_year,
    )
    if not files:
        raise FileNotFoundError("No T-100 files were found.")
    pieces: list[pd.DataFrame] = []
    rows_read = 0
    rows_kept = 0
    for path in files:
        for raw in read_table_chunks(path, wanted_columns=T100_COLUMNS):
            rows_read += len(raw)
            frame = normalize_columns(raw)
            frame, year = _year_filter(frame, start_year=start_year, end_year=end_year)
            if frame.empty:
                continue
            if "CLASS" in frame:
                frame = frame[clean_code(frame["CLASS"]).eq("F")].copy()
                year = year.loc[frame.index]
            carrier = _carrier(frame)
            origin_col = _first_column(frame, ("ORIGIN",))
            dest_col = _first_column(frame, ("DEST", "DESTINATION"))
            if origin_col is None or dest_col is None:
                raise ValueError(f"{path} is missing ORIGIN or DEST.")
            clean = pd.DataFrame(
                {
                    "carrier": carrier,
                    "carrier_name": _carrier_name(frame, carrier),
                    "year": year,
                    "origin": clean_code(frame[origin_col]),
                    "destination": clean_code(frame[dest_col]),
                    "departures": _numeric(frame, ("DEPARTURES_PERFORMED",)),
                    "seats": _numeric(frame, ("SEATS",)),
                    "passengers": _numeric(frame, ("PASSENGERS",)),
                    "distance": _numeric(frame, ("DISTANCE",)),
                    "air_time_minutes": _numeric(frame, ("AIR_TIME",)),
                }
            )
            clean = _carrier_filter(clean, carriers)
            clean = clean[
                clean["carrier"].notna()
                & clean["origin"].notna()
                & clean["destination"].notna()
                & clean["departures"].fillna(0).gt(0)
                & clean["distance"].fillna(0).gt(0)
            ].copy()
            if clean.empty:
                continue
            clean["asm"] = clean["seats"].fillna(0) * clean["distance"]
            clean["rpm"] = clean["passengers"].fillna(0) * clean["distance"]
            clean["departure_miles"] = clean["departures"] * clean["distance"]
            rows_kept += len(clean)
            pieces.append(clean)
    if not pieces:
        raise ValueError("No scheduled passenger T-100 records remained after filtering.")

    segments = pd.concat(pieces, ignore_index=True)
    keys = ["carrier", "carrier_name", "year", "origin", "destination"]
    segments = segments.groupby(keys, observed=True, as_index=False).agg(
        departures=("departures", "sum"),
        passengers=("passengers", "sum"),
        asm=("asm", "sum"),
        rpm=("rpm", "sum"),
        departure_miles=("departure_miles", "sum"),
        air_time_minutes=("air_time_minutes", "sum"),
    )

    carrier_keys = ["carrier", "carrier_name", "year"]
    totals = segments.groupby(carrier_keys, observed=True, as_index=False).agg(
        departures=("departures", "sum"),
        passengers=("passengers", "sum"),
        asm=("asm", "sum"),
        rpm=("rpm", "sum"),
        departure_miles=("departure_miles", "sum"),
        air_time_minutes=("air_time_minutes", "sum"),
        origin_airports=("origin", "nunique"),
    )
    served = pd.concat(
        [
            segments[carrier_keys + ["origin"]].rename(columns={"origin": "airport"}),
            segments[carrier_keys + ["destination"]].rename(
                columns={"destination": "airport"}
            ),
        ],
        ignore_index=True,
    )
    destinations = (
        served.groupby(carrier_keys, observed=True)["airport"]
        .nunique()
        .rename("total_destinations")
        .reset_index()
    )
    totals = totals.merge(destinations, on=carrier_keys, how="left")
    totals = totals[totals["departures"] >= min_departures].copy()
    totals["days_in_year"] = _days_in_year(totals["year"])
    totals["network_density_departures_per_airport_day"] = (
        totals["departures"] / totals["origin_airports"] / totals["days_in_year"]
    )
    totals["average_sector_miles"] = totals["departure_miles"] / totals["departures"]
    totals["load_factor_pct"] = totals["rpm"] / totals["asm"] * 100
    totals["aircraft_hours"] = totals["air_time_minutes"] / 60

    airport_activity = segments.groupby(
        carrier_keys + ["origin"], observed=True, as_index=False
    ).agg(departures=("departures", "sum"))
    airport_activity = airport_activity.merge(
        totals[carrier_keys], on=carrier_keys, how="inner"
    )
    airport_activity["airport_rank"] = airport_activity.groupby(
        carrier_keys, observed=True
    )["departures"].rank(method="first", ascending=False)
    top_airports = airport_activity[airport_activity["airport_rank"] <= 5].rename(
        columns={"origin": "airport"}
    )
    return (
        totals,
        top_airports.sort_values(carrier_keys + ["airport_rank"]),
        {"files": len(files), "rows_read": rows_read, "rows_kept": rows_kept},
    )


def _domestic_rows(frame: pd.DataFrame) -> pd.DataFrame:
    region_col = _first_column(
        frame, ("CARRIER_REGION", "CARRIERREGION", "REGION", "ENTITY")
    )
    if region_col is None:
        return frame
    values = clean_code(frame[region_col])
    domestic = values.isin({"D", "DOMESTIC"}) | values.str.contains(
        "DOMESTIC", na=False
    )
    return frame.loc[domestic].copy() if domestic.any() else frame


def _load_form41_numeric(
    inputs: Sequence[str | Path] | None,
    *,
    fields: dict[str, Sequence[str]],
    start_year: int | None,
    end_year: int | None,
    carriers: set[str] | None,
    label: str,
    quarterly_coverage_column: str | None,
) -> tuple[pd.DataFrame, int]:
    files = _files_in_year_range(
        existing_files(inputs, label=label),
        start_year=start_year,
        end_year=end_year,
    )
    pieces: list[pd.DataFrame] = []
    rows_read = 0
    wanted = {
        "YEAR",
        "QUARTER",
        "UNIQUE_CARRIER",
        "UNIQUECARRIER",
        "CARRIER",
        "UNIQUE_CARRIER_NAME",
        "UNIQUECARRIERNAME",
        "CARRIER_NAME",
        "CARRIERNAME",
        "CARRIER_REGION",
        "CARRIERREGION",
        "REGION",
        "ENTITY",
    } | {alias for aliases in fields.values() for alias in aliases}
    for path in files:
        for raw in read_table_chunks(path, wanted_columns=wanted):
            rows_read += len(raw)
            frame = normalize_columns(raw)
            frame, year = _year_filter(frame, start_year=start_year, end_year=end_year)
            frame = _domestic_rows(frame)
            year = year.loc[frame.index]
            if frame.empty:
                continue
            carrier = _carrier(frame)
            clean = pd.DataFrame(
                {
                    "carrier": carrier,
                    "carrier_name": _carrier_name(frame, carrier),
                    "year": year,
                    "_quarter": _numeric(frame, ("QUARTER",)),
                    **{name: _numeric(frame, aliases) for name, aliases in fields.items()},
                }
            )
            clean = _carrier_filter(clean, carriers)
            if not clean.empty:
                pieces.append(clean)
    if not pieces:
        return pd.DataFrame(columns=["carrier", "carrier_name", "year", *fields]), rows_read
    combined = pd.concat(pieces, ignore_index=True)
    keys = ["carrier", "carrier_name", "year"]
    result = combined.groupby(keys, observed=True)[list(fields)].sum(min_count=1).reset_index()
    if quarterly_coverage_column:
        coverage = (
            combined.groupby(keys, observed=True)["_quarter"]
            .nunique()
            .rename(quarterly_coverage_column)
            .reset_index()
        )
        result = result.merge(coverage, on=keys, how="left")
        incomplete = result[quarterly_coverage_column] < 4
        result.loc[incomplete, list(fields)] = np.nan
    return result, rows_read


def load_p12(
    inputs: Sequence[str | Path] | None,
    *,
    start_year: int | None,
    end_year: int | None,
    carriers: set[str] | None,
) -> tuple[pd.DataFrame, int]:
    frame, rows = _load_form41_numeric(
        inputs,
        fields={
            "operating_expense_usd": ("OP_EXPENSES", "OPEXPENSES"),
            "passenger_revenue_usd": ("TRANS_REV_PAX", "TRANSREVPAX"),
            "operating_revenue_usd": ("OP_REVENUES", "OPREVENUES"),
            "baggage_fees_usd": ("PROP_BAG", "PROPBAG"),
            "reservation_cancellation_fees_usd": (
                "RES_CANCEL_FEES",
                "RESCANCELFEES",
            ),
        },
        start_year=start_year,
        end_year=end_year,
        carriers=carriers,
        label="Form 41 P-1.2",
        quarterly_coverage_column="p12_quarters",
    )
    money = [column for column in frame if column.endswith("_usd")]
    frame[money] = frame[money] * 1000
    return frame, rows


def load_p6(
    inputs: Sequence[str | Path] | None,
    *,
    start_year: int | None,
    end_year: int | None,
    carriers: set[str] | None,
) -> tuple[pd.DataFrame, int]:
    frame, rows = _load_form41_numeric(
        inputs,
        fields={
            "personnel_cost_usd": (
                "SALARIES_BENEFITS",
                "SALARIESBENEFITS",
                "SALARIES",
            )
        },
        start_year=start_year,
        end_year=end_year,
        carriers=carriers,
        label="Form 41 P-6",
        quarterly_coverage_column="p6_quarters",
    )
    if "personnel_cost_usd" in frame:
        frame["personnel_cost_usd"] *= 1000
    return frame, rows


def load_p10(
    inputs: Sequence[str | Path] | None,
    *,
    start_year: int | None,
    end_year: int | None,
    carriers: set[str] | None,
) -> tuple[pd.DataFrame, int]:
    frame, rows = _load_form41_numeric(
        inputs,
        fields={
            "total_employees": ("TOTAL", "TOTAL_EMPLOYEES"),
            "pilots_copilots": ("PILOTS_COPILOTS", "PILOTSCOPILOTS"),
            "other_flight_personnel": (
                "OTHER_FLIGHT_PERSONNEL",
                "OTHERFLIGHTPERSONNEL",
                "OTHER_FLT_PERS",
            ),
        },
        start_year=start_year,
        end_year=end_year,
        carriers=carriers,
        label="Form 41 P-10",
        quarterly_coverage_column=None,
    )
    if not frame.empty:
        frame["flight_crew_employees"] = frame[
            ["pilots_copilots", "other_flight_personnel"]
        ].sum(axis=1, min_count=1)
    return frame, rows


def _fleet_family(value: object) -> str:
    text = re.sub(r"[^A-Z0-9]+", "", str(value).upper())
    families = (
        "B717",
        "B737",
        "B747",
        "B757",
        "B767",
        "B777",
        "B787",
        "A220",
        "A300",
        "A310",
        "A320",
        "A330",
        "A340",
        "A350",
        "A380",
        "CRJ",
        "ERJ",
        "EMB",
        "E170",
        "E175",
        "E190",
        "E195",
        "MD80",
        "MD90",
        "DC9",
        "ATR",
        "DHC8",
    )
    for family in families:
        if family in text:
            return "EJET" if family in {"E170", "E175", "E190", "E195"} else family
    return text or "UNKNOWN"


def load_b43(
    inputs: Sequence[str | Path] | None,
    *,
    start_year: int | None,
    end_year: int | None,
    carriers: set[str] | None,
) -> tuple[pd.DataFrame, int]:
    files = _files_in_year_range(
        existing_files(inputs, label="Form 41 B-43"),
        start_year=start_year,
        end_year=end_year,
    )
    wanted = {
        "YEAR",
        "UNIQUE_CARRIER",
        "UNIQUECARRIER",
        "CARRIER",
        "UNIQUE_CARRIER_NAME",
        "UNIQUECARRIERNAME",
        "CARRIER_NAME",
        "CARRIERNAME",
        "TAIL_NUMBER",
        "TAILNUMBER",
        "MODEL",
        "AIRCRAFT_TYPE",
        "AIRCRAFTTYPE",
        "AIRCRAFT_STATUS",
        "AIRCRAFTSTATUS",
        "OPERATING_STATUS",
        "OPERATINGSTATUS",
    }
    pieces: list[pd.DataFrame] = []
    rows_read = 0
    for path in files:
        for raw in read_table_chunks(path, wanted_columns=wanted):
            rows_read += len(raw)
            frame = normalize_columns(raw)
            frame, year = _year_filter(frame, start_year=start_year, end_year=end_year)
            if frame.empty:
                continue
            carrier = _carrier(frame)
            tail_col = _first_column(frame, ("TAIL_NUMBER", "TAILNUMBER"))
            model_col = _first_column(frame, ("MODEL", "AIRCRAFT_TYPE", "AIRCRAFTTYPE"))
            if tail_col is None or model_col is None:
                continue
            clean = pd.DataFrame(
                {
                    "carrier": carrier,
                    "carrier_name": _carrier_name(frame, carrier),
                    "year": year,
                    "tail_number": clean_code(frame[tail_col]),
                    "model": frame[model_col],
                }
            )
            operating_col = _first_column(frame, ("OPERATING_STATUS", "OPERATINGSTATUS"))
            if operating_col is not None:
                operating = clean_code(frame[operating_col])
                recognized = operating.isin({"Y", "YES", "1", "ACTIVE", "OPERATING"})
                if recognized.any():
                    clean = clean.loc[recognized]
            status_col = _first_column(frame, ("AIRCRAFT_STATUS", "AIRCRAFTSTATUS"))
            if status_col is not None and not clean.empty:
                status = clean_code(frame.loc[clean.index, status_col])
                inactive = status.str.contains(
                    "RETIRED|DESTROYED|SCRAPPED|STORED|INACTIVE", na=False
                )
                clean = clean.loc[~inactive]
            clean = _carrier_filter(clean, carriers)
            clean = clean[clean["tail_number"].notna() & clean["tail_number"].ne("")]
            if not clean.empty:
                clean["fleet_family"] = clean["model"].map(_fleet_family)
                pieces.append(clean)
    columns = ["carrier", "carrier_name", "year", "aircraft_count", "fleet_uniformity_pct"]
    if not pieces:
        return pd.DataFrame(columns=columns), rows_read
    fleet = pd.concat(pieces, ignore_index=True).drop_duplicates(
        ["carrier", "year", "tail_number"]
    )
    keys = ["carrier", "carrier_name", "year"]
    counts = fleet.groupby(keys, observed=True).agg(
        aircraft_count=("tail_number", "nunique")
    )
    family_counts = fleet.groupby(keys + ["fleet_family"], observed=True)[
        "tail_number"
    ].nunique()
    most_common = family_counts.groupby(level=keys).max().rename("most_common_family_count")
    result = counts.join(most_common).reset_index()
    result["fleet_uniformity_pct"] = (
        result["most_common_family_count"] / result["aircraft_count"] * 100
    )
    return result[columns], rows_read


def load_db1b(
    inputs: Sequence[str | Path] | None,
    *,
    start_year: int | None,
    end_year: int | None,
    carriers: set[str] | None,
    min_quarters: int,
) -> tuple[pd.DataFrame, int]:
    files = _files_in_year_range(
        existing_files(inputs, label="DB1B"),
        start_year=start_year,
        end_year=end_year,
    )
    pieces: list[pd.DataFrame] = []
    rows_read = 0
    for path in files:
        for raw in read_table_chunks(path, wanted_columns=DB1B_COLUMNS):
            rows_read += len(raw)
            frame = normalize_columns(raw)
            frame, year = _year_filter(frame, start_year=start_year, end_year=end_year)
            if frame.empty:
                continue
            origin_country = _first_column(frame, ("ORIGIN_COUNTRY", "ORIGINCOUNTRY"))
            dest_country = _first_column(frame, ("DEST_COUNTRY", "DESTCOUNTRY"))
            if origin_country and dest_country:
                domestic = clean_code(frame[origin_country]).isin({"US", "USA"}) & clean_code(
                    frame[dest_country]
                ).isin({"US", "USA"})
                frame = frame.loc[domestic]
                year = year.loc[frame.index]
            carrier_col = _first_column(
                frame, ("TK_CARRIER", "TKCARRIER", "RP_CARRIER", "RPCARRIER")
            )
            fare_col = _first_column(frame, ("MKT_FARE", "MKTFARE"))
            if carrier_col is None or fare_col is None:
                continue
            passenger = _numeric(frame, ("PASSENGERS",)).fillna(1)
            clean = pd.DataFrame(
                {
                    "carrier": clean_code(frame[carrier_col]),
                    "year": year,
                    "quarter": _numeric(frame, ("QUARTER",)),
                    "fare_weight": passenger,
                    "weighted_fare": pd.to_numeric(frame[fare_col], errors="coerce")
                    * passenger,
                }
            )
            clean = _carrier_filter(clean, carriers)
            clean = clean[
                clean["carrier"].notna()
                & clean["fare_weight"].gt(0)
                & clean["weighted_fare"].notna()
            ]
            if not clean.empty:
                pieces.append(
                    clean.groupby(
                        ["carrier", "year", "quarter"],
                        observed=True,
                        as_index=False,
                    ).agg(
                        fare_weight=("fare_weight", "sum"),
                        weighted_fare=("weighted_fare", "sum"),
                    )
                )
    columns = ["carrier", "year", "db1b_average_fare_usd", "db1b_quarters"]
    if not pieces:
        return pd.DataFrame(columns=columns), rows_read
    quarterly = pd.concat(pieces, ignore_index=True).groupby(
        ["carrier", "year", "quarter"], observed=True, as_index=False
    ).agg(fare_weight=("fare_weight", "sum"), weighted_fare=("weighted_fare", "sum"))
    annual = quarterly.groupby(["carrier", "year"], observed=True, as_index=False).agg(
        fare_weight=("fare_weight", "sum"),
        weighted_fare=("weighted_fare", "sum"),
        db1b_quarters=("quarter", "nunique"),
    )
    annual["db1b_average_fare_usd"] = annual["weighted_fare"] / annual["fare_weight"]
    annual.loc[annual["db1b_quarters"] < min_quarters, "db1b_average_fare_usd"] = np.nan
    return annual[columns], rows_read


def load_ontime(
    inputs: Sequence[str | Path] | None,
    *,
    start_year: int | None,
    end_year: int | None,
    carriers: set[str] | None,
    min_months: int,
    carrier_basis: str = "operating",
) -> tuple[pd.DataFrame, int]:
    if carrier_basis not in {"operating", "marketing"}:
        raise ValueError("on-time carrier basis must be 'operating' or 'marketing'")
    carrier_aliases = (
        ("OP_UNIQUE_CARRIER", "OPERATING_AIRLINE")
        if carrier_basis == "operating"
        else ("MKT_UNIQUE_CARRIER", "MARKETING_AIRLINE_NETWORK")
    )
    files = _files_in_year_range(
        existing_files(inputs, label="on-time"),
        start_year=start_year,
        end_year=end_year,
    )
    pieces: list[pd.DataFrame] = []
    rows_read = 0
    for path in files:
        for raw in read_table_chunks(path, wanted_columns=ONTIME_COLUMNS):
            rows_read += len(raw)
            frame = normalize_columns(raw)
            frame, year = _year_filter(frame, start_year=start_year, end_year=end_year)
            if frame.empty:
                continue
            carrier_column = _first_column(frame, carrier_aliases)
            if carrier_column is None:
                raise ValueError(
                    f"On-time input lacks a {carrier_basis}-carrier column; "
                    f"available columns: {list(frame.columns[:30])}"
                )
            carrier = clean_code(frame[carrier_column])
            cancelled = _numeric(frame, ("CANCELLED",), 0).fillna(0)
            diverted = _numeric(frame, ("DIVERTED",), 0).fillna(0)
            dep_del15 = _numeric(frame, ("DEP_DEL15", "DEPDELAY15"))
            if dep_del15.isna().all():
                delay = _numeric(frame, ("DEP_DELAY_NEW",))
                dep_del15 = delay.ge(15).where(delay.notna())
            arr_del15 = _numeric(frame, ("ARR_DEL15", "ARRDELAY15"))
            if arr_del15.isna().all():
                delay = _numeric(frame, ("ARR_DELAY_NEW",))
                arr_del15 = delay.ge(15).where(delay.notna())
            clean = pd.DataFrame(
                {
                    "carrier": carrier,
                    "year": year,
                    "month": _numeric(frame, ("MONTH",)),
                    "dep_valid": (cancelled < 0.5) & dep_del15.notna(),
                    "dep_on_time": (cancelled < 0.5) & dep_del15.lt(0.5),
                    "arr_valid": (cancelled < 0.5) & (diverted < 0.5) & arr_del15.notna(),
                    "arr_on_time": (cancelled < 0.5)
                    & (diverted < 0.5)
                    & arr_del15.lt(0.5),
                }
            )
            clean = _carrier_filter(clean, carriers)
            if not clean.empty:
                pieces.append(
                    clean.groupby(
                        ["carrier", "year", "month"],
                        observed=True,
                        as_index=False,
                    ).agg(
                        dep_valid=("dep_valid", "sum"),
                        dep_on_time=("dep_on_time", "sum"),
                        arr_valid=("arr_valid", "sum"),
                        arr_on_time=("arr_on_time", "sum"),
                    )
                )
    columns = [
        "carrier",
        "year",
        "departure_punctuality_pct",
        "arrival_punctuality_pct",
        "ontime_months",
    ]
    if not pieces:
        return pd.DataFrame(columns=columns), rows_read
    monthly = pd.concat(pieces, ignore_index=True).groupby(
        ["carrier", "year", "month"], observed=True, as_index=False
    ).sum(numeric_only=True)
    annual = monthly.groupby(["carrier", "year"], observed=True, as_index=False).agg(
        dep_valid=("dep_valid", "sum"),
        dep_on_time=("dep_on_time", "sum"),
        arr_valid=("arr_valid", "sum"),
        arr_on_time=("arr_on_time", "sum"),
        ontime_months=("month", "nunique"),
    )
    annual["departure_punctuality_pct"] = annual["dep_on_time"] / annual["dep_valid"] * 100
    annual["arrival_punctuality_pct"] = annual["arr_on_time"] / annual["arr_valid"] * 100
    incomplete = annual["ontime_months"] < min_months
    annual.loc[
        incomplete, ["departure_punctuality_pct", "arrival_punctuality_pct"]
    ] = np.nan
    return annual[columns], rows_read


def load_cbd_distances(
    path: str | Path | None,
    top_airports: pd.DataFrame,
) -> pd.DataFrame:
    columns = ["carrier", "year", "average_top5_airport_cbd_distance_miles"]
    if path is None or not Path(path).expanduser().is_file():
        return pd.DataFrame(columns=columns)
    distances = normalize_columns(pd.read_csv(Path(path).expanduser()))
    aliases = {
        "carrier": _first_column(distances, ("CARRIER",)),
        "year": _first_column(distances, ("YEAR",)),
        "airport": _first_column(distances, ("AIRPORT",)),
        "distance": _first_column(
            distances,
            ("DISTANCE_TO_CBD_MILES", "CBD_DISTANCE_MILES", "DISTANCE_MILES"),
        ),
    }
    if any(value is None for value in aliases.values()):
        raise ValueError(
            "CBD file must contain carrier, year, airport, and distance_to_cbd_miles."
        )
    clean = pd.DataFrame(
        {
            "carrier": clean_code(distances[aliases["carrier"]]),
            "year": pd.to_numeric(distances[aliases["year"]], errors="coerce").astype("Int64"),
            "airport": clean_code(distances[aliases["airport"]]),
            "distance": pd.to_numeric(distances[aliases["distance"]], errors="coerce"),
        }
    )
    merged = top_airports.merge(clean, on=["carrier", "year", "airport"], how="left")
    grouped = merged.groupby(["carrier", "year"], observed=True).agg(
        average_top5_airport_cbd_distance_miles=("distance", "mean"),
        cbd_airports_present=("distance", "count"),
    )
    grouped.loc[
        grouped["cbd_airports_present"] < 5,
        "average_top5_airport_cbd_distance_miles",
    ] = np.nan
    return grouped.reset_index()[columns]


def cbd_template(top_airports: pd.DataFrame) -> pd.DataFrame:
    """Return the prefilled manual-input table needed for the CBD measure."""
    template = top_airports[
        ["carrier", "carrier_name", "year", "airport", "airport_rank", "departures"]
    ].copy()
    template["distance_to_cbd_miles"] = np.nan
    template["notes"] = ""
    return template


def _safe_ratio(numerator: pd.Series, denominator: pd.Series, multiplier: float = 1) -> pd.Series:
    result = numerator / denominator.replace(0, np.nan) * multiplier
    return result.replace([np.inf, -np.inf], np.nan)


def build_raw_metrics(
    *,
    t100_inputs: Sequence[str | Path],
    db1b_inputs: Sequence[str | Path] | None = None,
    ontime_inputs: Sequence[str | Path] | None = None,
    p12_inputs: Sequence[str | Path] | None = None,
    p6_inputs: Sequence[str | Path] | None = None,
    p10_inputs: Sequence[str | Path] | None = None,
    b43_inputs: Sequence[str | Path] | None = None,
    cbd_path: str | Path | None = None,
    start_year: int | None = None,
    end_year: int | None = None,
    carriers: set[str] | None = None,
    min_departures: float = 1_000,
    min_db1b_quarters: int = 4,
    min_ontime_months: int = 12,
    ontime_carrier_basis: str = "operating",
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Create one row per carrier-year with every raw spectrum measure."""
    if carriers is not None:
        carriers = {carrier.strip().upper() for carrier in carriers}
    t100, top_airports, t100_stats = load_t100(
        t100_inputs,
        start_year=start_year,
        end_year=end_year,
        carriers=carriers,
        min_departures=min_departures,
    )
    p12, p12_rows = load_p12(
        p12_inputs, start_year=start_year, end_year=end_year, carriers=carriers
    )
    p6, p6_rows = load_p6(
        p6_inputs, start_year=start_year, end_year=end_year, carriers=carriers
    )
    p10, p10_rows = load_p10(
        p10_inputs, start_year=start_year, end_year=end_year, carriers=carriers
    )
    b43, b43_rows = load_b43(
        b43_inputs, start_year=start_year, end_year=end_year, carriers=carriers
    )
    fares, db1b_rows = load_db1b(
        db1b_inputs,
        start_year=start_year,
        end_year=end_year,
        carriers=carriers,
        min_quarters=min_db1b_quarters,
    )
    ontime, ontime_rows = load_ontime(
        ontime_inputs,
        start_year=start_year,
        end_year=end_year,
        carriers=carriers,
        min_months=min_ontime_months,
        carrier_basis=ontime_carrier_basis,
    )
    cbd = load_cbd_distances(cbd_path, top_airports)

    metrics = t100.copy()
    for extra in (p12, p6, p10, b43, fares, ontime, cbd):
        if extra.empty:
            continue
        duplicate_names = [
            column
            for column in extra.columns
            if column in metrics.columns and column not in {"carrier", "year"}
        ]
        extra = extra.drop(columns=duplicate_names)
        metrics = metrics.merge(extra, on=["carrier", "year"], how="left")

    def series(name: str) -> pd.Series:
        return (
            pd.to_numeric(metrics[name], errors="coerce")
            if name in metrics
            else pd.Series(np.nan, index=metrics.index, dtype=float)
        )

    metrics["unit_cost_per_asm_cents"] = _safe_ratio(
        series("operating_expense_usd"), metrics["asm"], 100
    )
    metrics["yield_per_rpm_cents"] = _safe_ratio(
        series("passenger_revenue_usd"), metrics["rpm"], 100
    )
    metrics["operating_revenue_per_sector_usd"] = _safe_ratio(
        series("operating_revenue_usd"), metrics["departures"]
    )
    ancillary = series("baggage_fees_usd").fillna(0) + series(
        "reservation_cancellation_fees_usd"
    ).fillna(0)
    ancillary_per_passenger = _safe_ratio(ancillary, metrics["passengers"])
    metrics["average_fare_including_ancillary_usd"] = series(
        "db1b_average_fare_usd"
    ) + ancillary_per_passenger
    metrics["passengers_per_flight_crew_employee"] = _safe_ratio(
        metrics["passengers"], series("flight_crew_employees")
    )
    metrics["aircraft_hours_per_aircraft_day"] = _safe_ratio(
        metrics["aircraft_hours"], series("aircraft_count") * metrics["days_in_year"]
    )
    metrics["aircraft_sectors_per_aircraft_day"] = _safe_ratio(
        metrics["departures"], series("aircraft_count") * metrics["days_in_year"]
    )
    metrics["passengers_per_employee"] = _safe_ratio(
        metrics["passengers"], series("total_employees")
    )
    metrics["employees_per_aircraft"] = _safe_ratio(
        series("total_employees"), series("aircraft_count")
    )
    metrics["personnel_cost_per_asm_cents"] = _safe_ratio(
        series("personnel_cost_usd"), metrics["asm"], 100
    )
    metrics["flight_crew_share_pct"] = _safe_ratio(
        series("flight_crew_employees"), series("total_employees"), 100
    )
    metrics["asm_per_employee_thousands"] = _safe_ratio(
        metrics["asm"], series("total_employees"), 1 / 1000
    )

    metric_columns = [metric.name for metric in METRIC_SPECS]
    diagnostic_columns = [
        "departures",
        "passengers",
        "asm",
        "rpm",
        "aircraft_count",
        "total_employees",
        "flight_crew_employees",
        "db1b_quarters",
        "ontime_months",
        "p12_quarters",
        "p6_quarters",
    ]
    for column in metric_columns + diagnostic_columns:
        if column not in metrics:
            metrics[column] = np.nan
    output_columns = ["carrier", "carrier_name", "year", *metric_columns, *diagnostic_columns]
    diagnostics = {
        "t100_files": t100_stats["files"],
        "t100_rows_read": t100_stats["rows_read"],
        "t100_rows_kept": t100_stats["rows_kept"],
        "db1b_rows_read": db1b_rows,
        "ontime_rows_read": ontime_rows,
        "p12_rows_read": p12_rows,
        "p6_rows_read": p6_rows,
        "p10_rows_read": p10_rows,
        "b43_rows_read": b43_rows,
    }
    return (
        metrics[output_columns].sort_values(["year", "carrier"]).reset_index(drop=True),
        cbd_template(top_airports),
        diagnostics,
    )
