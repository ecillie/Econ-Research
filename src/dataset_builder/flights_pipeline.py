import pandas as pd
from pathlib import Path
import zipfile


BASE_DIR = Path("/Users/evancillie/Documents/Econ Research/data/raw/")


# ---------------------------------------------------------------------
# Columns to load
# ---------------------------------------------------------------------

T100_COLUMNS = [
    "YEAR",
    "QUARTER",
    "ORIGIN",
    "DEST",
    "UNIQUE_CARRIER",
    "DEPARTURES_PERFORMED",
    "DEPARTURES_SCHEDULED",
    "SEATS",
    "PASSENGERS",
    "DISTANCE",
    "PAYLOAD",
]

DB1B_COLUMNS = [
    "Year",
    "Quarter",
    "Origin",
    "Dest",
    "TkCarrier",
    "Passengers",
    "MktFare",
    "MktDistance",
    'MktCoupons',
]

ONTIME_ALIASES = {
    "year": ("Year", "YEAR"),
    "quarter": ("Quarter", "QUARTER"),
    "month": ("Month", "MONTH"),
    "carrier": (
        "IATA_Code_Marketing_Airline",
        "MKT_UNIQUE_CARRIER",
    ),
    "origin": ("Origin", "ORIGIN"),
    "dest": ("Dest", "DEST"),
    "cancelled": ("Cancelled", "CANCELLED"),
    "flights": ("Flights",),
    "distance": ("Distance", "DISTANCE"),
}



def flight_pipeline() -> pd.DataFrame:
    """
    Load, clean, aggregate, and merge T100, DB1B, and On-Time data.

    Raw files are cleaned immediately after loading to minimize memory use.

    Returns:
        pd.DataFrame:
            Combined route-quarter-carrier dataset.
    """
    
    print("Loading DB1B data...")
    db1b_df = load_db1b()
    print(f"DB1B cleaned: {db1b_df.shape}")
    
    print("Loading On-Time data...")
    ontime_df = load_ontime()
    print(f"On-Time cleaned: {ontime_df.shape}")

    print("Loading T100 data...")
    t100_df = load_t100()
    print(f"T100 cleaned: {t100_df.shape}")

    print("Merging datasets...")

    final_df = merge_datasets(
        ontime_df=ontime_df,
        t100_df=t100_df,
        db1b_df=db1b_df,
    )

    return final_df

