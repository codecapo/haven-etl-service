"""OS Linked Identifiers enrichment tests — no network."""

from __future__ import annotations

import csv
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from haven_etl.os_links import OsLinksMatcher, LinkedIds
from haven_etl.links import enrich_links
from haven_etl.enrich import write_candidates_csv
from haven_etl.councils.base import PropertyRow

failures = 0


def check(label, ok, detail=""):
    global failures
    print(f"  {'✓' if ok else '✗'} {label}" + ("" if ok else f" — {detail}"))
    if not ok:
        failures += 1


print("1. Linked Identifiers parser (shape-agnostic, no network)")
payload = {
    "linkedIdentifiers": [
        {"correlations": [{"correlatedIdentifiers": [
            {"identifier": "100023002", "identifierType": "UPRN"},
            {"identifier": "20900493", "identifierType": "USRN"},
        ]}]},
        {"correlations": [{"correlatedIdentifiers": [
            {"identifier": "osgb1000005207182", "identifierType": "TOID"},
        ]}]},
    ]
}
ids = OsLinksMatcher.parse(payload)
check("extracts USRN", ids.usrn == "20900493", str(ids))
check("extracts TOID", ids.toid == "osgb1000005207182", str(ids))
empty = OsLinksMatcher.parse({})
check("empty payload → no ids", empty.usrn is None and empty.toid is None)


class StubLinks:
    def __init__(self):
        self.calls = 0

    def lookup(self, uprn):
        self.calls += 1
        if uprn == "100023002":
            return LinkedIds("20900493", "osgb1000005207182")
        return LinkedIds(None, None)

    def flush(self):
        pass


print("2. enrich_links fills USRN/TOID for rows with a UPRN")
rows = [
    PropertyRow(property_reference="A/1", address_line1="8 Romford Road", uprn="100023002", source_council="x"),
    PropertyRow(property_reference="A/2", address_line1="9 Nowhere", source_council="x"),  # no UPRN
]
with tempfile.TemporaryDirectory() as d:
    d = Path(d)
    in_csv = write_candidates_csv(rows, d / "cand.csv")
    stub = StubLinks()
    stats = enrich_links(in_csv, d / "linked.csv", stub)

    check("only the UPRN row is looked up", stub.calls == 1, str(stub.calls))
    check("stats: 1 with_uprn", stats["with_uprn"] == 1, str(stats))
    check("stats: usrn + toid found", stats["usrn_found"] == 1 and stats["toid_found"] == 1, str(stats))

    out = {r["property_reference"]: r for r in csv.DictReader((d / "linked.csv").open())}
    check("UPRN row got USRN", out["A/1"]["usrn"] == "20900493", out["A/1"]["usrn"])
    check("UPRN row got TOID", out["A/1"]["toid"] == "osgb1000005207182", out["A/1"]["toid"])
    check("no-UPRN row untouched", out["A/2"]["usrn"] == "" and out["A/2"]["toid"] == "")

print(f"\n{'✅ PASS' if failures == 0 else '❌ FAIL'} — {failures} failure(s)")
sys.exit(1 if failures else 0)
