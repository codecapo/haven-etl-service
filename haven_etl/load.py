"""Load enriched property rows into a target council's Postgres (Supabase).

Uses a temp staging table + COPY + upsert so re-runs are idempotent and respect
the `property` UPRN primary key. Each council has its own isolated database; the
connection string is resolved per-council from the environment.
"""

from __future__ import annotations

from pathlib import Path

import duckdb

from .config import target_db_url
from .councils.base import PROPERTY_COLUMNS

# Columns we COPY (data_source is a PG enum; the rest map 1:1 to `property`).
_COPY_COLS = [
    "uprn",
    "usrn",
    "toid",
    "property_reference",
    "address_line1",
    "address_line2",
    "postcode",
    "estate",
    "tenure",
    "latitude",
    "longitude",
    "data_source",
]


def _rows_from_parquet(parquet: Path):
    con = duckdb.connect()
    cols = ", ".join(_COPY_COLS)
    return con.execute(f"SELECT {cols} FROM read_parquet('{parquet.as_posix()}')").fetchall()


def load_to_postgres(
    parquet: str | Path,
    council: str,
    table: str = "property",
    db_url: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Upsert the matched-property Parquet into the council DB.

    dry_run reports the row count and the SQL plan without connecting — useful
    before a DB URL is provisioned.
    """
    parquet = Path(parquet)
    rows = _rows_from_parquet(parquet)
    url = db_url or target_db_url(council)

    if dry_run or not url:
        return {
            "rows": len(rows),
            "table": table,
            "loaded": False,
            "reason": "dry-run" if dry_run else f"no DB url for council '{council}' (set HAVEN_DB_URL__{council.upper()})",
        }

    import psycopg  # imported lazily so dry-runs don't require the driver

    col_list = ", ".join(_COPY_COLS)
    # NOT NULL columns on `property`: property_reference, address_line1, postcode.
    set_list = ", ".join(f"{c} = excluded.{c}" for c in _COPY_COLS if c != "uprn")
    with psycopg.connect(url) as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            create temp table _stage (
              uprn text, usrn text, toid text, property_reference text, address_line1 text,
              address_line2 text, postcode text, estate text, tenure text,
              latitude double precision, longitude double precision, data_source text
            ) on commit drop;
            """
        )
        with cur.copy(f"copy _stage ({col_list}) from stdin") as copy:
            for r in rows:
                copy.write_row(r)
        cur.execute(
            f"""
            insert into {table} ({col_list}, master_version)
            select uprn, usrn, toid, property_reference, address_line1, address_line2,
                   coalesce(postcode, '') as postcode, estate, tenure,
                   latitude, longitude, coalesce(data_source, 'import')::data_source, 1
            from _stage
            where uprn is not null and uprn <> ''
            on conflict (uprn) do update set {set_list};
            """
        )
        conn.commit()
        affected = cur.rowcount
    return {"rows": len(rows), "table": table, "loaded": True, "affected": affected}
