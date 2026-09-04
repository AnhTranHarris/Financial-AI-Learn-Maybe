from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from hashlib import sha256
import json
from math import isfinite
import os
from pathlib import Path
import subprocess
from typing import Any, Callable, Iterable, Mapping, Sequence

from .features import FeatureBar
from .provider_registry import ProviderHealth, ProviderRegistry, ProviderSnapshot


PROTOCOL = "dusty-isolated-forecast-provider-v1"
CHRONOS2_PROVIDER_ID = "chronos2"
TARGET = "completed_close_after_horizon_observations"
QUANTILE_LEVELS = (0.1, 0.5, 0.9)
MIN_CONTEXT_OBSERVATIONS = 32
MAX_CONTEXT_OBSERVATIONS = 2048
MAX_HORIZON_STEPS = 64
DEFAULT_TIMEOUT_SECONDS = 180


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)


def payload_sha256(payload: object) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


class ProviderForecastStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ForecastEvidence:
    protocol: str
    provider_id: str
    model_id: str
    model_revision: str
    provider_version: str
    license_id: str
    symbol: str
    timeframe: str
    as_of: datetime
    origin_at: datetime
    horizon_steps: int
    origin_value: float
    p10: float
    p50: float
    p90: float
    context_sha256: str
    request_sha256: str
    response_sha256: str
    target: str = TARGET
    broker_write_authority: bool = False
    promotion_authority: bool = False
    entry_veto_authority: bool = False

    def __post_init__(self) -> None:
        if self.protocol != PROTOCOL:
            raise ValueError("forecast_evidence_protocol_mismatch")
        if not all(
            value.strip()
            for value in (
                self.provider_id,
                self.model_id,
                self.model_revision,
                self.provider_version,
                self.license_id,
                self.symbol,
                self.timeframe,
                self.target,
            )
        ):
            raise ValueError("forecast_evidence_identity_incomplete")
        if self.as_of.tzinfo is None or self.as_of.utcoffset() is None:
            raise ValueError("forecast_evidence_as_of_must_be_timezone_aware")
        if self.origin_at.tzinfo is None or self.origin_at.utcoffset() is None:
            raise ValueError("forecast_evidence_origin_must_be_timezone_aware")
        if self.origin_at > self.as_of:
            raise ValueError("forecast_evidence_origin_after_as_of")
        if type(self.horizon_steps) is not int or not 1 <= self.horizon_steps <= MAX_HORIZON_STEPS:
            raise ValueError("forecast_evidence_horizon_out_of_bounds")
        values = (self.origin_value, self.p10, self.p50, self.p90)
        if any(isinstance(value, bool) or not isfinite(value) or value <= 0 for value in values):
            raise ValueError("forecast_evidence_prices_must_be_finite_positive")
        if not self.p10 <= self.p50 <= self.p90:
            raise ValueError("forecast_evidence_quantiles_cross")
        for digest in (self.context_sha256, self.request_sha256, self.response_sha256):
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest.lower()):
                raise ValueError("forecast_evidence_requires_sha256_identity")
        if self.broker_write_authority or self.promotion_authority or self.entry_veto_authority:
            raise ValueError("forecast_evidence_cannot_receive_operational_authority")

    @property
    def predicted_return_p50(self) -> float:
        return self.p50 / self.origin_value - 1.0

    @property
    def fingerprint(self) -> str:
        return payload_sha256(self.as_dict())

    def as_dict(self) -> dict[str, object]:
        return {
            "protocol": self.protocol,
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "model_revision": self.model_revision,
            "provider_version": self.provider_version,
            "license_id": self.license_id,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": self.as_of.isoformat(),
            "origin_at": self.origin_at.isoformat(),
            "horizon_steps": self.horizon_steps,
            "origin_value": self.origin_value,
            "p10": self.p10,
            "p50": self.p50,
            "p90": self.p90,
            "predicted_return_p50": self.predicted_return_p50,
            "context_sha256": self.context_sha256,
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
            "target": self.target,
            "broker_write_authority": self.broker_write_authority,
            "promotion_authority": self.promotion_authority,
            "entry_veto_authority": self.entry_veto_authority,
        }


@dataclass(frozen=True, slots=True)
class ProviderForecastResult:
    provider_id: str
    status: ProviderForecastStatus
    evidence: ForecastEvidence | None = None
    error: str = ""

    def __post_init__(self) -> None:
        if not self.provider_id.strip():
            raise ValueError("provider_forecast_result_requires_provider")
        if self.status is ProviderForecastStatus.AVAILABLE:
            if self.evidence is None or self.error:
                raise ValueError("available_provider_forecast_requires_evidence_only")
        elif self.evidence is not None or not self.error:
            raise ValueError("unavailable_provider_forecast_requires_error_only")

    @property
    def available(self) -> bool:
        return self.status is ProviderForecastStatus.AVAILABLE

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.provider_id,
            "status": self.status.value,
            "evidence": None if self.evidence is None else self.evidence.as_dict(),
            "error": self.error,
        }


