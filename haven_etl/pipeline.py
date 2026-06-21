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
) -> dict:
    out_dir = Path(out_dir) if out_dir else DATA_DIR
    mapper = _mapper(council)

    rows = mapper.parse(file)
    candidates_csv = write_candidates_csv(rows, out_dir / f"{council}_candidates.csv")
    enriched = enrich(candidates_csv, council=council, out_dir=out_dir)

    result = {
        "council": council,
        "source_file": str(file),
        "parsed_rows": len(rows),
        "candidates_csv": str(candidates_csv),
        **{k: (str(v) if isinstance(v, Path) else v) for k, v in enriched.items()},
    }

    if load:
        result["load"] = load_to_postgres(
            enriched["matched_path"], council=council, table=table, dry_run=dry_run
        )
    return result
