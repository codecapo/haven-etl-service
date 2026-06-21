"""OS Linked Identifiers enrichment step.

For candidate rows that have a UPRN, fetch the USRN + TOID and fill those columns
so they ride through enrich → load onto the property record. Rows without a UPRN
are left untouched (there's nothing to look up).
"""

from __future__ import annotations

import csv
from pathlib import Path

from .enrich import _CANDIDATE_FIELDS


def enrich_links(in_csv, out_csv, matcher) -> dict:
    """Fill usrn/toid for rows that have a UPRN, via `matcher`. Returns stats."""
    in_csv, out_csv = Path(in_csv), Path(out_csv)
    rows = list(csv.DictReader(in_csv.open(encoding="utf-8")))
    stats = {"rows": len(rows), "with_uprn": 0, "looked_up": 0, "usrn_found": 0, "toid_found": 0}

    for r in rows:
        uprn = (r.get("uprn") or "").strip()
        if not uprn:
            continue
        stats["with_uprn"] += 1
        # Don't re-fetch if both are already present (e.g. council-supplied).
        if (r.get("usrn") or "").strip() and (r.get("toid") or "").strip():
            continue
        ids = matcher.lookup(uprn)
        stats["looked_up"] += 1
        if ids.usrn and not (r.get("usrn") or "").strip():
            r["usrn"] = ids.usrn
            stats["usrn_found"] += 1
        if ids.toid and not (r.get("toid") or "").strip():
            r["toid"] = ids.toid
            stats["toid_found"] += 1

    if hasattr(matcher, "flush"):
        matcher.flush()

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CANDIDATE_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _CANDIDATE_FIELDS})

    return stats
