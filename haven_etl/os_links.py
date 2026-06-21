"""OS Linked Identifiers API — enrich a UPRN with its related identifiers.

Given a UPRN, returns the USRN (the street it sits on) and the OS MasterMap TOID
(the topographic feature / building). Free on the OS Data Hub. Used to enrich
properties that already have a UPRN — it does NOT do address→UPRN (that's
os_places). Stdlib only; disk-cached + rate-limited like os_places.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .config import DATA_DIR

BASE_URL = "https://api.os.uk/search/links/v1/identifierTypes/UPRN"


@dataclass
class LinkedIds:
    usrn: str | None
    toid: str | None


def _corr_value(corr: dict) -> str | None:
    ids = corr.get("correlatedIdentifiers") or []
    return str(ids[0]["identifier"]) if ids and ids[0].get("identifier") else None


class OsLinksMatcher:
    def __init__(
        self,
        api_key: str,
        *,
        cache_path: str | Path | None = None,
        rate_ms: int = 120,
        timeout: int = 15,
        max_retries: int = 3,
    ):
        if not api_key:
            raise ValueError("OS Data Hub API key required (set OS_API_KEY)")
        self.api_key = api_key
        self.rate_s = max(0, rate_ms) / 1000.0
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_path = Path(cache_path) if cache_path else DATA_DIR / "os_links_cache.json"
        self._cache: dict = {}
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text())
            except Exception:
                self._cache = {}
        self._last_call = 0.0
        self._dirty = 0

    def _throttle(self) -> None:
        if self.rate_s:
            wait = self.rate_s - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
        self._last_call = time.monotonic()

    def _fetch(self, uprn: str) -> dict | None:
        params = urllib.parse.urlencode({"key": self.api_key})
        url = f"{BASE_URL}/{urllib.parse.quote(str(uprn))}?{params}"
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None  # no linked identifiers for this UPRN
                if e.code in (429, 500, 502, 503) and attempt < self.max_retries - 1:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
            except urllib.error.URLError:
                if attempt < self.max_retries - 1:
                    time.sleep(1.0 * (attempt + 1))
                    continue
                raise
        return None

    @staticmethod
    def parse(payload: dict | None) -> LinkedIds:
        """Pull the USRN (street) and the building TOID from the correlations.

        A UPRN links to several identifiers; we want the USRN and the
        TopographicArea TOID (the building), preferring it over the RoadLink TOID
        (the road) which is also returned."""
        correlations = (payload or {}).get("correlations") or []
        usrn = None
        toid_building = None
        toid_any = None
        for corr in correlations:
            ctype = corr.get("correlatedIdentifierType")
            if ctype == "USRN" and usrn is None:
                usrn = _corr_value(corr)
            elif ctype == "TOID":
                val = _corr_value(corr)
                if val and toid_any is None:
                    toid_any = val
                if corr.get("correlatedFeatureType") == "TopographicArea" and toid_building is None:
                    toid_building = val
        return LinkedIds(usrn=usrn, toid=toid_building or toid_any)

    def lookup(self, uprn: str) -> LinkedIds:
        uprn = (uprn or "").strip()
        if not uprn:
            return LinkedIds(None, None)
        if uprn in self._cache:
            d = self._cache[uprn]
            return LinkedIds(d.get("usrn"), d.get("toid"))
        ids = self.parse(self._fetch(uprn))
        self._cache[uprn] = {"usrn": ids.usrn, "toid": ids.toid}
        self._dirty += 1
        if self._dirty >= 25:
            self.flush()
        return ids

    def flush(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache))
            self._dirty = 0
        except Exception:
            pass
