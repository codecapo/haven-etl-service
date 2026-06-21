"""Self-contained deployment health check.

Runs the full enrich pipeline on synthetic data written to HAVEN_DATA_DIR — no
external files needed. Use it to verify a fresh deploy (image + machine + mounted
volume + DuckDB all working) without uploading the big OS UPRN file:

    fly machine run . selftest --region lhr --volume haven_data:/data -a <app>
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .config import DATA_DIR
from .councils.base import PropertyRow, explode_block
from .enrich import write_candidates_csv, enrich


def run_selftest(data_dir: str | Path | None = None) -> dict:
    data_dir = Path(data_dir) if data_dir else DATA_DIR
    data_dir.mkdir(parents=True, exist_ok=True)
    checks: list[tuple[str, bool]] = []

    # 1. Block explosion.
    rows = explode_block(council="selftest", block_code="B1", block_address="1-10 Test House (Cons)", units=10)
    checks.append(("block explosion → 10 units", len(rows) == 10))

    # 2. Synthetic OS UPRN parquet on the (mounted) data volume.
    os_parquet = data_dir / "os_uprn_selftest.parquet"
    con = duckdb.connect()
    con.execute(
        f"""
        COPY (SELECT * FROM (VALUES
            (1001::BIGINT, 51.5::DOUBLE, -0.1::DOUBLE),
            (1002::BIGINT, 51.6::DOUBLE, -0.2::DOUBLE)
        ) AS t(uprn, latitude, longitude))
        TO '{os_parquet.as_posix()}' (FORMAT PARQUET);
        """
    )
    checks.append(("write parquet to volume", os_parquet.exists()))

    # 3. Enrich: 2 rows with UPRNs (matched + coords) + 1 without (unmatched).
    cand = [
        PropertyRow(property_reference="S/1001", address_line1="1 Test House", postcode="N1 1AA", uprn="1001"),
        PropertyRow(property_reference="S/1002", address_line1="2 Test House", postcode="N1 1AA", uprn="1002"),
        PropertyRow(property_reference="S/none", address_line1="3 Test House"),
    ]
    csv_path = write_candidates_csv(cand, data_dir / "selftest_candidates.csv")
    res = enrich(csv_path, council="selftest", os_parquet=os_parquet, out_dir=data_dir)
    checks.append(("enrich matched 2", res["matched"] == 2))
    checks.append(("enrich filled coords 2", res["with_coords"] == 2))
    checks.append(("enrich unmatched 1", res["unmatched"] == 1))

    ok = all(passed for _, passed in checks)
    return {
        "ok": ok,
        "data_dir": str(data_dir),
        "duckdb": duckdb.__version__,
        "checks": [{"name": n, "pass": p} for n, p in checks],
    }
