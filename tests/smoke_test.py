"""End-to-end smoke test (no external DB needed).

Proves: block explosion, the OS UPRN coords-join (enrich), the matched/unmatched
split, and the load plan (dry-run). Run after building a sample os_uprn.parquet:

    python -m haven_etl os-uprn --zip <zip> --sample 500000
    ./.venv/bin/python tests/smoke_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import duckdb

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven_etl.config import OS_UPRN_PARQUET, DATA_DIR
from haven_etl.councils.base import PropertyRow, explode_block
from haven_etl.enrich import write_candidates_csv, enrich
from haven_etl.load import load_to_postgres

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'✓' if ok else '✗'} {label}" + ("" if ok else f" — {detail}"))
    if not ok:
        failures += 1


print("1. Block explosion")
# Ranges are authoritative door numbers (61 + 23 = 84), and take precedence over
# the Units total (78) — a real discrepancy worth surfacing as a DQ signal.
rows = explode_block(council="test", block_code="B999", block_address="1-61 & 95-117 Redman House (Cons)", units=78)
nums = [r.address_line1 for r in rows]
check("multi-range '1-61 & 95-117' explodes to 84 door numbers", len(rows) == 84, f"got {len(rows)}")
check("ranges override the Units total (78)", len(rows) != 78)
check("base name parsed (no range/paren left)", nums[0] == "1 Redman House", f"got {nums[0]!r}")
check("second range starts at 95", "95 Redman House" in nums, "missing 95")
check("no gap numbers (62-94 excluded)", "70 Redman House" not in nums)

print("2. Enrich — coords join against OS UPRN parquet")
if not Path(OS_UPRN_PARQUET).exists():
    check("os_uprn.parquet exists", False, f"build it first: {OS_UPRN_PARQUET}")
else:
    # Pull 3 real UPRNs (with their true coords) from the sample parquet.
    con = duckdb.connect()
    sample = con.execute(
        f"SELECT uprn, latitude, longitude FROM read_parquet('{Path(OS_UPRN_PARQUET).as_posix()}') LIMIT 3"
    ).fetchall()
    cand = [
        PropertyRow(
            property_reference=f"T/{uprn}",
            address_line1=f"{i+1} Test Street",
            postcode="N1 1AA",
            uprn=str(uprn),
            source_council="test",
        )
        for i, (uprn, _lat, _lng) in enumerate(sample)
    ]
    # Plus one with NO uprn (should land in unmatched).
    cand.append(PropertyRow(property_reference="T/none", address_line1="9 Nowhere", source_council="test"))

    csv_path = write_candidates_csv(cand, DATA_DIR / "test_candidates.csv")
    res = enrich(csv_path, council="test")
    check("3 UPRN rows matched", res["matched"] == 3, f"got {res['matched']}")
    check("1 no-UPRN row unmatched", res["unmatched"] == 1, f"got {res['unmatched']}")
    check("all matched rows got coordinates", res["with_coords"] == 3, f"got {res['with_coords']}")

    # Coordinates must equal the OS source values exactly.
    got = con.execute(
        f"SELECT uprn, latitude, longitude FROM read_parquet('{Path(res['matched_path']).as_posix()}') ORDER BY uprn"
    ).fetchall()
    want = sorted((str(u), lat, lng) for u, lat, lng in sample)
    got_norm = sorted((str(u), lat, lng) for u, lat, lng in got)
    check("joined coordinates match OS source", got_norm == want, f"{got_norm} != {want}")

    print("3. Load — dry-run plan")
    plan = load_to_postgres(res["matched_path"], council="test", dry_run=True)
    check("dry-run reports 3 rows, no connection", plan["rows"] == 3 and plan["loaded"] is False, str(plan))

print(f"\n{'✅ PASS' if failures == 0 else '❌ FAIL'} — {failures} failure(s)")
sys.exit(1 if failures else 0)