def merge_datasets(
    ontime_df: pd.DataFrame,
    t100_df: pd.DataFrame,
    db1b_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Merge cleaned datasets using standardized route-quarter-carrier keys.

    Args:
        ontime_df:
            Cleaned On-Time Performance data.
        t100_df:
            Cleaned T100 data.
        db1b_df:
            Cleaned DB1B data.

    Returns:
        pd.DataFrame:
            Combined route-quarter-carrier dataset.
    """
    merge_keys = [
        "route",
        "airport_1",
        "airport_2",
        "year",
        "quarter",
        "carrier",
    ]

    merged_df = pd.merge(
        ontime_df,
        t100_df,
        how="left",
        on=merge_keys,
        validate="one_to_one",
    )

    merged_df = pd.merge(
        merged_df,
        db1b_df,
        how="left",
        on=merge_keys,
        validate="one_to_one",
    )

    return merged_df

def load_t100() -> pd.DataFrame:
    """
    Load, clean, and combine annual T100 Domestic Segment files.

    Each file is cleaned immediately after loading to reduce memory use.

    Returns:
        pd.DataFrame:
            Combined route-quarter-carrier T100 data.
    """
    cleaned_dfs = []

    for year in range(2021, 2026):
        path = (
            BASE_DIR
            / "t100"
            / f"T100_Domestic_Segment_{year}.zip"
        )

        print(f"  Loading T100 {year}...")

        raw_df = unzip_to_df(
            path,
            usecols=T100_COLUMNS,
        )

        cleaned_df = clean_t100(raw_df)
        cleaned_dfs.append(cleaned_df)

        del raw_df

    return pd.concat(
        cleaned_dfs,
        ignore_index=True,
    )


def clean_t100(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate T100 data to route-quarter-carrier level.

    Args:
        df:
            Raw T100 data.

    Returns:
        pd.DataFrame:
            Aggregated T100 data.
    """
    df = df.copy()

    df["airport_1"] = df[["ORIGIN", "DEST"]].min(axis=1)
    df["airport_2"] = df[["ORIGIN", "DEST"]].max(axis=1)

    df["route"] = (
        df["airport_1"]
        + "-"
        + df["airport_2"]
    )

    route_quarter_carrier = (
        df.groupby(
            [
                "route",
                "airport_1",
                "airport_2",
                "YEAR",
                "QUARTER",
                "UNIQUE_CARRIER",
            ],
            as_index=False,
            observed=True,
        )
        .agg(
            departures=("DEPARTURES_PERFORMED", "sum"),
            scheduled_departures=("DEPARTURES_SCHEDULED", "sum"),
            seats=("SEATS", "sum"),
            t100_passengers=("PASSENGERS", "sum"),
            t100_distance=("DISTANCE", "mean"),
            payload=("PAYLOAD", "mean"),
        )
    )

    route_quarter_carrier.rename(
        columns={
            "YEAR": "year",
            "QUARTER": "quarter",
            "UNIQUE_CARRIER": "carrier",
        },
        inplace=True,
    )

    return route_quarter_carrier

def load_db1b() -> pd.DataFrame:
    """
    Load, clean, and combine quarterly DB1B Market files.

    Each quarter is cleaned immediately after loading to reduce memory use.

    Returns:
        pd.DataFrame:
            Combined route-quarter-carrier DB1B data.
    """
    cleaned_dfs = []

    for year in range(2021, 2026):
        max_quarter = 2 if year == 2025 else 4

        for quarter in range(1, max_quarter + 1):
            path = (
                BASE_DIR
                / "db1b"
                / f"DB1BMarket_{year}Q{quarter}.zip"
            )

            print(f"  Loading DB1B {year} Q{quarter}...")

            raw_df = unzip_to_df(
                path,
                usecols=DB1B_COLUMNS,
            )

            cleaned_df = clean_db1b(raw_df)
            cleaned_dfs.append(cleaned_df)

            del raw_df

    return pd.concat(
        cleaned_dfs,
        ignore_index=True,
    )


def clean_db1b(df: pd.DataFrame) -> pd.DataFrame:
    """
    Aggregate DB1B Market data to route-quarter-ticketing-carrier level.

    Uses ticketing carrier as the airline brand and computes a
    passenger-weighted average market fare.

    Args:
        df:
            Raw DB1B Market data.

    Returns:
        pd.DataFrame:
            Aggregated DB1B data.
    """
    df = df.copy()

    df["airport_1"] = df[["Origin", "Dest"]].min(axis=1)
    df["airport_2"] = df[["Origin", "Dest"]].max(axis=1)
    df["nonstop"] = (df["MktCoupons"] == 1).astype("int8")

    df["route"] = (
        df["airport_1"]
        + "-"
        + df["airport_2"]
    )

    df["fare_weighted"] = (
        df["MktFare"]
        * df["Passengers"]
    )

    route_quarter_carrier = (
        df.groupby(
            [
                "route",
                "airport_1",
                "airport_2",
                "Year",
                "Quarter",
                "TkCarrier",
                "MktCoupons"
            ],
            as_index=False,
            observed=True,
        )
        .agg(
            db1b_passengers=("Passengers", "sum"),
            fare_weighted=("fare_weighted", "sum"),
            db1b_distance=("MktDistance", "mean"),
        )
    )

    route_quarter_carrier["avg_fare"] = (
        route_quarter_carrier["fare_weighted"]
        / route_quarter_carrier["db1b_passengers"]
    )

    route_quarter_carrier.drop(
        columns="fare_weighted",
        inplace=True,
    )

    route_quarter_carrier.rename(
        columns={
            "Year": "year",
            "Quarter": "quarter",
            "TkCarrier": "carrier",
        },
        inplace=True,
    )

    return route_quarter_carrier


# ---------------------------------------------------------------------
# On-Time Performance
# ---------------------------------------------------------------------

def load_ontime() -> pd.DataFrame:
    """
    Load, clean, and combine monthly On-Time Performance files.

    Supports multiple BTS On-Time column-name schemas by resolving
    equivalent fields in each source file.

    Returns:
        pd.DataFrame:
            Combined route-quarter-marketing-carrier On-Time data.
    """
    cleaned_dfs = []

    for year in range(2021, 2026):
        max_month = 2 if year == 2025 else 12

        for month in range(1, max_month + 1):
            path = (
                BASE_DIR
                / "marketing_on_time"
                / f"Marketing_Carrier_On_Time_{year}_{month}.zip"
            )

            print(f"  Loading On-Time {year}-{month:02d}...")

            raw_df = load_ontime_file(path)
            cleaned_df = clean_ontime(raw_df)

            cleaned_dfs.append(cleaned_df)

            del raw_df

    combined_df = pd.concat(
        cleaned_dfs,
        ignore_index=True,
    )

    # Monthly files may create multiple observations for the same
    # route-quarter-carrier, so aggregate once more after concatenation.
    combined_df = (
        combined_df.groupby(
            [
                "route",
                "airport_1",
                "airport_2",
                "year",
                "quarter",
                "carrier",
            ],
            as_index=False,
            observed=True,
        )
        .agg(
            scheduled_flights=("scheduled_flights", "sum"),
            performed_flights=("performed_flights", "sum"),
            cancellations=("cancellations", "sum"),
        )
    )

    return combined_df


def load_ontime_file(
    zip_path: str | Path,
) -> pd.DataFrame:
    """
    Load only required On-Time columns while supporting different BTS schemas.

    For example, some files use:
        Year, Origin, IATA_Code_Marketing_Airline

    while others use:
        YEAR, ORIGIN, MKT_UNIQUE_CARRIER

    Both are normalized to a single internal schema.

    Args:
        zip_path:
            Path to the On-Time ZIP archive.

    Returns:
        pd.DataFrame:
            On-Time data with standardized column names.
    """
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        csv_files = [
            file
            for file in zip_ref.namelist()
            if file.lower().endswith(".csv")
        ]

        if not csv_files:
            raise ValueError(
                f"No CSV file found in ZIP archive: {zip_path}"
            )

        csv_name = csv_files[0]

        # Read header only.
        with zip_ref.open(csv_name) as csv_file:
            header = pd.read_csv(
                csv_file,
                nrows=0,
            )

        available_columns = {
            column.strip(): column
            for column in header.columns
        }

        resolved = {}

        # These can be calculated or omitted if unavailable.
        optional_columns = {
            "quarter",
            "flights",
            "distance",
        }

        for logical_name, aliases in ONTIME_ALIASES.items():
            match = next(
                (
                    alias
                    for alias in aliases
                    if alias in available_columns
                ),
                None,
            )

            if match is None:
                if logical_name in optional_columns:
                    continue

                raise ValueError(
                    f"Could not resolve On-Time column "
                    f"'{logical_name}' in {zip_path.name}.\n"
                    f"Expected one of: {aliases}\n"
                    f"Available columns: "
                    f"{list(available_columns.keys())}"
                )

            resolved[logical_name] = match

        columns_to_read = [
            available_columns[column]
            for column in resolved.values()
        ]

        with zip_ref.open(csv_name) as csv_file:
            df = pd.read_csv(
                csv_file,
                usecols=columns_to_read,
                low_memory=False,
            )

    df.columns = df.columns.str.strip()

    rename_map = {
        actual_name: logical_name
        for logical_name, actual_name in resolved.items()
    }

    df.rename(
        columns=rename_map,
        inplace=True,
    )

    return df


def clean_ontime(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Aggregate On-Time data to route-quarter-marketing-carrier level.

    Uses the marketing carrier so regional branded service is assigned
    to the major airline brand. Cancelled flights do not count as
    performed service.

    Args:
        df:
            Standardized raw On-Time Performance data.

    Returns:
        pd.DataFrame:
            Aggregated route-quarter-carrier On-Time data.
    """
    df = df.copy()

    # Calculate quarter when the source file does not provide it.
    if "quarter" not in df.columns:
        df["quarter"] = (
            ((df["month"] - 1) // 3) + 1
        ).astype("int8")

    df["airport_1"] = df[["origin", "dest"]].min(axis=1)
    df["airport_2"] = df[["origin", "dest"]].max(axis=1)

    df["route"] = (
        df["airport_1"]
        + "-"
        + df["airport_2"]
    )

    # Each row is normally one scheduled flight. If BTS provides
    # a Flights field, use that instead.
    if "flights" in df.columns:
        df["scheduled_flights"] = df["flights"]
    else:
        df["scheduled_flights"] = 1

    df["performed_flights"] = (
        df["cancelled"] == 0
    ).astype("int8")

    route_quarter_carrier = (
        df.groupby(
            [
                "route",
                "airport_1",
                "airport_2",
                "year",
                "quarter",
                "carrier",
            ],
            as_index=False,
            observed=True,
        )
        .agg(
            scheduled_flights=("scheduled_flights", "sum"),
            performed_flights=("performed_flights", "sum"),
            cancellations=("cancelled", "sum"),
        )
    )

    return route_quarter_carrier


# ---------------------------------------------------------------------
# Generic ZIP loader
# ---------------------------------------------------------------------

def unzip_to_df(
    zip_path: str | Path,
    usecols: list[str] | None = None,
) -> pd.DataFrame:
    """
    Read selected columns from the first CSV inside a ZIP archive.

    Column names are stripped of leading and trailing whitespace before
    selecting requested fields.

    Args:
        zip_path:
            Path to the ZIP archive.
        usecols:
            Optional list of columns to load.

    Returns:
        pd.DataFrame:
            Loaded CSV data.

    Raises:
        ValueError:
            If no CSV exists or required columns are missing.
        FileNotFoundError:
            If the ZIP archive does not exist.
        zipfile.BadZipFile:
            If the file is not a valid ZIP archive.
    """
    with zipfile.ZipFile(zip_path, "r") as zip_ref:
        csv_files = [
            file
            for file in zip_ref.namelist()
            if file.lower().endswith(".csv")
        ]

        if not csv_files:
            raise ValueError(
                f"No CSV file found in ZIP archive: {zip_path}"
            )

        csv_name = csv_files[0]

        with zip_ref.open(csv_name) as csv_file:
            header = pd.read_csv(
                csv_file,
                nrows=0,
            )

        actual_columns = {
            column.strip(): column
            for column in header.columns
        }

        if usecols is not None:
            missing_columns = [
                column
                for column in usecols
                if column not in actual_columns
            ]

            if missing_columns:
                raise ValueError(
                    f"Missing columns in {zip_path.name}: "
                    f"{missing_columns}\n"
                    f"Available columns: "
                    f"{list(actual_columns.keys())}"
                )

            columns_to_read = [
                actual_columns[column]
                for column in usecols
            ]

        else:
            columns_to_read = None

        with zip_ref.open(csv_name) as csv_file:
            df = pd.read_csv(
                csv_file,
                usecols=columns_to_read,
                low_memory=False,
            )

    df.columns = df.columns.str.strip()

    return df


if __name__ == "__main__":
    df = flight_pipeline()
    df.to_excel(
    "/Users/evancillie/Documents/Econ Research/output/combined_flight_data.xlsx",
    index=False,
)
    