"""Command-line orchestration and output for spectrum scoring."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

import pandas as pd

from .config import INDEX_NAMES, METRIC_SPECS
from .data import build_raw_metrics, existing_files
from .scoring import score_business_models


def _default_ontime_inputs() -> list[str]:
    complete = Path("data/raw/business_model/on_time")
    limited = Path("data/raw/marketing_on_time")
    if complete.exists():
        return [str(complete)]
    return [str(limited)] if limited.exists() else []


def _metric_definitions() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "index": metric.index,
                "metric": metric.name,
                "direction": "higher_to_fsnc" if metric.higher_is_fsnc else "higher_to_lcc",
                "source": metric.source,
                "description": metric.description,
                "implementation_status": metric.implementation_status,
            }
            for metric in METRIC_SPECS
        ]
    )


def _coverage(raw_metrics: pd.DataFrame) -> pd.DataFrame:
    rows = []
    total = len(raw_metrics)
    for metric in METRIC_SPECS:
        present = int(raw_metrics.get(metric.name, pd.Series(dtype=float)).notna().sum())
        rows.append(
            {
                "index": metric.index,
                "metric": metric.name,
                "carrier_years_present": present,
                "carrier_years_total": total,
                "coverage_ratio": present / total if total else 0,
            }
        )
    return pd.DataFrame(rows)


def _file_count(paths: Sequence[str] | None, label: str) -> int:
    return len(existing_files(paths, label=label))


def run(args: argparse.Namespace) -> dict[str, object]:
    """Build or load raw measures, score them, and write audit-friendly outputs."""
    output = args.output_dir.expanduser()
    output.mkdir(parents=True, exist_ok=True)

    build_diagnostics: dict[str, int] = {}
    if args.metrics_csv:
        raw_metrics = pd.read_csv(args.metrics_csv.expanduser())
        top_airports = pd.DataFrame()
    else:
        ontime_inputs = args.on_time if args.on_time is not None else _default_ontime_inputs()
        raw_metrics, top_airports, build_diagnostics = build_raw_metrics(
            t100_inputs=args.t100,
            db1b_inputs=args.db1b,
            ontime_inputs=ontime_inputs,
            p12_inputs=args.p12,
            p6_inputs=args.p6,
            p10_inputs=args.p10,
            b43_inputs=args.b43,
            cbd_path=args.cbd_distances,
            start_year=args.start_year,
            end_year=args.end_year,
            carriers=set(args.carriers) if args.carriers else None,
            min_departures=args.min_departures,
            min_db1b_quarters=args.min_db1b_quarters,
            min_ontime_months=args.min_ontime_months,
            ontime_carrier_basis=args.on_time_carrier_basis,
        )
        if not top_airports.empty:
            top_airports.to_csv(output / "airport_cbd_distance_template.csv", index=False)

    results = score_business_models(
        raw_metrics,
        decimals=args.percentile_decimals,
        strict=args.strict,
    )
    coverage = _coverage(raw_metrics)
    definitions = _metric_definitions()

    raw_metrics.to_csv(output / "carrier_year_raw_metrics.csv", index=False)
    results.metric_scores.to_csv(output / "carrier_year_metric_scores.csv", index=False)
    results.carrier_year_indices.to_csv(
        output / "carrier_year_index_scores.csv", index=False
    )
    results.carrier_scores.to_csv(output / "carrier_spectrum_scores.csv", index=False)
    coverage.to_csv(output / "metric_coverage.csv", index=False)
    definitions.to_csv(output / "metric_definitions.csv", index=False)

    score_summary = results.carrier_scores[
        [
            "carrier",
            "carrier_name",
            "years_present",
            *[f"{index}_index" for index in INDEX_NAMES],
            "spectrum_score",
            "index_standard_deviation",
            "score_complete",
            "spectrum_side",
        ]
    ]
    score_summary.to_csv(output / "spectrum_summary.csv", index=False)

    diagnostics: dict[str, object] = {
        "method": "Lohmann and Koo (2013) six-index airline business-model spectrum",
        "normalization": (
            "Legacy Excel PERCENTRANK across all observed carrier-years for each metric; "
            "metric ranks averaged within six equally weighted indices; index scores averaged "
            "to form the spectrum score."
        ),
        "spectrum": {"lcc": 0, "fsnc": 1},
        "scope": "Scheduled domestic passenger service (T-100 class F)",
        "start_year": args.start_year,
        "end_year": args.end_year,
        "requested_carriers": args.carriers or "all carriers meeting the departure threshold",
        "min_departures": args.min_departures,
        "min_db1b_quarters": args.min_db1b_quarters,
        "min_ontime_months": args.min_ontime_months,
        "on_time_carrier_basis": args.on_time_carrier_basis,
        "strict": args.strict,
        "carrier_years": len(raw_metrics),
        "carriers": int(raw_metrics["carrier"].nunique()),
        "complete_carrier_scores": int(results.carrier_scores["score_complete"].sum()),
        "build": build_diagnostics,
    }
    with (output / "diagnostics.json").open("w", encoding="utf-8") as stream:
        json.dump(diagnostics, stream, indent=2)
        stream.write("\n")
    return diagnostics


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build the 20 measures in Lohmann and Koo's airline business-model "
            "spectrum and score carriers from LCC-like (0) to FSNC-like (1)."
        )
    )
    parser.add_argument("--t100", nargs="+", default=["data/raw/t100"])
    parser.add_argument("--db1b", nargs="+", default=["data/raw/db1b"])
    parser.add_argument(
        "--on-time",
        nargs="+",
        default=None,
        help=(
            "On-time input(s). Defaults to data/raw/business_model/on_time when "
            "present, otherwise data/raw/marketing_on_time."
        ),
    )
    parser.add_argument("--p12", nargs="+", default=["data/raw/business_model/p12"])
    parser.add_argument("--p6", nargs="+", default=["data/raw/business_model/p6"])
    parser.add_argument("--p10", nargs="+", default=["data/raw/business_model/p10"])
    parser.add_argument("--b43", nargs="+", default=["data/raw/business_model/b43"])
    parser.add_argument(
        "--cbd-distances",
        type=Path,
        default=Path("data/manual/airport_cbd_distances.csv"),
    )
    parser.add_argument(
        "--metrics-csv",
        type=Path,
        help="Score a prepared carrier-year metrics CSV instead of rebuilding measures.",
    )
    parser.add_argument("--start-year", type=int)
    parser.add_argument("--end-year", type=int)
    parser.add_argument("--carriers", nargs="+", help="Optional two-character carrier codes.")
    parser.add_argument("--min-departures", type=float, default=1_000)
    parser.add_argument("--min-db1b-quarters", type=int, default=4)
    parser.add_argument("--min-ontime-months", type=int, default=12)
    parser.add_argument(
        "--on-time-carrier-basis",
        choices=("operating", "marketing"),
        default="operating",
        help=(
            "Attribute punctuality to the operating carrier (default) or marketing "
            "carrier. Operating is consistent with the T-100 carrier basis."
        ),
    )
    parser.add_argument("--percentile-decimals", type=int, default=3)
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Leave the overall score blank unless all 20 measures are present.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("output/business_model_spectrum"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.start_year and args.end_year and args.end_year < args.start_year:
        raise ValueError("--end-year must not precede --start-year")
    diagnostics = run(args)
    print(
        "Scoring complete: "
        f"{diagnostics['carrier_years']} carrier-years and {diagnostics['carriers']} carriers. "
        f"Outputs: {args.output_dir.expanduser()}"
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2)
