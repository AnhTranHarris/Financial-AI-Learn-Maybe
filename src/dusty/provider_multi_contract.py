from __future__ import annotations

"""Shared point-in-time contract for optional external forecast contractors."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
import json
from typing import Any, Iterable, Sequence

from .features import FeatureBar
from .provider_forecast_adapter import (
    MAX_HORIZON_STEPS,
    MIN_CONTEXT_OBSERVATIONS,
    MAX_CONTEXT_OBSERVATIONS,
    PROTOCOL,
    TARGET,
    ForecastEvidence,
    ProviderForecastResult,
    ProviderForecastStatus,
    canonical_json,
    payload_sha256,
)
from .provider_registry import ProviderSnapshot


KRONOS_PROVIDER_ID = "kronos-small"
TIMESFM25_PROVIDER_ID = "timesfm-2.5"
CHRONOS_DISTRIBUTION = "native_quantiles"
TIMESFM_DISTRIBUTION = "native_quantiles"
KRONOS_DISTRIBUTION = "empirical_5_seed_paths"


@dataclass(frozen=True, slots=True)
class ForecastProvenance:
    provider_id: str
    distribution_method: str
    sample_count: int

    def __post_init__(self) -> None:
        if not self.provider_id.strip() or not self.distribution_method.strip():
            raise ValueError("forecast_provenance_identity_incomplete")
        if type(self.sample_count) is not int or self.sample_count < 1:
            raise ValueError("forecast_provenance_sample_count_invalid")


@dataclass(frozen=True, slots=True)
class ContractorForecastResult:
    result: ProviderForecastResult
    provenance: ForecastProvenance

    @property
    def available(self) -> bool:
        return self.result.available

    def as_dict(self) -> dict[str, object]:
        return {
            "result": self.result.as_dict(),
            "provenance": {
                "provider_id": self.provenance.provider_id,
                "distribution_method": self.provenance.distribution_method,
                "sample_count": self.provenance.sample_count,
            },
        }


def _validated_rows(bars: Iterable[FeatureBar]) -> tuple[FeatureBar, ...]:
    rows = tuple(bars)
    if not MIN_CONTEXT_OBSERVATIONS <= len(rows) <= MAX_CONTEXT_OBSERVATIONS:
        raise ValueError(
            f"forecast_context_requires_{MIN_CONTEXT_OBSERVATIONS}_to_{MAX_CONTEXT_OBSERVATIONS}_observations"
        )
    if any(left.at >= right.at for left, right in zip(rows, rows[1:])):
        raise ValueError("forecast_context_must_be_strictly_chronological")
    return rows


def _base_request(
    rows: Sequence[FeatureBar],
    *,
    symbol: str,
    timeframe: str,
    horizon_steps: int,
    snapshot: ProviderSnapshot,
    context: list[dict[str, object]],
) -> dict[str, object]:
    if not symbol.strip() or not timeframe.strip():
        raise ValueError("forecast_request_requires_symbol_and_timeframe")
    if type(horizon_steps) is not int or not 1 <= horizon_steps <= MAX_HORIZON_STEPS:
        raise ValueError("forecast_request_horizon_out_of_bounds")
    if snapshot.spec.model_revision is None or snapshot.spec.runtime_version is None:
        raise ValueError("forecast_provider_identity_is_not_pinned")
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
        "context_sha256": payload_sha256(tuple(context)),
        "context": context,
    }


def build_timesfm25_request(
    bars: Iterable[FeatureBar],
    *,
    symbol: str,
    timeframe: str,
    horizon_steps: int,
    snapshot: ProviderSnapshot,
) -> dict[str, object]:
    if snapshot.spec.provider_id != TIMESFM25_PROVIDER_ID:
        raise ValueError("timesfm25_request_requires_timesfm25_snapshot")
    rows = _validated_rows(bars)
    context = [{"at": row.at.isoformat(), "close": row.close} for row in rows]
    request = _base_request(
        rows,
        symbol=symbol,
        timeframe=timeframe,
        horizon_steps=horizon_steps,
        snapshot=snapshot,
        context=context,
    )
    request["quantile_levels"] = [0.1, 0.5, 0.9]
    request["distribution_method"] = TIMESFM_DISTRIBUTION
    return request


def build_kronos_request(
    bars: Iterable[FeatureBar],
    *,
    symbol: str,
    timeframe: str,
    horizon_steps: int,
    snapshot: ProviderSnapshot,
    future_times: Sequence[datetime],
) -> dict[str, object]:
    if snapshot.spec.provider_id != KRONOS_PROVIDER_ID:
        raise ValueError("kronos_request_requires_kronos_snapshot")
    rows = _validated_rows(bars)
    if snapshot.spec.tokenizer_id is None or snapshot.spec.tokenizer_revision is None:
        raise ValueError("kronos_tokenizer_identity_is_not_pinned")
    schedule = tuple(future_times)
    if len(schedule) != horizon_steps:
        raise ValueError("kronos_future_schedule_must_match_horizon")
    previous = rows[-1].at
    for at in schedule:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("kronos_future_schedule_must_be_timezone_aware")
        if at <= previous:
            raise ValueError("kronos_future_schedule_must_be_strictly_after_context")
        previous = at
    context = [
        {
            "at": row.at.isoformat(),
            "open": row.open,
            "high": row.high,
            "low": row.low,
            "close": row.close,
            "volume": row.tick_volume,
        }
        for row in rows[-512:]
    ]
    request = _base_request(
        rows[-512:],
        symbol=symbol,
        timeframe=timeframe,
        horizon_steps=horizon_steps,
        snapshot=snapshot,
        context=context,
    )
    request.update(
        {
            "source_revision": snapshot.spec.source_revision,
            "tokenizer_id": snapshot.spec.tokenizer_id,
            "tokenizer_revision": snapshot.spec.tokenizer_revision,
            "future_times": [at.isoformat() for at in schedule],
            "distribution_method": KRONOS_DISTRIBUTION,
            "sample_count": 5,
        }
    )
    return request


def parse_provider_response(
    snapshot: ProviderSnapshot,
    request: dict[str, object],
    response_line: str,
    *,
    distribution_method: str,
    sample_count: int,
) -> ContractorForecastResult:
    response: Any = json.loads(response_line)
    if not isinstance(response, dict):
        raise TypeError("response_must_be_object")
    expected = {
        "protocol": PROTOCOL,
        "provider_id": snapshot.spec.provider_id,
        "model_id": snapshot.spec.model_id,
        "model_revision": snapshot.spec.model_revision,
        "provider_version": snapshot.spec.runtime_version,
        "request_sha256": payload_sha256(request),
        "context_sha256": request["context_sha256"],
        "as_of": request["as_of"],
        "horizon_steps": request["horizon_steps"],
        "target": TARGET,
        "distribution_method": distribution_method,
        "sample_count": sample_count,
    }
    for key, expected_value in expected.items():
        if response.get(key) != expected_value:
            raise ValueError(f"response_identity_mismatch:{key}")
    quantiles = response.get("quantiles")
    if not isinstance(quantiles, dict):
        raise TypeError("response_quantiles_must_be_object")
    origin = response.get("origin_value")
    p10, p50, p90 = (quantiles.get(name) for name in ("p10", "p50", "p90"))
    if any(
        isinstance(value, bool) or not isinstance(value, (int, float))
        for value in (origin, p10, p50, p90)
    ):
        raise TypeError("response_prices_must_be_numbers")
    context = request.get("context")
    if not isinstance(context, list) or not context:
        raise TypeError("request_context_missing")
    evidence = ForecastEvidence(
        protocol=PROTOCOL,
        provider_id=snapshot.spec.provider_id,
        model_id=snapshot.spec.model_id,
        model_revision=str(snapshot.spec.model_revision),
        provider_version=str(snapshot.spec.runtime_version),
        license_id=snapshot.spec.license_id,
        symbol=str(request["symbol"]),
        timeframe=str(request["timeframe"]),
        as_of=datetime.fromisoformat(str(request["as_of"])),
        origin_at=datetime.fromisoformat(str(context[-1]["at"])),
        horizon_steps=int(request["horizon_steps"]),
        origin_value=float(origin),
        p10=float(p10),
        p50=float(p50),
        p90=float(p90),
        context_sha256=str(request["context_sha256"]),
        request_sha256=payload_sha256(request),
        response_sha256=sha256(response_line.encode("utf-8")).hexdigest(),
    )
    return ContractorForecastResult(
        ProviderForecastResult(
            provider_id=snapshot.spec.provider_id,
            status=ProviderForecastStatus.AVAILABLE,
            evidence=evidence,
        ),
        ForecastProvenance(
            provider_id=snapshot.spec.provider_id,
            distribution_method=distribution_method,
            sample_count=sample_count,
        ),
    )


def unavailable_result(
    provider_id: str,
    error: str,
    *,
    distribution_method: str,
    sample_count: int,
) -> ContractorForecastResult:
    return ContractorForecastResult(
        ProviderForecastResult(
            provider_id=provider_id,
            status=ProviderForecastStatus.UNAVAILABLE,
            error=error,
        ),
        ForecastProvenance(
            provider_id=provider_id,
            distribution_method=distribution_method,
            sample_count=sample_count,
        ),
    )
