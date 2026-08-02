# Airline Route Exit Research

This project builds research datasets from U.S. Bureau of Transportation
Statistics (BTS) data. It contains two related pipelines:

1. `expand_ulcc_dataset.py` creates a quarterly panel around ultra-low-cost
   carrier (ULCC) exits for staggered difference-in-differences analysis.
2. `identify_airline_route_exits.py` identifies monthly nonstop route exits by
   any marketing or operating carrier and creates readable summary reports.

Both commands are lightweight entry points. The implementation lives in the
modular `airline_research` package, with separate files for data loading,
analysis, downloads, configuration, and reporting.

## Setup

Python 3.10 or newer is recommended.

```bash
python3 -m pip install -r requirements.txt
```

Run commands from the project directory. By default, raw files are stored under
`data/raw` and generated results are written under `output`.

## Quarterly ULCC exit panel

The quarterly pipeline combines T-100 operating data with DB1B Market fare data.
Its main output is a market-quarter panel containing operating outcomes, fare
outcomes, treatment timing, and relative event time.

### Default exit definition

A carrier-market is classified as a candidate exit when:

- the carrier is Frontier (`F9`), Spirit (`NK`), or Allegiant (`G4`);
- it was active in at least three of the previous four quarters;
- it operated at least 12 departures during that pre-exit window;
- it records no service during the next four quarters; and
- another carrier continues serving the market for at least three quarters.

Markets are undirected airport pairs. For example, `ALB-MCO` and `MCO-ALB` are
treated as the same market. All thresholds and carrier codes can be changed with
command-line options.

### Download data and build the panel

```bash
python3 expand_ulcc_dataset.py \
  --download \
  --start-quarter 2021Q1 \
  --end-quarter 2025Q2
```

Valid existing ZIP archives are reused, so interrupted downloads can be resumed.
DB1B files are large and may require substantial disk space.

Quarterly DB1B data ends at 2025Q2. Later T-100 quarters can be included, but
those observations will not have DB1B fare outcomes.

### Build from existing files

```bash
python3 expand_ulcc_dataset.py \
  --start-quarter 2021Q1 \
  --end-quarter 2025Q2
```

The default input directories are:

```text
data/raw/t100
data/raw/db1b
```

To use a broader ULCC definition that includes Sun Country:

```bash
python3 expand_ulcc_dataset.py --ulcc F9 NK G4 SY
```

Use `python3 expand_ulcc_dataset.py --help` to see all threshold and path
options.

### Quarterly outputs

Results are written to `output/expanded_dataset` by default:

- `ulcc_exit_events.csv` — one row per qualifying carrier-market exit.
- `market_quarter_analysis_panel.csv` — market outcomes and treatment timing.
- `t100_carrier_market_quarter.csv` — standardized carrier-market operations.
- `diagnostics.json` — coverage, event counts, carrier codes, and exit rules.

The analysis panel retains never-treated and not-yet-treated markets.
`treatment_cohort` records the first qualifying ULCC exit in each market, while
`relative_quarter` measures event time around that exit.

## Monthly exits by any airline

The monthly pipeline identifies carriers that stop serving an exact nonstop
airport pair for at least 12 consecutive months. Connecting itineraries are not
examined.

Marketing-carrier mode is the default. It attributes service to the airline that
sold the flight while retaining the physical operator separately, which avoids
mistaking a change in regional operating partner for a branded airline exit.

### Marketing-carrier analysis

Download missing BTS Marketing Carrier On-Time archives and create the reports:

```bash
python3 identify_airline_route_exits.py --download-marketing-data
```

Later runs can reuse the downloaded archives:

```bash
python3 identify_airline_route_exits.py
```

By default, an event must have:

- at least 12 consecutive months without service;
- active service in all 12 months before the exit; and
- at least 10 performed flights during that pre-exit year.

The presence of another airline on the route is reported but is not required.
To require a competing airline in the exit-start month, run:

```bash
python3 identify_airline_route_exits.py \
  --require-competing-airline-at-exit
```

### Operator-level analysis

To define exits using the physical operating carrier in T-100 data:

```bash
python3 identify_airline_route_exits.py --carrier-level operator
```

Use `python3 identify_airline_route_exits.py --help` for the complete set of
date, service-class, direction, threshold, and output options.

### Monthly outputs

The command creates:

- `output/airline_route_exits.txt` — a readable event-by-event report.
- `output/route_exit_summary_tables.xlsx` — an Excel workbook with `Exit Events`,
  `By Airline`, and `By Airport` worksheets.

For every exit, the reports identify other airlines serving the exact route in
the exit month and measure how long that alternative nonstop service continues.

## Supported input files

Both pipelines accept individual files or directories containing:

- `.csv`
- `.csv.gz`
- `.zip` archives containing CSV or text files
- `.parquet`

The loaders recognize common BTS header variants, including compact DB1B names
such as `MktFare` and `MktDistance`. If quarterly files do not contain both
`YEAR` and `QUARTER`, their filenames must include a token such as `2024Q3`.

Large inputs are processed in chunks. Only required columns are loaded, and
archives that clearly fall outside the requested date range are skipped.

## Project structure

```text
.
├── expand_ulcc_dataset.py
├── identify_airline_route_exits.py
├── requirements.txt
└── src/
    ├── bts.py
    ├── ulcc/
    │   ├── analysis.py
    │   ├── cli.py
    │   ├── config.py
    │   ├── data.py
    │   ├── download.py
    │   └── reporting.py
    └── route_exits/
        ├── analysis.py
        ├── cli.py
        ├── config.py
        ├── data.py
        ├── download.py
        └── reporting.py
```

The top-level scripts remain available for existing commands and notebooks.
Reusable functions can also be imported directly from `airline_research.ulcc`
or `airline_research.route_exits`.

## Research guidance

Exit classifications should be manually audited before estimation. Temporary
suspensions, seasonal schedules, airport substitutions, and carrier-code changes
can resemble permanent exits.

Recommended sensitivity checks include:

- shorter and longer required absence windows;
- stricter pre-exit activity thresholds;
- alternative ULCC definitions;
- minimum market-size restrictions;
- directional versus undirected routes; and
- balanced event windows before and after treatment.

For staggered treatment designs, use an estimator designed for heterogeneous
treatment timing, such as Callaway-Sant'Anna or Sun-Abraham. A basic two-way
fixed-effects regression is not equivalent to those estimators.
