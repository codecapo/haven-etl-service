# haven-etl-service

Central data pipeline for the Haven platform. Each council runs its **own
isolated Supabase backend**; this service (vendor-side) ingests reference + stock
data and loads it into each council's database. It never co-mingles council data.

## What it does

1. **OS Open UPRN → Parquet** — the national UPRN→coordinate table (~41.5M rows)
   is held once as Parquet and refreshed ~every 6 weeks. Used to enrich council
   stock with coordinates. Never copied into a council DB.
2. **Council stock mappers** — per-council parsers that normalize a messy export
   and **explode blocks into individual properties** (e.g. `1-160 Southfleet` →
   160 units).
3. **Enrich** — DuckDB joins exploded stock → OS UPRN to fill coordinates.
4. **Load** — upsert the matched properties into that council's Postgres.

Rows **without a UPRN** can't be loaded yet (the `property` table is keyed by
UPRN); they're parked in `<council>_unmatched.parquet` for the future
address→UPRN matching phase (needs OS AddressBase / a geocoder).

## Setup

> **Use Python 3.12.** Homebrew's Python 3.14 currently ships a broken
> `pyexpat` (libexpat symbol mismatch) that breaks `pip` and `openpyxl`.

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # then fill in per-council DB URLs
```

## Usage

```bash
# 1) Build the national OS Open UPRN reference (full run; minutes).
python -m haven_etl os-uprn --zip /path/osopenuprn_202605_csv.zip
#    Fast test slice:
python -m haven_etl os-uprn --zip /path/osopenuprn_202605_csv.zip --sample 500000

# 2) Ingest a council's stock (parse → explode → enrich). Add --load to write to PG.
python -m haven_etl ingest --council camden --file "/path/Camden_*.xlsx"
python -m haven_etl ingest --council camden --file "/path/Camden_*.xlsx" --load --dry-run

# Inspect a Parquet artifact.
python -m haven_etl stats --parquet data/os_uprn.parquet
```

## Known data-quality notes

* **Range vs unit count.** When a block address has door-number ranges
  (`1-61 & 95-117`), those are treated as authoritative and may differ from the
  `Units` total (e.g. 84 door numbers vs 78 units) due to numbering gaps. Ranges
  win because they yield real, knockable addresses.
* **No-UPRN councils.** Stock without UPRNs (e.g. the Camden sample) cannot be
  loaded into `property` (UPRN is the PK) and lands in `<council>_unmatched.parquet`
  pending address→UPRN matching.

## Deploy (Fly.io, London / `lhr`)

UK data residency, co-located with the council Supabase backends (AWS
`eu-west-2`). It runs as a **worker** — no public HTTP — and jobs are Machines
that run to completion.

One-time setup (already done for `haven-etl-service` in the `personal` org):

```bash
fly apps create haven-etl-service -o personal
fly volumes create haven_data --region lhr --size 10        # holds the OS UPRN Parquet
fly secrets set HAVEN_DB_URL__CAMDEN="postgresql://...@...eu-west-2.pooler.supabase.com:5432/postgres"
```

Jobs run as one-off Machines that build from the Dockerfile, mount the volume,
run the CLI, and exit (no idle machine). Args go straight to the CLI:

```bash
# Deployment health check (self-contained, no data needed) — verified green in lhr:
fly machine run . selftest --dockerfile Dockerfile -r lhr -v haven_data:/data --vm-memory 2048 -a haven-etl-service

# Real jobs (put the source file on /data first, see below):
fly machine run . os-uprn --zip /data/osopenuprn.zip --dockerfile Dockerfile -r lhr -v haven_data:/data --vm-memory 2048 -a haven-etl-service
fly machine run . ingest --council camden --file /data/camden.xlsx --load --dockerfile Dockerfile -r lhr -v haven_data:/data -a haven-etl-service
```

Output goes to `fly logs -a haven-etl-service`. Add `--rm` (interactive terminals
only) to auto-destroy the machine, or `fly machine destroy <id> --force` after.

Get source files onto the `/data` volume via a temporary machine + `fly sftp`,
or have the job fetch them. **6-weekly OS refresh:** a weekly scheduled Machine
with a date guard, or trigger from the PlatOps dashboard / external cron.

## Adding a council

Create `haven_etl/councils/<name>.py` exposing `parse(path) -> list[PropertyRow]`
(reuse `explode_block` from `councils/base.py`). The pipeline auto-discovers it
by name.

## Layout

```
haven_etl/
  os_uprn.py        OS Open UPRN zip → Parquet (DuckDB + fsspec, streamed)
  councils/base.py  PropertyRow + block explosion
  councils/camden.py Camden stock mapper
  enrich.py         join stock → OS UPRN coords; split matched/unmatched
  load.py           upsert into a council's Postgres (temp stage + COPY + upsert)
  pipeline.py       orchestration
  cli.py            `python -m haven_etl ...`
```
