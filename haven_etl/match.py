"""Address→UPRN matching step.

For council stock that arrives WITHOUT UPRNs (e.g. the Camden sample), fill the
UPRN by matching each address against the OS Places API. A confident match
(score ≥ min_score) sets the UPRN so the row can load into `property`; weaker /
no matches keep an empty UPRN (→ enrich routes them to unmatched) and are written
to a review audit so a human can resolve them.
"""

from __future__ import annotations

import csv
from pathlib import Path

from .enrich import _CANDIDATE_FIELDS

_AUDIT_FIELDS = ["source_council", "address_line1", "postcode", "result", "uprn", "matched_address", "score"]


def match_candidates(in_csv, out_csv, matcher, min_score: float = 0.4, audit_csv=None) -> dict:
    """Fill UPRNs on no-UPRN candidate rows via `matcher`. Returns stats."""
    in_csv, out_csv = Path(in_csv), Path(out_csv)
    rows = list(csv.DictReader(in_csv.open(encoding="utf-8")))
    audit: list[dict] = []
    stats = {"rows": len(rows), "already_uprn": 0, "attempted": 0, "matched": 0, "low_confidence": 0, "no_result": 0}

    for r in rows:
        if (r.get("uprn") or "").strip():
            stats["already_uprn"] += 1
            continue
        address = (r.get("address_line1") or "").strip()
        postcode = (r.get("postcode") or "").strip() or None
        if not address:
            stats["no_result"] += 1
            continue

        stats["attempted"] += 1
        m = matcher.match(address, postcode)
        if m and m.score >= min_score:
            r["uprn"] = m.uprn
            if not (r.get("postcode") or "").strip() and m.postcode:
                r["postcode"] = m.postcode
            stats["matched"] += 1
            result = "matched"
        elif m:
            stats["low_confidence"] += 1
            result = "low_confidence"
        else:
            stats["no_result"] += 1
            result = "no_result"
        audit.append(
            {
                "source_council": r.get("source_council", ""),
                "address_line1": address,
                "postcode": postcode or "",
                "result": result,
                "uprn": m.uprn if m else "",
                "matched_address": m.matched_address if m else "",
                "score": f"{m.score:.3f}" if m else "",
            }
        )

    if hasattr(matcher, "flush"):
        matcher.flush()

    out_csv.parent.mkdir(parents=True, exist_ok=True)
    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_CANDIDATE_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _CANDIDATE_FIELDS})

    if audit_csv and audit:
        audit_csv = Path(audit_csv)
        audit_csv.parent.mkdir(parents=True, exist_ok=True)
        with audit_csv.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=_AUDIT_FIELDS)
            w.writeheader()
            w.writerows(audit)

    return stats
