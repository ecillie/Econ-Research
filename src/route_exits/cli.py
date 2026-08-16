"""Command-line orchestration for monthly airline route-exit analysis."""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from .analysis import identify_exit_episodes
from .data import load_marketing_monthly_routes, load_monthly_routes
from .download import ensure_marketing_data
from .reporting import write_excel_tables, write_text_report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Find scheduled passenger airlines that left a nonstop domestic "
            "airport-pair route for at least 12 consecutive months."
        )
    )
    parser.add_argument(
        "--t100",
        nargs="+",
        default=["data/raw/t100"],
        help="T-100 Segment files or directories (default: data/raw/t100).",
    )
    parser.add_argument(
        "--carrier-level",
        choices=("marketing", "operator"),
        default="marketing",
        help=(
            "Define exits for the branded marketing carrier (default) or the "
            "physical operator."
        ),
    )
    parser.add_argument(
        "--marketing-data",
        type=Path,
        default=Path("data/raw/marketing_on_time"),
        help="Folder for BTS Marketing Carrier On-Time extracts.",
    )
    parser.add_argument(
        "--download-marketing-data",
        action="store_true",
        help="Download missing minimal marketing-carrier extracts from BTS.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/airline_route_exits.txt"),
        help="Destination text file.",
    )
    parser.add_argument(
        "--excel-output",
        type=Path,
        default=Path("output/route_exit_summary_tables.xlsx"),
        help="Destination Excel workbook containing airline and airport tables.",
    )
    parser.add_argument(
        "--min-absence-months",
        type=int,
        default=12,
        help="Consecutive inactive months required for an exit (default: 12).",
    )
    parser.add_argument(
        "--min-active-departures",
        type=float,
        default=1,
        help="Performed departures required for an active route-month.",
    )
    parser.add_argument(
        "--min-active-months-before-exit",
        type=int,
        default=12,
        help="Active months required in the 12 months before exit (default: 12).",
    )
    parser.add_argument(
        "--min-performed-flights-before-exit",
        type=float,
        default=10,
        help="Minimum performed flights during the 12 months before exit.",
    )
    parser.add_argument(
        "--require-competing-airline-at-exit",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Require another airline on the exact route in the exit-start month.",
    )
    parser.add_argument(
        "--service-classes",
        nargs="+",
        default=["F"],
        help="T-100 service classes to include (default: F).",
    )
    parser.add_argument(
        "--all-service-classes",
        action="store_true",
        help="Include nonscheduled, cargo-only, and other service classes.",
    )
    parser.add_argument(
        "--directional",
        action="store_true",
        help="Treat A-to-B and B-to-A as different routes.",
    )
    parser.add_argument("--start-month", help="Optional first month in YYYY-MM format.")
    parser.add_argument("--end-month", help="Optional last month in YYYY-MM format.")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    return build_parser().parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if args.min_absence_months < 1:
        raise ValueError("--min-absence-months must be at least 1.")
    if not 1 <= args.min_active_months_before_exit <= 12:
        raise ValueError("--min-active-months-before-exit must be between 1 and 12.")
    if args.min_performed_flights_before_exit < 0:
        raise ValueError("--min-performed-flights-before-exit cannot be negative.")
    if args.min_active_departures <= 0:
        raise ValueError("--min-active-departures must be positive.")
    if args.start_month and args.end_month:
        if pd.Period(args.end_month, freq="M") < pd.Period(args.start_month, freq="M"):
            raise ValueError("--end-month must not precede --start-month.")


def run_analysis(argv: list[str] | None = None):
    args = parse_args(argv)
    _validate_args(args)

    operator_detail = None

    if args.carrier_level == "marketing":
        if args.download_marketing_data:
            ensure_marketing_data(args)

        monthly, operator_detail, rows_read = load_marketing_monthly_routes(args)

    else:
        monthly, rows_read = load_monthly_routes(args)

    events = identify_exit_episodes(
        monthly,
        min_absence_months=args.min_absence_months,
        min_active_months_before_exit=args.min_active_months_before_exit,
        min_performed_flights_before_exit=args.min_performed_flights_before_exit,
        require_competing_airline_at_exit=args.require_competing_airline_at_exit,
        operator_detail=operator_detail,
    )

    return events

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _validate_args(args)
    operator_detail = None
    if args.carrier_level == "marketing":
        if args.download_marketing_data:
            ensure_marketing_data(args)
        monthly, operator_detail, rows_read = load_marketing_monthly_routes(args)
    else:
        monthly, rows_read = load_monthly_routes(args)
    events = identify_exit_episodes(
        monthly,
        min_absence_months=args.min_absence_months,
        min_active_months_before_exit=args.min_active_months_before_exit,
        min_performed_flights_before_exit=args.min_performed_flights_before_exit,
        require_competing_airline_at_exit=args.require_competing_airline_at_exit,
        operator_detail=operator_detail,
    )
    write_text_report(events, args.output, args=args, rows_read=rows_read)
    write_excel_tables(events, args.excel_output)
    print(
        f"Wrote {len(events):,} qualifying exit episodes to {args.output.resolve()}"
    )
    print(f"Wrote Excel summary tables to {args.excel_output.resolve()}")
    return 0
