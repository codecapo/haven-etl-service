"""Enrich exploded council stock with coordinates from the OS Open UPRN Parquet.

Splits the candidate rows into:
  * matched   — have a UPRN → coordinates filled from OS Open UPRN (loadable into
                the council `property` table, which is keyed by UPRN).
  * unmatched — no UPRN → cannot be loaded yet; emitted for the future
                address→UPRN matching phase.
"""

from __future__ import annotations

import csv
from pathlib import Path

import duckdb

from .config import OS_UPRN_PARQUET, DATA_DIR
from .councils.base import PROPERTY_COLUMNS, PropertyRow

_CANDIDATE_FIELDS = PROPERTY_COLUMNS + ["block_code", "source_council"]


def write_candidates_csv(rows: list[PropertyRow], out_csv: str | Path) -> Path:
    """Persist parsed rows to a CSV that DuckDB can read for enrichment."""
    out_csv = Path(out_csv)
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CANDIDATE_FIELDS)
        w.writeheader()
        for r in rows:
            rec = r.to_record()
            w.writerow({k: ("" if rec.get(k) is None else rec.get(k)) for k in _CANDIDATE_FIELDS})
    return out_csv


def enrich(
    candidates_csv: str | Path,
    council: str,
    os_parquet: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> dict:
    """Join candidates → OS UPRN coords; write matched + unmatched Parquet."""
    candidates_csv = Path(candidates_csv)
    os_parquet = Path(os_parquet) if os_parquet else OS_UPRN_PARQUET
    out_dir = Path(out_dir) if out_dir else DATA_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    matched_path = out_dir / f"{council}_property.parquet"
    unmatched_path = out_dir / f"{council}_unmatched.parquet"

    con = duckdb.connect()
    cand = f"read_csv('{candidates_csv.as_posix()}', header=true, all_varchar=true)"
    has_os = os_parquet.exists()

    # Matched: rows with a UPRN. Coordinates come from OS Open UPRN when available.
    # Prefer coordinates already on the candidate (e.g. from the OS Places
    # postcode match), falling back to the OS Open UPRN reference by UPRN.
    if has_os:
        coord_select = (
            "COALESCE(TRY_CAST(NULLIF(c.latitude,'') AS DOUBLE), o.latitude) AS latitude, "
            "COALESCE(TRY_CAST(NULLIF(c.longitude,'') AS DOUBLE), o.longitude) AS longitude"
        )
    else:
        coord_select = (
            "TRY_CAST(NULLIF(c.latitude,'') AS DOUBLE) AS latitude, "
            "TRY_CAST(NULLIF(c.longitude,'') AS DOUBLE) AS longitude"
        )
    coord_join = (
        f"LEFT JOIN read_parquet('{os_parquet.as_posix()}') o ON TRY_CAST(c.uprn AS BIGINT) = o.uprn"
        if has_os
        else ""
    )
    con.execute(
        f"""
        COPY (
            SELECT c.uprn,
                   NULLIF(c.usrn,'') AS usrn,
                   NULLIF(c.toid,'') AS toid,
                   c.property_reference, c.address_line1,
                   NULLIF(c.address_line2,'') AS address_line2,
                   NULLIF(c.postcode,'') AS postcode,
                   NULLIF(c.estate,'') AS estate,
                   NULLIF(c.tenure,'') AS tenure,
                   {coord_select},
                   'import' AS data_source
            FROM {cand} c
            {coord_join}
            WHERE c.uprn IS NOT NULL AND c.uprn <> ''
        ) TO '{matched_path.as_posix()}' (FORMAT PARQUET);
        """
    )
    # Unmatched: no UPRN — parked for address→UPRN matching.
    con.execute(
        f"""
        COPY (
            SELECT * FROM {cand} c WHERE c.uprn IS NULL OR c.uprn = ''
        ) TO '{unmatched_path.as_posix()}' (FORMAT PARQUET);
        """
    )

    def count(p: Path) -> int:
        return con.execute(f"SELECT COUNT(*) FROM read_parquet('{p.as_posix()}')").fetchone()[0]

    matched_n = count(matched_path)
    with_coords = (
        con.execute(
            f"SELECT COUNT(*) FROM read_parquet('{matched_path.as_posix()}') WHERE latitude IS NOT NULL"
        ).fetchone()[0]
        if matched_n
        else 0
    )
    return {
        "matched_path": matched_path,
        "unmatched_path": unmatched_path,
        "matched": matched_n,
        "with_coords": with_coords,
        "unmatched": count(unmatched_path),
        "os_reference_used": has_os,
    }
