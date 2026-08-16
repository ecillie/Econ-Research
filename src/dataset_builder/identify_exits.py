import pandas as pd

from src.route_exits.cli import run_analysis


def match_flights_exit(flights: pd.DataFrame) -> pd.DataFrame:
    """
    Match flights with identified exits based on specified criteria.
    Args:
        flights (pd.DataFrame): DataFrame containing flight data.
    Returns:
        pd.DataFrame: DataFrame containing matched flights and exits.
    """
    exits = exits_pipeline() 
    exits = year_month_to_year_quater(exits, "exit_start_month")
    exits = exits.rename(columns={"year_quarter": "year_quarter_exit"})
    exits = year_month_to_year_quater(exits, "return_month")
    exits = exits.rename(columns={"year_quarter": "year_quarter_return"})
    drop_cols = [
    "airport_1",
    "airport_2",
    "city_1",
    "city_2",
    "first_observed_service_month",
    "last_service_month",
    "one_year_absence_verified_through",
    "absence_observed_through",
    "sample_start_month",
    "sample_end_month",
    ]
    exits = exits.drop(columns=drop_cols)
    return label_effected_routes(flights, exits)

def label_effected_routes(
    flights: pd.DataFrame,
    exits: pd.DataFrame,
) -> pd.DataFrame:

    flights = flights.copy()
    exits = exits.copy()

    # Build flight quarter
    flights["year_quarter"] = pd.PeriodIndex(
        flights["year"].astype(int).astype(str)
        + "Q"
        + flights["quarter"].astype(int).astype(str),
        freq="Q",
    )

    # Completely ignore the existing quarter columns
    exits = exits.drop(
        columns=["year_quarter_exit", "year_quarter_return"],
        errors="ignore",
    )

    # Rebuild them from the month columns
    exits["year_quarter_exit"] = pd.to_datetime(
        exits["exit_start_month"],
        format="%Y-%m",
        errors="coerce",
    ).dt.to_period("Q")

    exits["year_quarter_return"] = pd.to_datetime(
        exits["return_month"],
        format="%Y-%m",
        errors="coerce",
    ).dt.to_period("Q")

    exit_cols = [
        "route",
        "carrier",
        "airline",
        "exit_start_month",
        "return_month",
        "year_quarter_exit",
        "year_quarter_return",
        "exit_status",
        "observed_absence_months",
        "active_months_in_prior_12",
        "departures_in_prior_12",
        "seats_in_prior_12",
        "passengers_in_prior_12",
        "competing_airline_count_at_exit",
        "competing_airlines_at_exit",
        "competing_departures_at_exit",
        "continued_by_other_airline_after_exit",
        "continuous_other_airline_service_months",
        "continuous_other_airline_service_through",
        "other_airline_continuation_status",
        "airlines_during_continuous_service",
    ]

    before = len(flights)

    flights = flights.merge(
        exits[exit_cols],
        on=["route", "carrier"],
        how="left",
        validate="many_to_one",
    )

    assert len(flights) == before

    flights["effected"] = (
        flights["year_quarter_exit"].notna()
        & (flights["year_quarter"] >= flights["year_quarter_exit"])
        & (
            flights["year_quarter_return"].isna()
            | (flights["year_quarter"] < flights["year_quarter_return"])
        )
    ).astype("int8")

    return flights

def year_month_to_year_quater(df, year_month_col):
    dates = pd.to_datetime(
        df[year_month_col],
        format="%Y-%m",
        errors="coerce"
    )

    df["year"] = dates.dt.year.astype("Int64")
    df["quarter"] = dates.dt.quarter.astype("Int64")
    df["year_quarter"] = dates.dt.to_period("Q")

    return df
    
def exits_pipeline() -> pd.DataFrame:
    """
    Run the exits pipeline to identify airline route exits based on specified criteria.
    Returns:
        pd.DataFrame: DataFrame containing the identified exits.
    """
    
    events = run_analysis([
        "--t100", "data/raw/t100",
        "--carrier-level", "marketing",
        "--marketing-data", "data/raw/marketing_on_time",
        "--download-marketing-data",
        "--min-absence-months", "12",
        "--min-active-departures", "1",
        "--min-active-months-before-exit", "12",
        "--min-performed-flights-before-exit", "10",
        "--service-classes", "F",
    ])

    return events

if __name__ == "__main__":
    flights = pd.read_excel("/Users/evancillie/Documents/Econ Research/output/combined_flight_data.xlsx")
    matched_flights = match_flights_exit(flights)
    
    