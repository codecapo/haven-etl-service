"""Configuration — paths + per-council database resolution."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("HAVEN_DATA_DIR") or (ROOT / "data")).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)

OS_UPRN_PARQUET = Path(os.getenv("OS_UPRN_PARQUET") or (DATA_DIR / "os_uprn.parquet")).resolve()

# OS Places API (address → UPRN matching). Key from the OS Data Hub.
OS_PLACES_API_KEY = os.getenv("OS_PLACES_API_KEY")
OS_PLACES_MIN_SCORE = float(os.getenv("OS_PLACES_MIN_SCORE") or "0.4")
OS_PLACES_RATE_MS = int(os.getenv("OS_PLACES_RATE_MS") or "120")


def target_db_url(council: str | None = None) -> str | None:
    """Resolve the Postgres URL for a council's isolated backend.

    Prefers a per-council override (HAVEN_DB_URL__CAMDEN), falling back to a
    generic SUPABASE_DB_URL / HAVEN_DB_URL for local testing.
    """
    if council:
        specific = os.getenv(f"HAVEN_DB_URL__{council.upper()}")
        if specific:
            return specific
    return os.getenv("SUPABASE_DB_URL") or os.getenv("HAVEN_DB_URL")
