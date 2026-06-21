"""OS Places API matcher: address → UPRN (authoritative, flat-aware).

OS Open UPRN has no addresses, so it can't turn "1 Southfleet" into a UPRN. The
OS Places API (OS Data Hub) does exactly that — and because it matches on the
address text it resolves individual flats, which a coordinate snap cannot (a
block stacks many UPRNs on one point).

Requires an OS Data Hub API key (OS_PLACES_API_KEY). Responses are cached on disk
so the same address is never re-billed and rate limits are respected. Uses only
the stdlib (urllib) — no extra dependency.
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

FIND_URL = "https://api.os.uk/search/places/v1/find"


@dataclass
class PlacesMatch:
    uprn: str
    latitude: float | None
    longitude: float | None
    matched_address: str
    postcode: str | None
    score: float  # OS MATCH 0..1 (1 = exact)


def _f(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


class OsPlacesMatcher:
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
            raise ValueError("OS Places API key required (set OS_PLACES_API_KEY)")
        self.api_key = api_key
        self.rate_s = max(0, rate_ms) / 1000.0
        self.timeout = timeout
        self.max_retries = max_retries
        self.cache_path = Path(cache_path) if cache_path else DATA_DIR / "os_places_cache.json"
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

    def _fetch(self, query: str) -> dict | None:
        params = urllib.parse.urlencode(
            {"query": query, "maxresults": 1, "output_srs": "EPSG:4326", "key": self.api_key}
        )
        url = f"{FIND_URL}?{params}"
        for attempt in range(self.max_retries):
            self._throttle()
            try:
                with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                # Back off on rate-limit / transient server errors, then retry.
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
    def parse(payload: dict | None) -> PlacesMatch | None:
        """Pure parser for an OS Places /find response (unit-testable)."""
        for r in (payload or {}).get("results") or []:
            dpa = r.get("DPA") or r.get("LPI")
            if not dpa or not dpa.get("UPRN"):
                continue
            return PlacesMatch(
                uprn=str(dpa["UPRN"]),
                latitude=_f(dpa.get("LAT")),
                longitude=_f(dpa.get("LNG")),
                matched_address=dpa.get("ADDRESS", ""),
                postcode=dpa.get("POSTCODE"),
                score=_f(dpa.get("MATCH")) or 0.0,
            )
        return None

    def match(self, address: str, postcode: str | None = None) -> PlacesMatch | None:
        query = f"{address}, {postcode}" if postcode else (address or "")
        query = query.strip().strip(",").strip()
        if not query:
            return None
        key = query.lower()
        if key in self._cache:
            return self._from_cache(self._cache[key])
        match = self.parse(self._fetch(query))
        self._cache[key] = self._to_cache(match)
        self._dirty += 1
        if self._dirty >= 25:
            self.flush()
        return match

    def flush(self) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache))
            self._dirty = 0
        except Exception:
            pass

    @staticmethod
    def _to_cache(m: PlacesMatch | None) -> dict | None:
        if m is None:
            return None
        return {
            "uprn": m.uprn,
            "lat": m.latitude,
            "lng": m.longitude,
            "addr": m.matched_address,
            "pc": m.postcode,
            "score": m.score,
        }

    @staticmethod
    def _from_cache(d: dict | None) -> PlacesMatch | None:
        if d is None:
            return None
        return PlacesMatch(d["uprn"], d.get("lat"), d.get("lng"), d.get("addr", ""), d.get("pc"), d.get("score", 0.0))
