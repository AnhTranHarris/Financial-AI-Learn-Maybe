from __future__ import annotations

import base64
import csv
import io
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


class AcquisitionMode(StrEnum):
    DIRECT_PUBLIC_API = "direct_public_api"
    MT5_TERMINAL = "mt5_terminal"
    AUTHORIZED_ADAPTER = "authorized_adapter"
    USER_PROVIDED = "user_provided"
    UNSUPPORTED_AUTOMATIC = "unsupported_automatic"


@dataclass(frozen=True, slots=True)
class SourceCapability:
    source_id: str
    mode: AcquisitionMode
    automatic: bool
    reason: str


_CAPABILITIES: dict[str, SourceCapability] = {
    "mt5_history": SourceCapability("mt5_history", AcquisitionMode.MT5_TERMINAL, True, "official terminal history API"),
    "bls": SourceCapability("bls", AcquisitionMode.DIRECT_PUBLIC_API, True, "official BLS Public Data API"),
    "ecb": SourceCapability("ecb", AcquisitionMode.DIRECT_PUBLIC_API, True, "official ECB Data Portal API"),
    "sec_edgar": SourceCapability("sec_edgar", AcquisitionMode.DIRECT_PUBLIC_API, True, "official SEC public EDGAR data API"),
    "cftc_cot": SourceCapability("cftc_cot", AcquisitionMode.DIRECT_PUBLIC_API, True, "official CFTC public reporting API"),
    "github_known_repo": SourceCapability("github_known_repo", AcquisitionMode.DIRECT_PUBLIC_API, True, "known public repository content only"),
    "tradingview": SourceCapability("tradingview", AcquisitionMode.UNSUPPORTED_AUTOMATIC, False, "no official public data/indicator API; use user-authorized or supplied material"),
    "forex_factory": SourceCapability("forex_factory", AcquisitionMode.AUTHORIZED_ADAPTER, False, "automatic acquisition requires a verified lawful machine interface"),
    "myfxbook": SourceCapability("myfxbook", AcquisitionMode.AUTHORIZED_ADAPTER, False, "automatic acquisition requires an authorized API/session and must not scrape public rankings blindly"),
    "quantpedia": SourceCapability("quantpedia", AcquisitionMode.USER_PROVIDED, False, "licensed research should enter only through user-authorized material"),
}


def source_capability(source_id: str) -> SourceCapability:
    key = source_id.strip().lower()
    if key not in _CAPABILITIES:
        return SourceCapability(key or "unknown", AcquisitionMode.UNSUPPORTED_AUTOMATIC, False, "no verified acquisition contract")
    return _CAPABILITIES[key]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    base_delay_seconds: float = 0.25
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if self.attempts < 1 or self.base_delay_seconds < 0 or self.timeout_seconds <= 0:
            raise ValueError("invalid retry policy")


FetchBytes = Callable[[Request, float], bytes]


def _urllib_fetch(request: Request, timeout: float) -> bytes:
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - URLs are adapter-controlled HTTPS endpoints.
        return response.read()


