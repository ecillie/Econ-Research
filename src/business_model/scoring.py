"""Percent-rank scoring for the airline business-model spectrum."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import INDEX_NAMES, METRIC_SPECS, METRICS_BY_INDEX


IDENTIFIER_COLUMNS = ("carrier", "carrier_name", "year")


@dataclass(frozen=True)
class ScoreResults:
    """All normalized and aggregated score tables."""

    metric_scores: pd.DataFrame
    carrier_year_indices: pd.DataFrame
    carrier_scores: pd.DataFrame


def excel_percentrank(
    values: pd.Series,
    *,
    higher_is_fsnc: bool,
    decimals: int | None = 3,
) -> pd.Series:
    """Return Excel PERCENTRANK-compatible scores for observed values.

    The source paper used the legacy Excel PERCENTRANK function. For a value
    found in the sample, Excel uses the first matching position in the sorted
    array divided by ``n - 1``. This matters when several carrier-years tie.
    """
    numeric = pd.to_numeric(values, errors="coerce")
    observed = np.sort(numeric.dropna().to_numpy(dtype=float))
    result = pd.Series(np.nan, index=values.index, dtype=float)
    if observed.size == 0:
        return result
    if observed.size == 1 or observed[0] == observed[-1]:
        result.loc[numeric.notna()] = 0.5
        return result

    valid = numeric.notna()
    ranks = np.searchsorted(observed, numeric.loc[valid].to_numpy(), side="left")
    percentiles = ranks / (observed.size - 1)
    if not higher_is_fsnc:
        percentiles = 1.0 - percentiles
    if decimals is not None:
        percentiles = np.round(percentiles, decimals)
    result.loc[valid] = percentiles
    return result


def _require_columns(frame: pd.DataFrame) -> None:
    missing_identifiers = {"carrier", "year"} - set(frame.columns)
    if missing_identifiers:
        raise ValueError(
            f"Raw metrics are missing identifier columns: {sorted(missing_identifiers)}"
        )
    if frame.duplicated(["carrier", "year"]).any():
        duplicates = frame.loc[
            frame.duplicated(["carrier", "year"], keep=False), ["carrier", "year"]
        ]
        raise ValueError(
            "Raw metrics must have one row per carrier-year; duplicates include "
            f"{duplicates.head().to_dict(orient='records')}"
        )


def _metric_score_table(
    raw_metrics: pd.DataFrame,
    *,
    decimals: int | None,
) -> pd.DataFrame:
    identifiers = raw_metrics[[column for column in IDENTIFIER_COLUMNS if column in raw_metrics]]
    pieces: list[pd.DataFrame] = []
    for metric in METRIC_SPECS:
        raw = (
            raw_metrics[metric.name]
            if metric.name in raw_metrics
            else pd.Series(np.nan, index=raw_metrics.index, dtype=float)
        )
        scores = excel_percentrank(
            raw,
            higher_is_fsnc=metric.higher_is_fsnc,
            decimals=decimals,
        )
        piece = identifiers.copy()
        piece["index"] = metric.index
        piece["metric"] = metric.name
        piece["raw_value"] = pd.to_numeric(raw, errors="coerce")
        piece["percent_rank"] = scores
        piece["higher_is_fsnc"] = metric.higher_is_fsnc
        piece["direction"] = "higher_to_fsnc" if metric.higher_is_fsnc else "higher_to_lcc"
        piece["source"] = metric.source
        piece["implementation_status"] = metric.implementation_status
        pieces.append(piece)
    return pd.concat(pieces, ignore_index=True)


def _carrier_year_indices(metric_scores: pd.DataFrame) -> pd.DataFrame:
    keys = [column for column in IDENTIFIER_COLUMNS if column in metric_scores]
    grouped = metric_scores.groupby(keys + ["index"], observed=True, as_index=False).agg(
        index_score=("percent_rank", "mean"),
        metrics_present=("percent_rank", "count"),
    )
    expected = {index: len(metrics) for index, metrics in METRICS_BY_INDEX.items()}
    grouped["metrics_expected"] = grouped["index"].map(expected).astype(int)
    grouped["coverage_ratio"] = grouped["metrics_present"] / grouped["metrics_expected"]
    grouped["index_complete"] = grouped["metrics_present"] == grouped["metrics_expected"]

    scores = grouped.pivot(index=keys, columns="index", values="index_score")
    coverage = grouped.pivot(index=keys, columns="index", values="coverage_ratio")
    complete = grouped.pivot(index=keys, columns="index", values="index_complete")
    scores = scores.reindex(columns=INDEX_NAMES).add_suffix("_index")
    coverage = coverage.reindex(columns=INDEX_NAMES).add_suffix("_coverage")
    complete = complete.reindex(columns=INDEX_NAMES).add_suffix("_complete")
    result = pd.concat([scores, coverage, complete], axis=1).reset_index()
    score_columns = [f"{index}_index" for index in INDEX_NAMES]
    result["spectrum_score"] = result[score_columns].mean(axis=1)
    result["index_standard_deviation"] = result[score_columns].std(axis=1, ddof=1)
    result["indices_present"] = result[score_columns].notna().sum(axis=1)
    result["score_complete"] = result[
        [f"{index}_complete" for index in INDEX_NAMES]
    ].all(axis=1)
    return result.sort_values(["year", "spectrum_score", "carrier"])


def _carrier_scores(metric_scores: pd.DataFrame) -> pd.DataFrame:
    keys = [column for column in ("carrier", "carrier_name") if column in metric_scores]
    grouped = metric_scores.groupby(keys + ["index"], observed=True, as_index=False).agg(
        index_score=("percent_rank", "mean"),
        metric_years_present=("percent_rank", "count"),
    )
    years = metric_scores.groupby(keys, observed=True)["year"].nunique().rename("years_present")
    grouped = grouped.merge(years.reset_index(), on=keys, how="left")
    expected_per_year = {index: len(metrics) for index, metrics in METRICS_BY_INDEX.items()}
    grouped["metric_years_expected"] = grouped["index"].map(expected_per_year) * grouped[
        "years_present"
    ]
    grouped["coverage_ratio"] = (
        grouped["metric_years_present"] / grouped["metric_years_expected"]
    )
    grouped["index_complete"] = (
        grouped["metric_years_present"] == grouped["metric_years_expected"]
    )

    scores = grouped.pivot(index=keys, columns="index", values="index_score")
    coverage = grouped.pivot(index=keys, columns="index", values="coverage_ratio")
    complete = grouped.pivot(index=keys, columns="index", values="index_complete")
    scores = scores.reindex(columns=INDEX_NAMES).add_suffix("_index")
    coverage = coverage.reindex(columns=INDEX_NAMES).add_suffix("_coverage")
    complete = complete.reindex(columns=INDEX_NAMES).add_suffix("_complete")
    result = pd.concat([scores, coverage, complete], axis=1).reset_index()
    result = result.merge(years.reset_index(), on=keys, how="left")
    score_columns = [f"{index}_index" for index in INDEX_NAMES]
    result["spectrum_score"] = result[score_columns].mean(axis=1)
    result["index_standard_deviation"] = result[score_columns].std(axis=1, ddof=1)
    result["indices_present"] = result[score_columns].notna().sum(axis=1)
    result["score_complete"] = result[
        [f"{index}_complete" for index in INDEX_NAMES]
    ].all(axis=1)
    result["spectrum_side"] = np.select(
        [result["spectrum_score"] < 0.5, result["spectrum_score"] > 0.5],
        ["closer_to_lcc", "closer_to_fsnc"],
        default="midpoint",
    )
    result.loc[~result["score_complete"], "spectrum_side"] = (
        "partial_" + result.loc[~result["score_complete"], "spectrum_side"]
    )
    return result.sort_values(["spectrum_score", "carrier"])


def score_business_models(
    raw_metrics: pd.DataFrame,
    *,
    decimals: int | None = 3,
    strict: bool = False,
) -> ScoreResults:
    """Score carrier-year metrics and aggregate them to the six-index spectrum."""
    _require_columns(raw_metrics)
    working = raw_metrics.copy()
    if "carrier_name" not in working:
        working["carrier_name"] = working["carrier"]
    working["carrier"] = working["carrier"].astype("string").str.strip().str.upper()
    working["year"] = pd.to_numeric(working["year"], errors="raise").astype(int)

    metric_scores = _metric_score_table(working, decimals=decimals)
    carrier_year = _carrier_year_indices(metric_scores)
    carrier = _carrier_scores(metric_scores)
    if strict:
        carrier_year.loc[~carrier_year["score_complete"], "spectrum_score"] = np.nan
        carrier.loc[~carrier["score_complete"], "spectrum_score"] = np.nan
        carrier["spectrum_side"] = np.where(
            carrier["spectrum_score"].isna(), "incomplete", carrier["spectrum_side"]
        )
    return ScoreResults(metric_scores, carrier_year, carrier)
