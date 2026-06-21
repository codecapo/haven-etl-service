"""Orchestrate a council ingest: parse → explode → enrich → load."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from .config import DATA_DIR
from .enrich import write_candidates_csv, enrich
from .load import load_to_postgres


def _mapper(council: str):
    """Resolve a council's stock mapper module (haven_etl.councils.<council>)."""
    try:
        return import_module(f".councils.{council.lower()}", package="haven_etl")
    except ModuleNotFoundError as e:
        raise ValueError(f"No mapper for council '{council}'. Add haven_etl/councils/{council.lower()}.py") from e


def run(
    council: str,
    file: str | Path,
    out_dir: str | Path | None = None,
    load: bool = False,
    table: str = "property",
    dry_run: bool = False,
    match: bool = False,
    min_score: float | None = None,
    matcher=None,
) -> dict:
    out_dir = Path(out_dir) if out_dir else DATA_DIR
    mapper = _mapper(council)

    rows = mapper.parse(file)
    candidates_csv = write_candidates_csv(rows, out_dir / f"{council}_candidates.csv")

    # Address→UPRN matching for no-UPRN stock (OS Places API). Fills UPRNs above
    # the confidence threshold; low/no matches go to <council>_match_audit.csv.
    match_stats = None
    if match:
        from .match import match_candidates
        from .config import OS_PLACES_API_KEY, OS_PLACES_MIN_SCORE, OS_PLACES_RATE_MS

        if matcher is None:
            from .os_places import OsPlacesMatcher

            matcher = OsPlacesMatcher(OS_PLACES_API_KEY, rate_ms=OS_PLACES_RATE_MS)
        matched_csv = out_dir / f"{council}_candidates_matched.csv"
        match_stats = match_candidates(
            candidates_csv,
            matched_csv,
            matcher,
            min_score=min_score if min_score is not None else OS_PLACES_MIN_SCORE,
            audit_csv=out_dir / f"{council}_match_audit.csv",
        )
        candidates_csv = matched_csv

    enriched = enrich(candidates_csv, council=council, out_dir=out_dir)

    result = {
        "council": council,
        "source_file": str(file),
        "parsed_rows": len(rows),
        "candidates_csv": str(candidates_csv),
        **({"match": match_stats} if match_stats is not None else {}),
        **{k: (str(v) if isinstance(v, Path) else v) for k, v in enriched.items()},
    }

    if load:
        result["load"] = load_to_postgres(
            enriched["matched_path"], council=council, table=table, dry_run=dry_run
        )
    return result