class HTTPSClient:
    """Small dependency-free HTTPS client with bounded retries and injectable I/O for tests."""

    def __init__(
        self,
        *,
        fetch_bytes: FetchBytes = _urllib_fetch,
        sleeper: Callable[[float], None] = time.sleep,
        policy: RetryPolicy = RetryPolicy(),
        user_agent: str = "DustyDragon/0.1 research-contact-required",
    ) -> None:
        self._fetch = fetch_bytes
        self._sleep = sleeper
        self.policy = policy
        self.user_agent = user_agent

    def get_bytes(self, url: str, *, headers: Mapping[str, str] | None = None) -> bytes:
        parsed = urlparse(url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("automatic acquisition requires an absolute HTTPS URL")
        merged = {"User-Agent": self.user_agent, "Accept": "*/*"}
        merged.update(headers or {})
        request = Request(url, headers=merged, method="GET")
        last_error: BaseException | None = None
        for attempt in range(self.policy.attempts):
            try:
                return self._fetch(request, self.policy.timeout_seconds)
            except (HTTPError, URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 < self.policy.attempts and self.policy.base_delay_seconds:
                    self._sleep(self.policy.base_delay_seconds * (2**attempt))
        raise RuntimeError(f"bounded acquisition failed after {self.policy.attempts} attempts") from last_error

    def get_json(self, url: str, *, headers: Mapping[str, str] | None = None) -> object:
        return json.loads(self.get_bytes(url, headers=headers).decode("utf-8"))

    def get_text(self, url: str, *, headers: Mapping[str, str] | None = None) -> str:
        return self.get_bytes(url, headers=headers).decode("utf-8")


@dataclass(frozen=True, slots=True)
class MacroObservation:
    source_id: str
    series_id: str
    period: str
    value: float
    known_at: datetime
    effective_at: datetime | None = None
    unit: str = ""

    def __post_init__(self) -> None:
        if not self.source_id or not self.series_id or not self.period:
            raise ValueError("macro observation requires source, series, and period")
        if not math.isfinite(self.value):
            raise ValueError("macro observation value must be finite")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("known_at must be timezone-aware")
        if self.effective_at is not None and (self.effective_at.tzinfo is None or self.effective_at.utcoffset() is None):
            raise ValueError("effective_at must be timezone-aware")


class BLSAdapter:
    base_url = "https://api.bls.gov/publicAPI/v2/timeseries/data"

    def __init__(self, client: HTTPSClient) -> None:
        self.client = client

    def fetch_series(self, series_id: str, *, retrieved_at: datetime | None = None) -> tuple[MacroObservation, ...]:
        series = series_id.strip().upper()
        if not series:
            raise ValueError("BLS series id is required")
        payload = self.client.get_json(f"{self.base_url}/{series}")
        when = retrieved_at or datetime.now(timezone.utc)
        if not isinstance(payload, dict) or str(payload.get("status", "")).upper() != "REQUEST_SUCCEEDED":
            raise RuntimeError("BLS response did not report success")
        rows = payload.get("Results", {}).get("series", [])
        if not rows:
            return ()
        data = rows[0].get("data", [])
        observations = []
        for row in data:
            period = f"{row.get('year', '')}-{row.get('period', '')}"
            observations.append(MacroObservation("bls", series, period, float(row["value"]), when))
        return tuple(observations)


class ECBAdapter:
    base_url = "https://data-api.ecb.europa.eu/service/data"

    def __init__(self, client: HTTPSClient) -> None:
        self.client = client

    def fetch_series(
        self,
        flow_ref: str,
        key: str,
        *,
        start_period: str | None = None,
        end_period: str | None = None,
        retrieved_at: datetime | None = None,
    ) -> tuple[MacroObservation, ...]:
        flow = flow_ref.strip()
        series_key = key.strip()
        if not flow or not series_key:
            raise ValueError("ECB flow and key are required")
        params = {"format": "csvdata"}
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period
        url = f"{self.base_url}/{flow}/{series_key}?{urlencode(params)}"
        text = self.client.get_text(url, headers={"Accept": "text/csv"})
        when = retrieved_at or datetime.now(timezone.utc)
        rows = csv.DictReader(io.StringIO(text))
        result = []
        for row in rows:
            period = (row.get("TIME_PERIOD") or "").strip()
            value = (row.get("OBS_VALUE") or "").strip()
            if not period or not value:
                continue
            result.append(MacroObservation("ecb", f"{flow}:{series_key}", period, float(value), when, unit=(row.get("UNIT") or "")))
        return tuple(result)


@dataclass(frozen=True, slots=True)
class FilingEvent:
    cik: str
    accession_number: str
    form: str
    filing_date: str
    primary_document: str
    known_at: datetime


class SECSubmissionsAdapter:
    base_url = "https://data.sec.gov/submissions"

    def __init__(self, client: HTTPSClient) -> None:
        self.client = client

    def fetch_recent(self, cik: str, *, retrieved_at: datetime | None = None) -> tuple[FilingEvent, ...]:
        digits = "".join(ch for ch in cik if ch.isdigit())
        if not digits:
            raise ValueError("CIK is required")
        normalized = digits.zfill(10)
        payload = self.client.get_json(f"{self.base_url}/CIK{normalized}.json", headers={"Accept": "application/json"})
        if not isinstance(payload, dict):
            raise RuntimeError("SEC submissions response must be an object")
        recent = payload.get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        filing_dates = recent.get("filingDate", [])
        docs = recent.get("primaryDocument", [])
        accepted = recent.get("acceptanceDateTime", [])
        fallback = retrieved_at or datetime.now(timezone.utc)
        result = []
        for index, accession in enumerate(accessions):
            known = fallback
            if index < len(accepted) and accepted[index]:
                raw = str(accepted[index]).replace("Z", "+00:00")
                try:
                    parsed = datetime.fromisoformat(raw)
                    known = parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
                except ValueError:
                    known = fallback
            result.append(FilingEvent(normalized, str(accession), str(forms[index]) if index < len(forms) else "", str(filing_dates[index]) if index < len(filing_dates) else "", str(docs[index]) if index < len(docs) else "", known))
        return tuple(result)


class CFTCPublicAdapter:
    base_url = "https://publicreporting.cftc.gov/resource"

    def __init__(self, client: HTTPSClient) -> None:
        self.client = client

    def fetch_rows(self, dataset_id: str, *, limit: int = 100, where: str = "") -> tuple[dict[str, object], ...]:
        dataset = dataset_id.strip()
        if not dataset or limit < 1 or limit > 50_000:
            raise ValueError("invalid CFTC dataset request")
        params: dict[str, str] = {"$limit": str(limit)}
        if where.strip():
            params["$where"] = where.strip()
        payload = self.client.get_json(f"{self.base_url}/{dataset}.json?{urlencode(params)}", headers={"Accept": "application/json"})
        if not isinstance(payload, list) or any(not isinstance(row, dict) for row in payload):
            raise RuntimeError("CFTC public reporting response must be a list of objects")
        return tuple(dict(row) for row in payload)


@dataclass(frozen=True, slots=True)
class PublicRepositoryFile:
    repository: str
    path: str
    ref: str
    text: str
    source_url: str


class GitHubKnownRepositoryAdapter:
    """Fetch a known public repository file; discovery/ranking remains a separate bounded process."""

    base_url = "https://api.github.com/repos"

    def __init__(self, client: HTTPSClient) -> None:
        self.client = client

    def fetch_text(self, repository: str, path: str, *, ref: str = "main") -> PublicRepositoryFile:
        repo = repository.strip().strip("/")
        clean_path = path.strip().lstrip("/")
        clean_ref = ref.strip()
        if repo.count("/") != 1 or not clean_path or not clean_ref:
            raise ValueError("known GitHub repository, path, and ref are required")
        url = f"{self.base_url}/{repo}/contents/{clean_path}?{urlencode({'ref': clean_ref})}"
        payload = self.client.get_json(url, headers={"Accept": "application/vnd.github+json"})
        if not isinstance(payload, dict) or payload.get("type") != "file":
            raise RuntimeError("GitHub content response is not a file")
        encoding = str(payload.get("encoding", ""))
        content = str(payload.get("content", ""))
        if encoding != "base64" or not content:
            raise RuntimeError("GitHub file content must be base64 encoded")
        text = base64.b64decode(content).decode("utf-8")
        return PublicRepositoryFile(repo, clean_path, clean_ref, text, str(payload.get("html_url", "")))
