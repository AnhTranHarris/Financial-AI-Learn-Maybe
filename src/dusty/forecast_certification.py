from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Iterable

from .forecast_demo import ForecastDemoCertification
from .trust_review import ProofLevel


REQUIRED_FORECAST_MILESTONES = tuple(f"M{number}" for number in range(86, 95))


@dataclass(frozen=True, slots=True)
class ForecastMilestoneEvidence:
    milestone: str
    passed: bool
    artifact_hash: str
    data_hash: str
    config_hash: str
    test_hash: str
    commit_sha: str

    def __post_init__(self) -> None:
        if self.milestone not in REQUIRED_FORECAST_MILESTONES:
            raise ValueError(f"unsupported forecast milestone:{self.milestone}")
        if any(not value.strip() for value in (self.artifact_hash, self.data_hash, self.config_hash, self.test_hash, self.commit_sha)):
            raise ValueError("forecast milestone evidence is incomplete")

    @property
    def fingerprint(self) -> str:
        return _digest((self.milestone, self.passed, self.artifact_hash, self.data_hash, self.config_hash, self.test_hash, self.commit_sha))


@dataclass(frozen=True, slots=True)
class NativeForecastOperationalProof:
    terminal_build: int
    broker: str
    server: str
    symbol: str
    timeframe: str
    session_export_hash: str
    market_data_hash: str
    model_fingerprint: str
    forecast_output_hash: str
    tester_output_hash: str
    point_in_time_replay_passed: bool
    native_execution_parity_passed: bool
    passed: bool

    def __post_init__(self) -> None:
        if self.terminal_build <= 0 or any(not value.strip() for value in (self.broker, self.server, self.symbol, self.timeframe)):
            raise ValueError("native forecast environment is incomplete")
        hashes = (self.session_export_hash, self.market_data_hash, self.model_fingerprint, self.forecast_output_hash, self.tester_output_hash)
        if any(len(value) != 64 for value in hashes):
            raise ValueError("native forecast proof requires SHA-256 artifacts")
        if self.passed and not (self.point_in_time_replay_passed and self.native_execution_parity_passed):
            raise ValueError("native forecast proof cannot pass failed subproofs")

    @property
    def fingerprint(self) -> str:
        return _digest(
            (
                self.terminal_build,
                self.broker,
                self.server,
                self.symbol.upper(),
                self.timeframe.upper(),
                self.session_export_hash,
                self.market_data_hash,
                self.model_fingerprint,
                self.forecast_output_hash,
                self.tester_output_hash,
                self.point_in_time_replay_passed,
                self.native_execution_parity_passed,
                self.passed,
            )
        )


@dataclass(frozen=True, slots=True)
class ForecastPhaseCertification:
    level: ProofLevel
    software_package_certified: bool
    operational_forecasting_certified: bool
    live_write_authorized: bool
    reasons: tuple[str, ...]
    certification_hash: str


def certify_forecast_phase(
    evidence: Iterable[ForecastMilestoneEvidence],
    *,
    current_commit_sha: str,
    native_proof: NativeForecastOperationalProof | None = None,
    demo_certification: ForecastDemoCertification | None = None,
    m75_operational_proof_hash: str | None = None,
    m85_analysis_certification_hash: str | None = None,
) -> ForecastPhaseCertification:
    if len(current_commit_sha.strip()) < 7:
        raise ValueError("forecast certification requires current commit identity")
    rows = tuple(evidence)
    reasons: list[str] = []
    by_milestone: dict[str, ForecastMilestoneEvidence] = {}
    for row in rows:
        if row.milestone in by_milestone:
            reasons.append(f"duplicate_evidence:{row.milestone}")
        else:
            by_milestone[row.milestone] = row
    for milestone in REQUIRED_FORECAST_MILESTONES:
        row = by_milestone.get(milestone)
        if row is None:
            reasons.append(f"missing_evidence:{milestone}")
        elif not row.passed:
            reasons.append(f"milestone_failed:{milestone}")
        elif row.commit_sha != current_commit_sha:
            reasons.append(f"commit_mismatch:{milestone}")
    software_reasons = tuple(reasons)
    software_certified = not software_reasons

    operational_reasons: list[str] = []
    if native_proof is None:
        operational_reasons.append("native_forecast_operational_proof_missing")
    elif not native_proof.passed:
        operational_reasons.append("native_forecast_operational_proof_failed")
    if demo_certification is None:
        operational_reasons.append("forecast_demo_certification_missing")
    elif not demo_certification.certified:
        operational_reasons.append("forecast_demo_certification_failed")
    if m75_operational_proof_hash is None or len(m75_operational_proof_hash) != 64:
        operational_reasons.append("m75_operational_proof_missing")
    if m85_analysis_certification_hash is None or len(m85_analysis_certification_hash) != 64:
        operational_reasons.append("m85_analysis_certification_missing")

    if not software_certified or (native_proof is not None and not native_proof.passed) or (demo_certification is not None and not demo_certification.certified):
        level = ProofLevel.FAILED
    elif operational_reasons:
        level = ProofLevel.OPERATIONAL_EVIDENCE_REQUIRED
    else:
        level = ProofLevel.OPERATIONALLY_PROVEN
    all_reasons = tuple(reasons + operational_reasons)
    payload = {
        "schema": "dusty-m95-forecast-certification-v1",
        "commit": current_commit_sha,
        "milestones": tuple((name, by_milestone[name].fingerprint) for name in REQUIRED_FORECAST_MILESTONES if name in by_milestone),
        "native": None if native_proof is None else native_proof.fingerprint,
        "demo": None if demo_certification is None else demo_certification.evidence_hash,
        "m75": m75_operational_proof_hash,
        "m85": m85_analysis_certification_hash,
        "level": level.value,
        "reasons": all_reasons,
        "live_write_authorized": False,
    }
    return ForecastPhaseCertification(
        level,
        software_certified,
        level is ProofLevel.OPERATIONALLY_PROVEN,
        False,
        all_reasons,
        _digest(payload),
    )


def _digest(payload: object) -> str:
    return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")).hexdigest()
