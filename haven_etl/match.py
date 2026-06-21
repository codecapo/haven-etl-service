"""Address→UPRN matching step.

For council stock that arrives WITHOUT UPRNs (e.g. the Camden sample), fill the
UPRN by matching each address against the OS Places API. A confident match
(score ≥ min_score) sets the UPRN so the row can load into `property`; weaker /
no matches keep an empty UPRN (→ enrich routes them to unmatched) and are written
to a review audit so a human can resolve them.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from pathlib import Path

from .enrich import _CANDIDATE_FIELDS

_AUDIT_FIELDS = ["source_council", "address_line1", "postcode", "result", "uprn", "matched_address", "score"]

# --- local matching within a postcode (no API cost) ------------------------
_TOK = re.compile(r"[A-Za-z0-9]+")
_NOISE = {"FLAT", "FLATS", "THE", "LONDON"}


def _tokens(s: str) -> list[str]:
    return [t.upper() for t in _TOK.findall(s or "")]


def _best_in_postcode(candidate_address: str, records: list[dict]) -> dict | None:
    """Pick the OS record in a postcode that matches the candidate's flat number/
    letter + building/street name. Conservative: every numeric/letter unit token
    must be present, a name must overlap, and ties are rejected as ambiguous."""
    ct = _tokens(candidate_address)
    required = {t for t in ct if t.isdigit() or (len(t) == 1 and t.isalpha())}
    names = {t for t in ct if t.isalpha() and len(t) > 2 and t not in _NOISE}
    best = None
    best_score = -1
    tie = False
    for rec in records:
        ot = set(_tokens(rec.get("ADDRESS", "")))
        if required and not required.issubset(ot):
            continue
        overlap = len(names & ot)
        if names and overlap == 0:
            continue
        score = overlap * 2 + len(required)
        if score > best_score:
            best_score, best, tie = score, rec, False
        elif score == best_score and best and rec.get("UPRN") != best.get("UPRN"):
            tie = True
    return None if (best is None or tie) else best


def match_by_postcode(in_csv, out_csv, matcher, audit_csv=None, max_calls: int | None = None) -> dict:
    """Resolve UPRNs by fetching each distinct postcode once (cheap) and matching
    units locally. Far cheaper + more accurate than per-address /find."""
    in_csv, out_csv = Path(in_csv), Path(out_csv)
    rows = list(csv.DictReader(in_csv.open(encoding="utf-8")))
    groups: dict[str, list[int]] = defaultdict(list)
    stats = {
        "rows": len(rows),
        "already_uprn": 0,
        "no_postcode": 0,
        "postcodes": 0,
        "api_calls": 0,
        "matched": 0,
        "unmatched": 0,
        "skipped_capped": 0,
    }
    for i, r in enumerate(rows):
        if (r.get("uprn") or "").strip():
            stats["already_uprn"] += 1
            continue
        pc = (r.get("postcode") or "").strip().upper()
        if not pc:
            stats["no_postcode"] += 1
            continue
        groups[pc].append(i)
    stats["postcodes"] = len(groups)

    audit: list[dict] = []
    cache = getattr(matcher, "_cache", {})
    for pc, idxs in groups.items():
        cached = f"PC::{pc}" in cache
        if not cached and max_calls is not None and stats["api_calls"] >= max_calls:
            stats["skipped_capped"] += len(idxs)
            continue
        records = matcher.postcode(pc)
        if not cached:
            stats["api_calls"] += 1
        for i in idxs:
            r = rows[i]
            best = _best_in_postcode(r.get("address_line1", ""), records)
            if best:
                r["uprn"] = best["UPRN"]
                if best.get("LAT") is not None:
                    r["latitude"] = best["LAT"]
                if best.get("LNG") is not None:
                    r["longitude"] = best["LNG"]
                stats["matched"] += 1
                audit.append({"source_council": r.get("source_council", ""), "address_line1": r.get("address_line1", ""),
                              "postcode": pc, "result": "matched", "uprn": best["UPRN"],
                              "matched_address": best.get("ADDRESS", ""), "score": ""})
            else:
                stats["unmatched"] += 1
                audit.append({"source_council": r.get("source_council", ""), "address_line1": r.get("address_line1", ""),
                              "postcode": pc, "result": "no_match", "uprn": "", "matched_address": "", "score": ""})

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


def match_candidates(
    in_csv,
    out_csv,
    matcher,
    min_score: float = 0.4,
    audit_csv=None,
    require_postcode: bool = False,
    max_calls: int | None = None,
) -> dict:
    """Fill UPRNs on no-UPRN candidate rows via `matcher`. Returns stats.

    Cost controls (OS Places bills per call): `require_postcode` skips rows with
    no postcode (low match odds), and `max_calls` caps the number of paid calls.
    """
    in_csv, out_csv = Path(in_csv), Path(out_csv)
    rows = list(csv.DictReader(in_csv.open(encoding="utf-8")))
    audit: list[dict] = []
    stats = {
        "rows": len(rows),
        "already_uprn": 0,
        "attempted": 0,
        "matched": 0,
        "low_confidence": 0,
        "no_result": 0,
        "skipped_no_postcode": 0,
        "skipped_capped": 0,
    }

    for r in rows:
        if (r.get("uprn") or "").strip():
            stats["already_uprn"] += 1
            continue
        address = (r.get("address_line1") or "").strip()
        postcode = (r.get("postcode") or "").strip() or None
        if not address:
            stats["no_result"] += 1
            continue
        if require_postcode and not postcode:
            stats["skipped_no_postcode"] += 1
            continue
        if max_calls is not None and stats["attempted"] >= max_calls:
            stats["skipped_capped"] += 1
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
