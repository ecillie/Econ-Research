"""Output serialization and diagnostics for the quarterly ULCC pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .config import ExitRules


def diagnostics(
    t100_row_count: int,
    db1b_row_count: int,
    events: pd.DataFrame,
    panel: pd.DataFrame,
    rules: ExitRules,
    ulcc_codes: set[str],
) -> dict:
    return {
        "t100_rows_standardized": int(t100_row_count),
        "db1b_rows_standardized": int(db1b_row_count),
        "panel_rows": int(len(panel)),
        "panel_markets": int(panel["market"].nunique()),
        "candidate_exit_events": int(len(events)),
        "treated_markets": int(events["market"].nunique()) if len(events) else 0,
        "exits_by_carrier": (
            events["exiting_carrier"].value_counts().sort_index().to_dict()
            if len(events)
            else {}
        ),
        "first_quarter": str(panel["quarter"].min()),
        "last_quarter": str(panel["quarter"].max()),
        "ulcc_codes": sorted(ulcc_codes),
        "exit_rules": rules.__dict__,
    }


def write_outputs(
    output_dir: Path,
    carrier_market: pd.DataFrame,
    events: pd.DataFrame,
    panel: pd.DataFrame,
    summary: dict,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    carrier_market.assign(quarter=carrier_market["quarter"].astype(str)).to_csv(
        output_dir / "t100_carrier_market_quarter.csv", index=False
    )
    events.to_csv(output_dir / "ulcc_exit_events.csv", index=False)
    panel.assign(quarter=panel["quarter"].astype(str)).to_csv(
        output_dir / "market_quarter_analysis_panel.csv", index=False
    )
    (output_dir / "diagnostics.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
