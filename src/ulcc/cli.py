"""Command-line orchestration for the quarterly ULCC-exit pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .analysis import aggregate_market_operations, build_analysis_panel, identify_exits
from .config import ExitRules, ULCC_DEFAULT
from .data import load_db1b_aggregated, load_t100_aggregated
from .download import download_inputs
from .reporting import diagnostics, write_outputs


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create an expanded, staggered-DiD-ready ULCC exit dataset."
    )
    parser.add_argument(
        "--t100",
        nargs="+",
        default=["data/raw/t100"],
        help="T-100 files or directories (default: data/raw/t100).",
    )
    parser.add_argument(
        "--db1b",
        nargs="+",
        default=["data/raw/db1b"],
        help="DB1B Market files or directories (default: data/raw/db1b).",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="Download missing official BTS files before building the panel.",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=Path("output/expanded_dataset")
    )
    parser.add_argument(
        "--ulcc",
        nargs="+",
        default=list(ULCC_DEFAULT),
        help="Two-character carrier codes treated as ULCCs (default: F9 NK G4).",
    )
    parser.add_argument("--start-quarter", help="Optional first quarter, e.g. 2019Q1.")
    parser.add_argument("--end-quarter", help="Optional last quarter, e.g. 2025Q4.")
    parser.add_argument("--min-pre-quarters", type=int, default=4)
    parser.add_argument("--min-post-quarters", type=int, default=4)
    parser.add_argument(
        "--min-pre-active-quarters",
        type=int,
        default=3,
        help="Required active quarters within the pre-exit window.",
    )
    parser.add_argument(
        "--min-pre-departures",
        type=float,
        default=12,
        help="Minimum total departures during the pre-exit window.",
    )
    parser.add_argument(
        "--max-gap-quarters",
        type=int,
        default=1,
        help="Maximum inactive quarters allowed inside the pre-exit window.",
    )
    parser.add_argument(
        "--require-other-service-quarters",
        type=int,
        default=3,
        help="Post-exit quarters in which another carrier must serve the market.",
    )
    parser.add_argument("--fare-min", type=float, default=20)
    parser.add_argument("--fare-max", type=float, default=5000)
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if args.min_pre_quarters < 1 or args.min_post_quarters < 1:
        raise ValueError("Pre- and post-exit windows must each be at least 1 quarter.")
    if args.min_pre_active_quarters > args.min_pre_quarters:
        raise ValueError("--min-pre-active-quarters cannot exceed --min-pre-quarters.")
    if args.min_pre_active_quarters < 0 or args.max_gap_quarters < 0:
        raise ValueError("Active-quarter and gap thresholds cannot be negative.")
    if args.require_other_service_quarters < 0:
        raise ValueError("--require-other-service-quarters cannot be negative.")
    if args.fare_min > args.fare_max:
        raise ValueError("--fare-min cannot exceed --fare-max.")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _validate_args(args)
    rules = ExitRules(
        min_pre_quarters=args.min_pre_quarters,
        min_post_quarters=args.min_post_quarters,
        min_pre_departures=args.min_pre_departures,
        min_pre_active_quarters=args.min_pre_active_quarters,
        max_gap_quarters=args.max_gap_quarters,
        require_other_service_quarters=args.require_other_service_quarters,
    )
    ulcc_codes = {code.strip().upper() for code in args.ulcc}
    if args.download:
        download_inputs(args)
    carrier_market, t100_row_count = load_t100_aggregated(
        args.t100, args.start_quarter, args.end_quarter
    )
    fares, db1b_row_count = load_db1b_aggregated(
        args.db1b,
        args.start_quarter,
        args.end_quarter,
        args.fare_min,
        args.fare_max,
    )
    events = identify_exits(carrier_market, ulcc_codes, rules)
    operations = aggregate_market_operations(carrier_market)
    panel = build_analysis_panel(operations, fares, events)
    summary = diagnostics(
        t100_row_count,
        db1b_row_count,
        events,
        panel,
        rules,
        ulcc_codes,
    )
    write_outputs(args.output_dir, carrier_market, events, panel, summary)
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(f"\nWrote outputs to {args.output_dir.resolve()}")
    return 0
