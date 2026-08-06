"""Definitions for the Lohmann-Koo airline business-model spectrum."""

from __future__ import annotations

from dataclasses import dataclass


INDEX_NAMES = (
    "revenue",
    "connectivity",
    "convenience",
    "comfort",
    "aircraft",
    "labor",
)


@dataclass(frozen=True)
class MetricSpec:
    """One raw measure and the direction that maps it onto the spectrum."""

    name: str
    index: str
    higher_is_fsnc: bool
    source: str
    description: str
    implementation_status: str = "direct"


METRIC_SPECS = (
    MetricSpec(
        "unit_cost_per_asm_cents",
        "revenue",
        True,
        "Form 41 P-1.2 / T-100",
        "Operating expense divided by available seat miles, in cents.",
    ),
    MetricSpec(
        "yield_per_rpm_cents",
        "revenue",
        True,
        "Form 41 P-1.2 / T-100",
        "Scheduled passenger revenue divided by revenue passenger miles, in cents.",
    ),
    MetricSpec(
        "operating_revenue_per_sector_usd",
        "revenue",
        True,
        "Form 41 P-1.2 / T-100",
        "Operating revenue divided by performed departures.",
    ),
    MetricSpec(
        "average_fare_including_ancillary_usd",
        "revenue",
        True,
        "DB1B / Form 41 P-1.2",
        "Passenger-weighted DB1B market fare plus baggage and cancellation fees per passenger.",
        "public_data_proxy",
    ),
    MetricSpec(
        "network_density_departures_per_airport_day",
        "connectivity",
        True,
        "T-100",
        "Performed departures divided by served origin airports and calendar days.",
    ),
    MetricSpec(
        "total_destinations",
        "connectivity",
        True,
        "T-100",
        "Distinct airports served during the year.",
    ),
    MetricSpec(
        "average_sector_miles",
        "connectivity",
        True,
        "T-100",
        "Departure-weighted nonstop segment distance.",
    ),
    MetricSpec(
        "average_top5_airport_cbd_distance_miles",
        "convenience",
        False,
        "Manual airport-CBD distance table",
        "Mean CBD distance for the five airports with the most carrier departures.",
        "manual_input",
    ),
    MetricSpec(
        "departure_punctuality_pct",
        "convenience",
        False,
        "BTS Marketing Carrier On-Time Performance",
        "Share of performed flights departing less than 15 minutes late; "
        "operating carrier by default.",
    ),
    MetricSpec(
        "arrival_punctuality_pct",
        "convenience",
        False,
        "BTS Marketing Carrier On-Time Performance",
        "Share of non-diverted performed flights arriving less than 15 minutes late; "
        "operating carrier by default.",
    ),
    MetricSpec(
        "load_factor_pct",
        "comfort",
        False,
        "T-100",
        "Revenue passenger miles divided by available seat miles.",
    ),
    MetricSpec(
        "passengers_per_flight_crew_employee",
        "comfort",
        False,
        "T-100 / Form 41 P-10",
        "Enplaned passengers divided by P-10 pilots and other flight personnel; "
        "cabin-crew override recommended.",
        "public_data_proxy",
    ),
    MetricSpec(
        "aircraft_hours_per_aircraft_day",
        "aircraft",
        False,
        "T-100 / Form 41 B-43",
        "Airborne hours divided by active aircraft and calendar days.",
        "public_data_proxy",
    ),
    MetricSpec(
        "fleet_uniformity_pct",
        "aircraft",
        False,
        "Form 41 B-43",
        "Share of active aircraft in the carrier's most common aircraft family.",
    ),
    MetricSpec(
        "aircraft_sectors_per_aircraft_day",
        "aircraft",
        False,
        "T-100 / Form 41 B-43",
        "Performed departures divided by active aircraft and calendar days.",
    ),
    MetricSpec(
        "passengers_per_employee",
        "labor",
        False,
        "T-100 / Form 41 P-10",
        "Enplaned passengers divided by total employees.",
    ),
    MetricSpec(
        "employees_per_aircraft",
        "labor",
        True,
        "Form 41 P-10 / B-43",
        "Total employees divided by active aircraft.",
    ),
    MetricSpec(
        "personnel_cost_per_asm_cents",
        "labor",
        True,
        "Form 41 P-6 / T-100",
        "Salaries and related benefits divided by available seat miles, in cents.",
    ),
    MetricSpec(
        "flight_crew_share_pct",
        "labor",
        False,
        "Form 41 P-10",
        "P-10 pilots and other flight personnel divided by total employees; "
        "cabin-crew override recommended.",
        "public_data_proxy",
    ),
    MetricSpec(
        "asm_per_employee_thousands",
        "labor",
        False,
        "T-100 / Form 41 P-10",
        "Available seat miles per employee, in thousands.",
    ),
)


METRIC_BY_NAME = {metric.name: metric for metric in METRIC_SPECS}
METRICS_BY_INDEX = {
    index: tuple(metric.name for metric in METRIC_SPECS if metric.index == index)
    for index in INDEX_NAMES
}


DEFAULT_CARRIER_NAMES = {
    "AA": "American Airlines",
    "AS": "Alaska Airlines",
    "B6": "JetBlue Airways",
    "DL": "Delta Air Lines",
    "F9": "Frontier Airlines",
    "G4": "Allegiant Air",
    "HA": "Hawaiian Airlines",
    "MX": "Breeze Airways",
    "NK": "Spirit Airlines",
    "OO": "SkyWest Airlines",
    "SY": "Sun Country Airlines",
    "UA": "United Airlines",
    "WN": "Southwest Airlines",
}
