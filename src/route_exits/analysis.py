"""Detection of sustained monthly airline route-exit episodes."""

from __future__ import annotations

from typing import Any

import pandas as pd

from .config import KNOWN_CARRIER_NAMES
from .data import first_nonempty


EVENT_COLUMNS = [
    "route",
    "airport_1",
    "airport_2",
    "city_1",
    "city_2",
    "carrier",
    "airline",
    "operating_carriers_prior_12",
    "competing_airline_count_at_exit",
    "competing_airlines_at_exit",
    "competing_departures_at_exit",
    "continued_by_other_airline_after_exit",
    "continuous_other_airline_service_months",
    "continuous_other_airline_service_through",
    "other_airline_continuation_status",
    "airlines_during_continuous_service",
    "first_observed_service_month",
    "last_service_month",
    "exit_start_month",
    "one_year_absence_verified_through",
    "return_month",
    "absence_observed_through",
    "observed_absence_months",
    "exit_status",
    "active_months_in_prior_12",
    "departures_in_prior_12",
    "seats_in_prior_12",
    "passengers_in_prior_12",
    "sample_start_month",
    "sample_end_month",
]


def _carrier_name(code: str, group: pd.DataFrame) -> str:
    name = first_nonempty(group["carrier_name"])
    if pd.isna(name):
        return KNOWN_CARRIER_NAMES.get(code, "Name unavailable")
    return str(name)


def _competitor_details(
    competitors: pd.DataFrame,
) -> tuple[list[tuple[str, str, float]], str]:
    rows: list[tuple[str, str, float]] = []
    for code, group in competitors.groupby("carrier", observed=True, sort=False):
        rows.append(
            (str(code), _carrier_name(str(code), group), float(group["departures"].sum()))
        )
    rows.sort(key=lambda row: (-row[2], row[0]))
    summary = " | ".join(
        f"{code} - {name} ({int(departures):,} performed flights)"
        for code, name, departures in rows
    )
    return rows, summary or "None observed"


def _continuing_service(
    *,
    route: str,
    exiting_carrier: str,
    exit_month: pd.Period,
    observation_end: pd.Period,
    active_route_months: dict[tuple[Any, Any], pd.DataFrame],
    empty: pd.DataFrame,
) -> tuple[int, pd.Period | None, str, str]:
    groups: list[pd.DataFrame] = []
    month = exit_month
    while month <= observation_end:
        route_month = active_route_months.get((route, month), empty)
        others = route_month[route_month["carrier"] != exiting_carrier]
        if others.empty:
            break
        groups.append(others)
        month += 1
    if not groups:
        return 0, None, "no_other_airline_in_exit_month", "None observed"

    through = month - 1
    status = (
        "still_served_by_other_airline_at_sample_end"
        if through == observation_end
        else "other_airline_service_ended"
    )
    detail = pd.concat(groups, ignore_index=True)
    rows: list[tuple[str, str, int, float, pd.Period, pd.Period]] = []
    for code, group in detail.groupby("carrier", observed=True, sort=False):
        rows.append(
            (
                str(code),
                _carrier_name(str(code), group),
                int(group["month"].nunique()),
                float(group["departures"].sum()),
                group["month"].min(),
                group["month"].max(),
            )
        )
    rows.sort(key=lambda row: (-row[3], row[0]))
    summary = " | ".join(
        (
            f"{code} - {name} ({active_months} active months; "
            f"{int(departures):,} performed flights; "
            f"{first_month} to {last_month})"
        )
        for code, name, active_months, departures, first_month, last_month in rows
    )
    return len(groups), through, status, summary


def _operator_summary(
    *,
    route: str,
    carrier: str,
    carrier_name: object,
    lookback_start: pd.Period,
    last_service: pd.Period,
    operator_index: dict[tuple[Any, Any], pd.DataFrame] | None,
    empty_operator: pd.DataFrame | None,
) -> str:
    if operator_index is None:
        name = carrier_name if pd.notna(carrier_name) else "Name unavailable"
        return f"{carrier} - {name}"
    assert empty_operator is not None
    operators = operator_index.get((route, carrier), empty_operator)
    operators = operators[
        (operators["month"] >= lookback_start)
        & (operators["month"] <= last_service)
    ]
    counts = (
        operators.groupby("operator", observed=True)["performed_flights"]
        .sum()
        .sort_values(ascending=False)
    )
    return " | ".join(
        (
            f"{code} - {KNOWN_CARRIER_NAMES.get(code, 'Name unavailable')} "
            f"({int(count):,} flights)"
        )
        for code, count in counts.items()
        if count > 0
    )


