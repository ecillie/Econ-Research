"""Configuration values for the quarterly ULCC-exit pipeline."""

from __future__ import annotations

from dataclasses import dataclass


ULCC_DEFAULT = ("F9", "NK", "G4")


@dataclass(frozen=True)
class ExitRules:
    """Thresholds used to qualify a sustained carrier-market exit."""

    min_pre_quarters: int
    min_post_quarters: int
    min_pre_departures: float
    min_pre_active_quarters: int
    max_gap_quarters: int
    require_other_service_quarters: int
