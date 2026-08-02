"""Shared helpers for discovering, reading, and downloading BTS data files."""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from typing import BinaryIO, Iterable, Sequence

import pandas as pd
import requests


SUPPORTED_SUFFIXES = (".csv", ".csv.gz", ".zip", ".parquet")
DEFAULT_CHUNK_SIZE = 250_000
WEBFORM_STATE_FIELDS = (
    "__VIEWSTATE",
    "__VIEWSTATEGENERATOR",
    "__EVENTVALIDATION",
)


def normalize_column_name(column: object) -> str:
    """Return the canonical form used to match inconsistent BTS headers."""
    return re.sub(r"[^A-Z0-9]+", "_", str(column).strip().upper()).strip("_")


def normalize_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a frame's headers without mutating the caller's frame."""
    result = frame.copy()
    result.columns = [normalize_column_name(column) for column in result.columns]
    return result


def clean_code(values: pd.Series) -> pd.Series:
    """Normalize airline and airport codes."""
    return values.astype("string").str.strip().str.upper()


def canonical_airport_pair(
    origin: pd.Series, destination: pd.Series, *, directional: bool = False
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Build consistently ordered airport-pair columns and a route identifier."""
    origin = clean_code(origin)
    destination = clean_code(destination)
    if directional:
        return origin, destination, origin + "->" + destination
    forward = origin <= destination
    airport_1 = origin.where(forward, destination)
    airport_2 = destination.where(forward, origin)
    return airport_1, airport_2, airport_1 + "-" + airport_2


def discover_files(inputs: Sequence[str | Path], *, label: str = "input") -> list[Path]:
    """Expand input files/directories into a stable de-duplicated file list."""
    files: list[Path] = []
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.is_dir():
            files.extend(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file()
                and candidate.name.lower().endswith(SUPPORTED_SUFFIXES)
            )
        elif path.is_file():
            files.append(path)
        else:
            raise FileNotFoundError(f"{label} does not exist: {path}")
    unique = sorted({path.resolve() for path in files})
    if not unique:
        raise FileNotFoundError(f"No supported {label} files were found.")
    return unique


def _selected_actual_columns(
    columns: Iterable[object], wanted_columns: set[str] | None
) -> list[object] | None:
    if wanted_columns is None:
        return None
    return [
        column
        for column in columns
        if normalize_column_name(column) in wanted_columns
    ]


def _read_csv_chunks(
    stream_or_path: str | Path | BinaryIO,
    *,
    wanted_columns: set[str] | None,
    chunksize: int,
) -> Iterable[pd.DataFrame]:
    """Read a CSV in chunks, loading only requested headers when supplied."""
    usecols = None
    if wanted_columns is not None:
        header = pd.read_csv(stream_or_path, nrows=0)
        usecols = _selected_actual_columns(header.columns, wanted_columns)
        if hasattr(stream_or_path, "seek"):
            stream_or_path.seek(0)
    yield from pd.read_csv(
        stream_or_path,
        usecols=usecols,
        chunksize=chunksize,
        low_memory=False,
    )


def read_table_chunks(
    path: Path,
    *,
    wanted_columns: set[str] | None = None,
    chunksize: int = DEFAULT_CHUNK_SIZE,
) -> Iterable[pd.DataFrame]:
    """Yield chunks from CSV, compressed CSV, ZIP, or Parquet inputs."""
    lower = path.name.lower()
    if lower.endswith(".parquet"):
        frame = pd.read_parquet(path)
        selected = _selected_actual_columns(frame.columns, wanted_columns)
        yield frame if selected is None else frame[selected]
        return
    if lower.endswith(".zip"):
        with zipfile.ZipFile(path) as archive:
            members = [
                name
                for name in archive.namelist()
                if name.lower().endswith((".csv", ".txt"))
                and not name.startswith("__MACOSX/")
            ]
            if not members:
                raise ValueError(f"No CSV/TXT member found in {path}")
            for member in members:
                with archive.open(member) as stream:
                    yield from _read_csv_chunks(
                        stream,
                        wanted_columns=wanted_columns,
                        chunksize=chunksize,
                    )
        return
    yield from _read_csv_chunks(
        path,
        wanted_columns=wanted_columns,
        chunksize=chunksize,
    )


def valid_zip(path: Path) -> bool:
    """Return whether a path contains a non-empty, valid ZIP archive."""
    if not path.exists() or path.stat().st_size == 0:
        return False
    try:
        with zipfile.ZipFile(path) as archive:
            return bool(archive.namelist()) and archive.testzip() is None
    except zipfile.BadZipFile:
        return False


def webform_state(page: str, *, form_name: str = "BTS form") -> dict[str, str]:
    """Extract ASP.NET state fields needed to submit BTS download forms."""
    fields: dict[str, str] = {}
    for name in WEBFORM_STATE_FIELDS:
        patterns = (
            rf'name="{re.escape(name)}"[^>]*value="([^"]*)"',
            rf'id="{re.escape(name)}"[^>]*value="([^"]*)"',
        )
        match = next(
            (
                found
                for pattern in patterns
                if (found := re.search(pattern, page, flags=re.IGNORECASE))
            ),
            None,
        )
        if match is None:
            raise RuntimeError(f"Could not find {name} on the {form_name}.")
        fields[name] = html.unescape(match.group(1))
    return fields


def stream_response_to_zip(
    response: requests.Response, destination: Path
) -> None:
    """Atomically stream an HTTP response to a validated ZIP file."""
    response.raise_for_status()
    if "text/html" in response.headers.get("Content-Type", "").lower():
        preview = response.text[:300].replace("\n", " ")
        raise RuntimeError(
            f"BTS returned HTML instead of a ZIP for {destination.name}: {preview}"
        )
    temporary = destination.with_suffix(destination.suffix + ".part")
    with temporary.open("wb") as output:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                output.write(chunk)
    if not valid_zip(temporary):
        temporary.unlink(missing_ok=True)
        raise RuntimeError(f"Downloaded file is not a valid ZIP: {destination.name}")
    temporary.replace(destination)