def identify_exit_episodes(
    monthly: pd.DataFrame,
    *,
    min_absence_months: int,
    min_active_months_before_exit: int,
    min_performed_flights_before_exit: float = 0,
    require_competing_airline_at_exit: bool = False,
    operator_detail: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Return one record for every qualifying sustained gap in route service."""
    observation_start = monthly["month"].min()
    observation_end = monthly["month"].max()
    events: list[dict] = []
    operator_index = (
        {
            key: group
            for key, group in operator_detail.groupby(
                ["route", "carrier"], observed=True, sort=False
            )
        }
        if operator_detail is not None
        else None
    )
    empty_operator = operator_detail.iloc[0:0] if operator_detail is not None else None
    active_route_months = {
        key: group
        for key, group in monthly[monthly["active"]].groupby(
            ["route", "month"], observed=True, sort=False
        )
    }
    empty_monthly = monthly.iloc[0:0]

    for (route, carrier), group in monthly.groupby(
        ["route", "carrier"], observed=True, sort=True
    ):
        group = group.sort_values("month").reset_index(drop=True)
        active = group[group["active"]]
        if active.empty:
            continue
        active_months = sorted(active["month"].unique())
        active_set = set(active_months)
        first_service = active_months[0]
        carrier_name = first_nonempty(group["carrier_name"])
        city_1 = first_nonempty(group["city_1"])
        city_2 = first_nonempty(group["city_2"])
        airport_1 = group.iloc[0]["airport_1"]
        airport_2 = group.iloc[0]["airport_2"]

        for position, last_service in enumerate(active_months):
            exit_month = last_service + 1
            if exit_month in active_set:
                continue
            return_month = (
                active_months[position + 1]
                if position + 1 < len(active_months)
                else None
            )
            absence_end = (
                return_month - 1 if return_month is not None else observation_end
            )
            absence_months = int(absence_end.ordinal - exit_month.ordinal + 1)
            if absence_months < min_absence_months:
                continue

            lookback_start = last_service - 11
            pre = group[
                (group["month"] >= lookback_start)
                & (group["month"] <= last_service)
                & group["active"]
            ]
            pre_active_months = int(pre["month"].nunique())
            if pre_active_months < min_active_months_before_exit:
                continue
            pre_performed_flights = float(pre["departures"].sum())
            if pre_performed_flights < min_performed_flights_before_exit:
                continue

            competitors = active_route_months.get((route, exit_month), empty_monthly)
            competitors = competitors[competitors["carrier"] != carrier]
            if require_competing_airline_at_exit and competitors.empty:
                continue
            competitor_rows, competitor_summary = _competitor_details(competitors)
            continuation_months, continuation_through, continuation_status, continuing_summary = _continuing_service(
                route=route,
                exiting_carrier=carrier,
                exit_month=exit_month,
                observation_end=observation_end,
                active_route_months=active_route_months,
                empty=empty_monthly,
            )
            operator_summary = _operator_summary(
                route=route,
                carrier=carrier,
                carrier_name=carrier_name,
                lookback_start=lookback_start,
                last_service=last_service,
                operator_index=operator_index,
                empty_operator=empty_operator,
            )
            events.append(
                {
                    "route": route,
                    "airport_1": airport_1,
                    "airport_2": airport_2,
                    "city_1": city_1,
                    "city_2": city_2,
                    "carrier": carrier,
                    "airline": carrier_name,
                    "operating_carriers_prior_12": operator_summary,
                    "competing_airline_count_at_exit": len(competitor_rows),
                    "competing_airlines_at_exit": competitor_summary,
                    "competing_departures_at_exit": sum(row[2] for row in competitor_rows),
                    "continued_by_other_airline_after_exit": bool(continuation_months),
                    "continuous_other_airline_service_months": continuation_months,
                    "continuous_other_airline_service_through": (
                        str(continuation_through)
                        if continuation_through is not None
                        else ""
                    ),
                    "other_airline_continuation_status": continuation_status,
                    "airlines_during_continuous_service": continuing_summary,
                    "first_observed_service_month": str(first_service),
                    "last_service_month": str(last_service),
                    "exit_start_month": str(exit_month),
                    "one_year_absence_verified_through": str(
                        exit_month + min_absence_months - 1
                    ),
                    "return_month": (
                        str(return_month) if return_month is not None else ""
                    ),
                    "absence_observed_through": str(absence_end),
                    "observed_absence_months": absence_months,
                    "exit_status": (
                        "returned_after_at_least_one_year"
                        if return_month is not None
                        else "not_returned_by_sample_end"
                    ),
                    "active_months_in_prior_12": pre_active_months,
                    "departures_in_prior_12": pre_performed_flights,
                    "seats_in_prior_12": float(pre["seats"].sum()),
                    "passengers_in_prior_12": float(pre["passengers"].sum()),
                    "sample_start_month": str(observation_start),
                    "sample_end_month": str(observation_end),
                }
            )

    return pd.DataFrame(events, columns=EVENT_COLUMNS).sort_values(
        ["exit_start_month", "carrier", "route"]
    )