def _rows(bars: Iterable[FeatureBar]) -> tuple[FeatureBar, ...]:
    rows = tuple(bars)
    if not MIN_CONTEXT_OBSERVATIONS <= len(rows) <= MAX_CONTEXT_OBSERVATIONS:
        raise ValueError(
            f"forecast_context_requires_{MIN_CONTEXT_OBSERVATIONS}_to_{MAX_CONTEXT_OBSERVATIONS}_observations"
        )
    if any(left.at >= right.at for left, right in zip(rows, rows[1:])):
        raise ValueError("forecast_context_must_be_strictly_chronological")
    return rows


def build_chronos2_request(
    bars: Iterable[FeatureBar],
    *,
    symbol: str,
    timeframe: str,
    horizon_steps: int,
    snapshot: ProviderSnapshot,
) -> dict[str, object]:
    if snapshot.spec.provider_id != CHRONOS2_PROVIDER_ID:
        raise ValueError("chronos2_request_requires_chronos2_snapshot")
    if not symbol.strip() or not timeframe.strip():
        raise ValueError("forecast_request_requires_symbol_and_timeframe")
    if type(horizon_steps) is not int or not 1 <= horizon_steps <= MAX_HORIZON_STEPS:
        raise ValueError("forecast_request_horizon_out_of_bounds")
    if snapshot.spec.model_revision is None or snapshot.spec.runtime_version is None:
        raise ValueError("chronos2_provider_identity_is_not_pinned")
    rows = _rows(bars)
    context = tuple({"at": bar.at.isoformat(), "close": bar.close} for bar in rows)
    context_sha = payload_sha256(context)
    return {
        "protocol": PROTOCOL,
        "provider_id": snapshot.spec.provider_id,
        "model_id": snapshot.spec.model_id,
        "model_revision": snapshot.spec.model_revision,
        "runtime_version": snapshot.spec.runtime_version,
        "symbol": symbol.strip().upper(),
        "timeframe": timeframe.strip().upper(),
        "as_of": rows[-1].at.isoformat(),
        "horizon_steps": horizon_steps,
        "target": TARGET,
        "quantile_levels": list(QUANTILE_LEVELS),
        "context_sha256": context_sha,
        "context": list(context),
    }


def _child_environment(source: Mapping[str, str] | None = None) -> dict[str, str]:
    source = os.environ if source is None else source
    allowed = (
        "SystemRoot",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LOCALAPPDATA",
        "HF_HOME",
        "HUGGINGFACE_HUB_CACHE",
        "TRANSFORMERS_CACHE",
    )
    environment = {key: source[key] for key in allowed if key in source}
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "HF_HUB_OFFLINE": "1",
            "TRANSFORMERS_OFFLINE": "1",
            "CUDA_VISIBLE_DEVICES": "",
        }
    )
    return environment


def _bounded_error(value: str | None, *, limit: int = 1000) -> str:
    rendered = " ".join((value or "").strip().split())
    return rendered[:limit] if rendered else "no_provider_error_text"


