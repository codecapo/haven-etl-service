"""OS Open UPRN ingest → Parquet.

The OS Open UPRN product is a national table of every UPRN with its coordinates
(UPRN, X, Y, LAT, LONG) — ~41.5M rows, ~2.26GB uncompressed inside a ~589MB zip.
It has NO addresses; it only answers "where is this UPRN".

We hold it ONCE, centrally, as a columnar Parquet file (DuckDB reads it in ms),
refreshed every ~6 weeks. It is NEVER copied into a council database — council
stock is enriched against it centrally, and only the council's own properties
(with coordinates filled in) are pushed to that council's backend.

DuckDB streams the CSV straight out of the zip via fsspec — no full extraction.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import duckdb
import fsspec

from .config import OS_UPRN_PARQUET


def _csv_member(zip_path: Path) -> str:
    """Name of the single CSV inside the OS Open UPRN zip (version varies)."""
    with zipfile.ZipFile(zip_path) as z:
        for name in z.namelist():
            if name.lower().endswith(".csv"):
                return name
    raise FileNotFoundError(f"No .csv member found in {zip_path}")


def build_os_uprn_parquet(
    zip_path: str | Path,
    out_path: str | Path | None = None,
    sample: int | None = None,
) -> Path:
    """Stream the OS Open UPRN zip → a normalized Parquet (uprn, latitude, longitude).

    `sample` limits the row count for fast pipeline testing; omit for the full run.
    """
    zip_path = Path(zip_path).resolve()
    out_path = Path(out_path).resolve() if out_path else OS_UPRN_PARQUET
    out_path.parent.mkdir(parents=True, exist_ok=True)
    member = _csv_member(zip_path)

    con = duckdb.connect()
    # Let DuckDB read members directly out of the zip (no 2.26GB extraction).
    con.register_filesystem(fsspec.filesystem("zip", fo=str(zip_path)))
    limit = f"LIMIT {int(sample)}" if sample else ""

    # all_varchar on read keeps the BOM/typing from biting; we cast explicitly.
    con.execute(
        f"""
        COPY (
            SELECT
                CAST(UPRN AS BIGINT)      AS uprn,
                CAST(LATITUDE AS DOUBLE)  AS latitude,
                CAST(LONGITUDE AS DOUBLE) AS longitude
            FROM read_csv('zip://{member}', header = true, all_varchar = true,
                          normalize_names = true)
            {limit}
        ) TO '{out_path.as_posix()}' (FORMAT PARQUET, COMPRESSION ZSTD);
        """
    )
    return out_path


def parquet_stats(parquet_path: str | Path) -> dict:
    """Row count + a couple of sample rows, for a quick sanity check."""
    con = duckdb.connect()
    p = Path(parquet_path).as_posix()
    n = con.execute(f"SELECT COUNT(*) FROM read_parquet('{p}')").fetchone()[0]
    head = con.execute(f"SELECT * FROM read_parquet('{p}') LIMIT 3").fetchall()
    return {"rows": n, "head": head}
