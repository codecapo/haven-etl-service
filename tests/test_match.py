"""OS Places matcher tests — no network (stub matcher + pure response parser)."""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven_etl.os_places import OsPlacesMatcher, PlacesMatch
from haven_etl.match import match_candidates
from haven_etl.enrich import write_candidates_csv
from haven_etl.councils.base import PropertyRow

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'✓' if ok else '✗'} {label}" + ("" if ok else f" — {detail}"))
    if not ok:
        failures += 1


print("1. OS Places /find response parser (pure, no network)")
sample = {
    "results": [
        {
            "DPA": {
                "UPRN": "100023002",
                "ADDRESS": "8 ROMFORD ROAD, LONDON, E15 4LD",
                "POSTCODE": "E15 4LD",
                "LAT": 51.5407,
                "LNG": -0.0001,
                "MATCH": 0.95,
            }
        }
    ]
}
m = OsPlacesMatcher.parse(sample)
check("parses UPRN", m and m.uprn == "100023002", str(m))
check("parses score", m and abs(m.score - 0.95) < 1e-9)
check("parses coords + address", m and m.latitude == 51.5407 and "ROMFORD" in m.matched_address)
check("empty payload → None", OsPlacesMatcher.parse({"results": []}) is None)


# A deterministic stub standing in for the OS Places API.
class StubMatcher:
    def __init__(self):
        self.calls = 0

    def match(self, address, postcode=None):
        self.calls += 1
        if address.startswith("1 Southfleet"):
            return PlacesMatch("999001", 51.5, -0.1, "1 SOUTHFLEET", "NW5 1AA", 0.92)
        if address.startswith("2 Southfleet"):
            return PlacesMatch("999002", None, None, "2 SOUTHFLEET", None, 0.20)  # low confidence
        return None  # no result

    def flush(self):
        pass


print("2. match_candidates fills UPRNs above the threshold")
rows = [
    PropertyRow(property_reference="B/1", address_line1="1 Southfleet", source_council="camden"),
    PropertyRow(property_reference="B/2", address_line1="2 Southfleet", source_council="camden"),
    PropertyRow(property_reference="B/3", address_line1="3 Nowhere", source_council="camden"),
    PropertyRow(property_reference="X/9", address_line1="9 Known St", uprn="555", source_council="camden"),
]
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    in_csv = write_candidates_csv(rows, d / "cand.csv")
    stub = StubMatcher()
    stats = match_candidates(in_csv, d / "matched.csv", stub, min_score=0.4, audit_csv=d / "audit.csv")

    check("skips the row that already has a UPRN", stats["already_uprn"] == 1, str(stats))
    check("attempts the 3 no-UPRN rows", stats["attempted"] == 3, str(stats))
    check("1 confident match", stats["matched"] == 1, str(stats))
    check("1 low-confidence (below 0.4)", stats["low_confidence"] == 1, str(stats))
    check("1 no-result", stats["no_result"] == 1, str(stats))

    out = {r["property_reference"]: r for r in csv.DictReader((d / "matched.csv").open())}
    check("confident match got its UPRN", out["B/1"]["uprn"] == "999001", out["B/1"]["uprn"])
    check("low-confidence stays unmatched", out["B/2"]["uprn"] == "", repr(out["B/2"]["uprn"]))
    check("no-result stays unmatched", out["B/3"]["uprn"] == "", repr(out["B/3"]["uprn"]))
    check("pre-existing UPRN untouched", out["X/9"]["uprn"] == "555", out["X/9"]["uprn"])

    audit = list(csv.DictReader((d / "audit.csv").open()))
    check("audit logs all 3 attempts", len(audit) == 3, str(len(audit)))

print(f"\n{'✅ PASS' if failures == 0 else '❌ FAIL'} — {failures} failure(s)")
sys.exit(1 if failures else 0)