class Chronos2ForecastAdapter:
    """Runs Chronos-2 in its own Python process and returns evidence only.

    The child process receives only timestamps and completed close prices. It is
    launched with a small environment allow-list, Hugging Face offline mode and
    CPU-only visibility. Provider faults are converted to UNAVAILABLE rather
    than exceptions that can disable Dusty's deterministic lane.
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        *,
        worker_path: Path | None = None,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        if type(timeout_seconds) is not int or not 5 <= timeout_seconds <= 600:
            raise ValueError("provider_timeout_must_be_5_to_600_seconds")
        self.registry = registry
        self.worker_path = worker_path or Path(__file__).with_name("provider_worker_chronos2.py")
        self.timeout_seconds = timeout_seconds
        self._runner = runner

    def forecast(
        self,
        bars: Sequence[FeatureBar],
        *,
        symbol: str,
        timeframe: str,
        horizon_steps: int,
    ) -> ProviderForecastResult:
        snapshot = self.registry.snapshot(CHRONOS2_PROVIDER_ID)
        if snapshot.health is not ProviderHealth.INSTALLED:
            return self._unavailable(f"provider_not_installed:{snapshot.health.value}")
        if not self.worker_path.is_file():
            return self._unavailable("provider_worker_missing")
        request = build_chronos2_request(
            bars,
            symbol=symbol,
            timeframe=timeframe,
            horizon_steps=horizon_steps,
            snapshot=snapshot,
        )
        request_text = canonical_json(request)
        request_sha = sha256(request_text.encode("utf-8")).hexdigest()
        try:
            completed = self._runner(
                [str(snapshot.python_executable), str(self.worker_path)],
                input=request_text,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                env=_child_environment(),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return self._unavailable(f"provider_timeout:{self.timeout_seconds}s")
        except OSError as exc:
            return self._unavailable(f"provider_launch_failed:{type(exc).__name__}:{exc}")
        if completed.returncode != 0:
            return self._unavailable(f"provider_process_failed:{_bounded_error(completed.stderr)}")
        response_text = (completed.stdout or "").strip()
        if not response_text:
            return self._unavailable("provider_empty_response")
        try:
            response = json.loads(response_text)
        except json.JSONDecodeError as exc:
            return self._unavailable(f"provider_response_not_json:{exc.msg}")
        try:
            evidence = self._parse_response(snapshot, request, request_sha, response, response_text)
        except (KeyError, TypeError, ValueError) as exc:
            return self._unavailable(f"provider_response_invalid:{type(exc).__name__}:{exc}")
        return ProviderForecastResult(
            provider_id=CHRONOS2_PROVIDER_ID,
            status=ProviderForecastStatus.AVAILABLE,
            evidence=evidence,
        )

    def _parse_response(
        self,
        snapshot: ProviderSnapshot,
        request: dict[str, object],
        request_sha: str,
        response: Any,
        response_text: str,
    ) -> ForecastEvidence:
        if not isinstance(response, dict):
            raise TypeError("response_must_be_object")
        expected_identity = {
            "protocol": PROTOCOL,
            "provider_id": snapshot.spec.provider_id,
            "model_id": snapshot.spec.model_id,
            "model_revision": snapshot.spec.model_revision,
            "provider_version": snapshot.spec.runtime_version,
            "request_sha256": request_sha,
            "context_sha256": request["context_sha256"],
            "as_of": request["as_of"],
            "horizon_steps": request["horizon_steps"],
            "target": TARGET,
        }
        for key, expected in expected_identity.items():
            if response.get(key) != expected:
                raise ValueError(f"response_identity_mismatch:{key}")
        origin = response.get("origin_value")
        quantiles = response.get("quantiles")
        if not isinstance(quantiles, dict):
            raise TypeError("response_quantiles_must_be_object")
        p10, p50, p90 = (quantiles.get(key) for key in ("p10", "p50", "p90"))
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in (origin, p10, p50, p90)
        ):
            raise TypeError("response_prices_must_be_numbers")
        context = request["context"]
        if not isinstance(context, list) or not context:
            raise TypeError("request_context_missing")
        origin_at = datetime.fromisoformat(str(context[-1]["at"]))
        as_of = datetime.fromisoformat(str(request["as_of"]))
        return ForecastEvidence(
            protocol=PROTOCOL,
            provider_id=snapshot.spec.provider_id,
            model_id=snapshot.spec.model_id,
            model_revision=str(snapshot.spec.model_revision),
            provider_version=str(snapshot.spec.runtime_version),
            license_id=snapshot.spec.license_id,
            symbol=str(request["symbol"]),
            timeframe=str(request["timeframe"]),
            as_of=as_of,
            origin_at=origin_at,
            horizon_steps=int(request["horizon_steps"]),
            origin_value=float(origin),
            p10=float(p10),
            p50=float(p50),
            p90=float(p90),
            context_sha256=str(request["context_sha256"]),
            request_sha256=request_sha,
            response_sha256=sha256(response_text.encode("utf-8")).hexdigest(),
        )

    @staticmethod
    def _unavailable(error: str) -> ProviderForecastResult:
        return ProviderForecastResult(
            provider_id=CHRONOS2_PROVIDER_ID,
            status=ProviderForecastStatus.UNAVAILABLE,
            error=error,
        )


def _smoke_bars() -> tuple[FeatureBar, ...]:
    start = datetime(2026, 1, 5, 0, 15, tzinfo=timezone.utc)
    rows = []
    for index in range(96):
        close = 1.10000 + index * 0.00001 + (index % 7) * 0.000001
        rows.append(
            FeatureBar(
                at=start + timedelta(minutes=15 * index),
                open=close,
                high=close * 1.0001,
                low=close * 0.9999,
                close=close,
            )
        )
    return tuple(rows)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dusty isolated Chronos-2 research smoke test")
    parser.add_argument("--provider-root", type=Path, help="root containing Chronos2/.venv")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--smoke-test", action="store_true", help="run one synthetic no-broker forecast")
    args = parser.parse_args(argv)
    if not args.smoke_test:
        parser.error("M112 exposes only --smoke-test from the command line")
    result = Chronos2ForecastAdapter(
        ProviderRegistry(args.provider_root),
        timeout_seconds=args.timeout,
    ).forecast(
        _smoke_bars(),
        symbol="EURUSD",
        timeframe="M15",
        horizon_steps=16,
    )
    print(canonical_json(result.as_dict()))
    return 0 if result.available else 2


if __name__ == "__main__":
    raise SystemExit(main())
