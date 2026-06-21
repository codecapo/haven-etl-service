"""Haven ETL service — central data pipeline for the per-council Haven backends.

Pieces:
  * os_uprn  — ingest OS Open UPRN (national, 41.5M rows) → Parquet, held ONCE.
  * councils — per-council stock mappers (parse + block explosion → property rows).
  * enrich   — DuckDB join stock → OS UPRN Parquet to fill coordinates.
  * load     — COPY the final property rows into a target council's Postgres.
  * pipeline — orchestrate parse → explode → enrich → load.
"""

__version__ = "0.1.0"
