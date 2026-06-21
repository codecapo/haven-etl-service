"""Command-line entry point: `python -m haven_etl <command>`."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _print(obj) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="haven_etl", description="Haven central ETL")
    sub = p.add_subparsers(dest="cmd", required=True)

    # os-uprn: build the national OS Open UPRN Parquet reference.
    a = sub.add_parser("os-uprn", help="Ingest OS Open UPRN zip → Parquet reference")
    a.add_argument("--zip", required=True, help="Path to osopenuprn_*.zip")
    a.add_argument("--out", help="Output Parquet path (default: data/os_uprn.parquet)")
    a.add_argument("--sample", type=int, help="Only ingest N rows (fast test run)")

    # stats: inspect a Parquet.
    s = sub.add_parser("stats", help="Row count + sample of a Parquet")
    s.add_argument("--parquet", required=True)

    # selftest: self-contained deployment health check (no external files).
    sub.add_parser("selftest", help="Run a self-contained pipeline health check")

    # ingest: run a council pipeline (parse → explode → enrich [→ load]).
    g = sub.add_parser("ingest", help="Ingest a council stock file")
    g.add_argument("--council", required=True, help="e.g. camden")
    g.add_argument("--file", required=True, help="Path to the council stock export")
    g.add_argument("--out-dir", help="Where to write artifacts (default: ./data)")
    g.add_argument("--match", action="store_true", help="Match no-UPRN rows to UPRNs via the OS Places API")
    g.add_argument("--min-score", type=float, help="Min OS match score to accept (default 0.4)")
    g.add_argument("--match-postcode-only", action="store_true", help="Cost control: only call OS for rows that have a postcode")
    g.add_argument("--match-limit", type=int, help="Cost control: cap the number of paid OS Places calls")
    g.add_argument("--links", action="store_true", help="Enrich UPRNs with USRN + TOID via OS Linked Identifiers")
    g.add_argument("--load", action="store_true", help="Also load matched rows into the council DB")
    g.add_argument("--table", default="property", help="Target table (default: property)")
    g.add_argument("--dry-run", action="store_true", help="With --load: report only, don't connect")

    args = p.parse_args(argv)

    if args.cmd == "os-uprn":
        from .os_uprn import build_os_uprn_parquet, parquet_stats

        out = build_os_uprn_parquet(args.zip, out_path=args.out, sample=args.sample)
        _print({"parquet": str(out), **parquet_stats(out)})
        return 0

    if args.cmd == "stats":
        from .os_uprn import parquet_stats

        _print({"parquet": args.parquet, **parquet_stats(args.parquet)})
        return 0

    if args.cmd == "selftest":
        from .selftest import run_selftest

        result = run_selftest()
        _print(result)
        return 0 if result["ok"] else 1

    if args.cmd == "ingest":
        from .pipeline import run

        _print(
            run(
                council=args.council,
                file=args.file,
                out_dir=args.out_dir,
                match=args.match,
                min_score=args.min_score,
                match_require_postcode=args.match_postcode_only,
                match_max_calls=args.match_limit,
                links=args.links,
                load=args.load,
                table=args.table,
                dry_run=args.dry_run,
            )
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
