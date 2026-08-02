"""Exit identification and panel construction for quarterly ULCC data."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import ExitRules


def complete_ulcc_grid(
    carrier_market: pd.DataFrame, ulcc_codes: set[str]
) -> pd.DataFrame:
    """Add explicit zero-service rows for every observed ULCC market."""
    observed = carrier_market[carrier_market["carrier"].isin(ulcc_codes)].copy()
    if observed.empty:
        raise ValueError(
            "No ULCC observations found. Check --ulcc codes and carrier columns."
        )
    global_quarters = pd.period_range(
        carrier_market["quarter"].min(), carrier_market["quarter"].max(), freq="Q"
    )
    identities = observed[
        ["market", "airport_1", "airport_2", "carrier"]
    ].drop_duplicates()
    grid = identities.merge(
        pd.DataFrame({"quarter": global_quarters}), how="cross"
    ).merge(
        observed,
        on=["market", "airport_1", "airport_2", "carrier", "quarter"],
        how="left",
    )
    for column in (
        "departures",
        "seats",
        "passengers",
        "arr_delay_total",
        "cancellations",
    ):
        grid[column] = grid[column].fillna(0)
    grid["active"] = grid["active"].astype("boolean").fillna(False).astype(bool)
    return grid.sort_values(["market", "carrier", "quarter"])


def _active_carrier_index(
    carrier_market: pd.DataFrame,
) -> dict[tuple[str, pd.Period], frozenset[str]]:
    """Index active carriers once to avoid scanning the full frame per candidate."""
    active = carrier_market.loc[
        carrier_market["active"], ["market", "quarter", "carrier"]
    ]
    return {
        key: frozenset(group["carrier"].dropna().astype(str))
        for key, group in active.groupby(
            ["market", "quarter"], observed=True, sort=False
        )
    }


def identify_exits(
    carrier_market: pd.DataFrame, ulcc_codes: set[str], rules: ExitRules
) -> pd.DataFrame:
    """Identify sustained terminal exits that satisfy the configured rules."""
    grid = complete_ulcc_grid(carrier_market, ulcc_codes)
    active_carriers = _active_carrier_index(carrier_market)
    events: list[dict] = []

    for (market, carrier), group in grid.groupby(
        ["market", "carrier"], observed=True, sort=False
    ):
        group = group.sort_values("quarter").reset_index(drop=True)
        active_indices = np.flatnonzero(group["active"].to_numpy())
        if not len(active_indices):
            continue
        exit_index = int(active_indices[-1]) + 1
        if exit_index >= len(group):
            continue
        if len(group) - exit_index < rules.min_post_quarters:
            continue

        pre = group.iloc[exit_index - rules.min_pre_quarters : exit_index]
        if len(pre) < rules.min_pre_quarters:
            continue
        active_pre = int(pre["active"].sum())
        if active_pre < rules.min_pre_active_quarters:
            continue
        if len(pre) - active_pre > rules.max_gap_quarters:
            continue
        pre_departures = float(pre["departures"].sum())
        if pre_departures < rules.min_pre_departures:
            continue

        post = group.iloc[exit_index : exit_index + rules.min_post_quarters]
        if post["active"].any():
            continue

        exit_quarter = group.loc[exit_index, "quarter"]
        remaining_carriers: set[str] = set()
        other_active_quarters = 0
        for quarter_offset in range(rules.require_other_service_quarters):
            quarter_carriers = set(
                active_carriers.get((market, exit_quarter + quarter_offset), ())
            )
            quarter_carriers.discard(str(carrier))
            if quarter_carriers:
                other_active_quarters += 1
                remaining_carriers.update(quarter_carriers)
        if other_active_quarters < rules.require_other_service_quarters:
            continue

        last_pre = pre.iloc[-1]
        first_identity = group.iloc[0]
        events.append(
            {
                "event_id": f"{market}_{carrier}_{exit_quarter}",
                "market": market,
                "airport_1": first_identity["airport_1"],
                "airport_2": first_identity["airport_2"],
                "exiting_carrier": carrier,
                "last_active_quarter": str(last_pre["quarter"]),
                "first_treated_quarter": str(exit_quarter),
                "pre_active_quarters": active_pre,
                "pre_departures": pre_departures,
                "pre_seats": float(pre["seats"].sum()),
                "pre_passengers": float(pre["passengers"].sum()),
                "last_active_departures": float(last_pre["departures"]),
                "last_active_seats": float(last_pre["seats"]),
                "post_zero_quarters_verified": rules.min_post_quarters,
                "other_service_quarters_verified": other_active_quarters,
                "remaining_carriers": "|".join(sorted(remaining_carriers)),
                "n_remaining_carriers": len(remaining_carriers),
            }
        )

    columns = [
        "event_id",
        "market",
        "airport_1",
        "airport_2",
        "exiting_carrier",
        "last_active_quarter",
        "first_treated_quarter",
        "pre_active_quarters",
        "pre_departures",
        "pre_seats",
        "pre_passengers",
        "last_active_departures",
        "last_active_seats",
        "post_zero_quarters_verified",
        "other_service_quarters_verified",
        "remaining_carriers",
        "n_remaining_carriers",
    ]
    return pd.DataFrame(events, columns=columns)


def aggregate_market_operations(carrier_market: pd.DataFrame) -> pd.DataFrame:
    """Aggregate carrier-quarter operations and market concentration."""
    shares = carrier_market.copy()
    totals = shares.groupby(["market", "quarter"], observed=True)[
        "passengers"
    ].transform("sum")
    shares["passenger_share"] = np.divide(
        shares["passengers"],
        totals,
        out=np.zeros(len(shares), dtype=float),
        where=totals.to_numpy() > 0,
    )
    shares["share_squared"] = shares["passenger_share"].pow(2)
    shares["is_active_carrier"] = shares["active"].astype(int)
    return shares.groupby(
        ["market", "airport_1", "airport_2", "quarter"],
        observed=True,
        as_index=False,
    ).agg(
        departures=("departures", "sum"),
        seats=("seats", "sum"),
        t100_passengers=("passengers", "sum"),
        route_distance=("distance", "mean"),
        arr_delay_total=("arr_delay_total", "sum"),
        cancellations=("cancellations", "sum"),
        active_carriers=("is_active_carrier", "sum"),
        passenger_hhi=("share_squared", "sum"),
    )


def build_analysis_panel(
    operations: pd.DataFrame, fares: pd.DataFrame, events: pd.DataFrame
) -> pd.DataFrame:
    """Combine outcomes with first-treatment timing for staggered DiD analysis."""
    panel = operations.merge(
        fares,
        on=["market", "airport_1", "airport_2", "quarter"],
        how="outer",
        validate="one_to_one",
    )
    first_events = pd.DataFrame(
        columns=["market", "first_treated_quarter", "exiting_carrier", "event_id"]
    )
    if not events.empty:
        sortable = events.assign(
            _t=pd.PeriodIndex(events["first_treated_quarter"], freq="Q")
        ).sort_values(["market", "_t"])
        first_events = sortable.groupby("market", as_index=False).first()[
            ["market", "first_treated_quarter", "exiting_carrier", "event_id"]
        ]
    panel = panel.merge(first_events, on="market", how="left")
    treatment_period = pd.PeriodIndex(
        panel["first_treated_quarter"].fillna("2100Q1"), freq="Q"
    )
    panel["ever_treated"] = panel["first_treated_quarter"].notna().astype(int)
    panel["post"] = (
        (panel["ever_treated"] == 1) & (panel["quarter"] >= treatment_period)
    ).astype(int)
    panel["relative_quarter"] = (
        panel["quarter"].astype("int64") - treatment_period.astype("int64")
    )
    panel.loc[panel["ever_treated"] == 0, "relative_quarter"] = pd.NA
    panel["treatment_cohort"] = panel["first_treated_quarter"]
    panel["market_id"] = panel["market"]
    panel["year"] = panel["quarter"].dt.year
    panel["quarter_number"] = panel["quarter"].dt.quarter
    return panel.sort_values(["market", "quarter"])
