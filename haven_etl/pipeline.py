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
    match_require_postcode: bool = False,
    match_max_calls: int | None = None,
    links: bool = False,
    links_matcher=None,
) -> dict:
    out_dir = Path(out_dir) if out_dir else DATA_DIR
    mapper = _mapper(council)

    rows = mapper.parse(file)
    candidates_csv = write_candidates_csv(rows, out_dir / f"{council}_candidates.csv")

    # Address→UPRN matching for no-UPRN stock (OS Places API). Fills UPRNs above
    # the confidence threshold; low/no matches go to <council>_match_audit.csv.
    match_stats = None
    if match:
        from .match import match_by_postcode
        from .config import OS_API_KEY, OS_PLACES_RATE_MS

        if matcher is None:
            from .os_places import OsPlacesMatcher

            matcher = OsPlacesMatcher(OS_API_KEY, rate_ms=OS_PLACES_RATE_MS)
        matched_csv = out_dir / f"{council}_candidates_matched.csv"
        # Postcode-batch matching: one billed call per distinct postcode, then
        # exact local matching by flat number/letter + building name.
        match_stats = match_by_postcode(
            candidates_csv,
            matched_csv,
            matcher,
            audit_csv=out_dir / f"{council}_match_audit.csv",
            max_calls=match_max_calls,  # caps NEW (billed) postcode calls
        )
        candidates_csv = matched_csv

    # OS Linked Identifiers enrichment: fill USRN + TOID for rows with a UPRN.
    links_stats = None
    if links:
        from .links import enrich_links
        from .config import OS_API_KEY, OS_PLACES_RATE_MS

        if links_matcher is None:
            from .os_links import OsLinksMatcher

            links_matcher = OsLinksMatcher(OS_API_KEY, rate_ms=OS_PLACES_RATE_MS)
        linked_csv = out_dir / f"{council}_candidates_linked.csv"
        links_stats = enrich_links(candidates_csv, linked_csv, links_matcher)
        candidates_csv = linked_csv

    enriched = enrich(candidates_csv, council=council, out_dir=out_dir)

    result = {
        "council": council,
        "source_file": str(file),
        "parsed_rows": len(rows),
        "candidates_csv": str(candidates_csv),
        **({"match": match_stats} if match_stats is not None else {}),
        **({"links": links_stats} if links_stats is not None else {}),
        **{k: (str(v) if isinstance(v, Path) else v) for k, v in enriched.items()},
    }

    if load:
        result["load"] = load_to_postgres(
            enriched["matched_path"], council=council, table=table, dry_run=dry_run
        )
    return result
