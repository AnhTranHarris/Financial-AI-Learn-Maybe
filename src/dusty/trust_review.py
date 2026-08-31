from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Iterable, Sequence

from .features import (
    FeatureConfig,
    FeatureVector,
    IndicatorParityResult,
    compare_mt5_indicators,
    parse_mt5_indicator_csv,
)
from .tester_parity import (
    ExecutionParityAssessment,
    ExpectedExecutionEnvelope,
    normalize_tester_trades,
    parse_tester_deals_csv,
    reconcile_execution_envelopes,
)


class Capability(StrEnum):
    DATA_ACQUISITION = "data_acquisition"
    MARKET_FEATURES = "market_features_indicators"
    EVIDENCE_COGNITION = "evidence_to_cognition"
    MT5_LABORATORY = "real_mt5_laboratory"


class ProofLevel(StrEnum):
    FAILED = "failed"
    UNPROVEN = "unproven"
    SOFTWARE_PROVEN = "software_proven"
    OPERATIONAL_EVIDENCE_REQUIRED = "operational_evidence_required"
    OPERATIONALLY_PROVEN = "operationally_proven"


class DataProbeKind(StrEnum):
    MARKET = "market"
    MACRO = "macro"
    EVENT = "event"
    PUBLIC_STRATEGY = "public_strategy"


@dataclass(frozen=True, slots=True)
class ArtifactFingerprint:
    label: str
    sha256: str
    byte_count: int
    observed_at: datetime
    producer: str

    @classmethod
    def from_bytes(
        cls,
        label: str,
        payload: bytes,
        *,
        observed_at: datetime,
        producer: str,
    ) -> "ArtifactFingerprint":
        if not label.strip() or not producer.strip():
            raise ValueError("artifact label and producer are required")
        if observed_at.tzinfo is None or observed_at.utcoffset() is None:
            raise ValueError("artifact observed_at must be timezone-aware")
        return cls(
            label.strip(),
            sha256(payload).hexdigest(),
            len(payload),
            observed_at,
            producer.strip(),
        )

    @classmethod
    def from_text(
        cls,
        label: str,
        text: str,
        *,
        observed_at: datetime,
        producer: str,
    ) -> "ArtifactFingerprint":
        return cls.from_bytes(
            label,
            text.encode("utf-8"),
            observed_at=observed_at,
            producer=producer,
        )


@dataclass(frozen=True, slots=True)
class SoftwareProof:
    commit_sha: str
    run_id: str
    passed: bool
    observed_at: datetime

    def __post_init__(self) -> None:
        if len(self.commit_sha.strip()) < 7 or not self.run_id.strip():
            raise ValueError("software proof requires commit and run identity")
        if self.observed_at.tzinfo is None or self.observed_at.utcoffset() is None:
            raise ValueError("software proof observed_at must be timezone-aware")


@dataclass(frozen=True, slots=True)
class LiveDataProbe:
    kind: DataProbeKind
    source_id: str
    artifact: ArtifactFingerprint
    normalized_records: int

    def __post_init__(self) -> None:
        if not self.source_id.strip():
            raise ValueError("data probe source is required")
        if self.normalized_records < 1:
            raise ValueError("successful live data probe requires at least one normalized record")


@dataclass(frozen=True, slots=True)
class MT5Environment:
    terminal_build: int
    symbol: str
    period: str

    def __post_init__(self) -> None:
        if self.terminal_build <= 0 or not self.symbol.strip() or not self.period.strip():
            raise ValueError("native MT5 environment requires build, symbol, and period")


@dataclass(frozen=True, slots=True)
class NativeIndicatorProof:
    artifact: ArtifactFingerprint
    environment: MT5Environment | None
    parity: IndicatorParityResult
    passed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NativeTesterProof:
    artifact: ArtifactFingerprint
    environment: MT5Environment | None
    parity: ExecutionParityAssessment
    passed: bool
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CapabilityAssessment:
    capability: Capability
    level: ProofLevel
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class M75TrustReport:
    commit_sha: str
    assessments: tuple[CapabilityAssessment, ...]
    fingerprint: str

    def for_capability(self, capability: Capability) -> CapabilityAssessment:
        for assessment in self.assessments:
            if assessment.capability is capability:
                return assessment
        raise KeyError(capability)

    @property
    def operationally_trusted(self) -> bool:
        return all(
            assessment.level is ProofLevel.OPERATIONALLY_PROVEN
            for assessment in self.assessments
        )


def _parse_environment(
    text: str,
    *,
    expected_symbol: str,
    expected_period: str,
) -> tuple[MT5Environment | None, tuple[str, ...]]:
    reader = csv.DictReader(io.StringIO(text))
    required = {"terminal_build", "symbol", "period"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        return None, ("native_environment_metadata_missing",)
    environments: set[tuple[int, str, str]] = set()
    for row in reader:
        raw_build = (row.get("terminal_build") or "").strip()
        symbol = (row.get("symbol") or "").strip().upper()
        period = (row.get("period") or "").strip().upper()
        if not raw_build or not symbol or not period:
            return None, ("native_environment_metadata_incomplete",)
        try:
            build = int(raw_build)
        except ValueError:
            return None, ("native_terminal_build_invalid",)
        environments.add((build, symbol, period))
    if not environments:
        return None, ("native_environment_rows_missing",)
    if len(environments) != 1:
        return None, ("native_environment_mixed",)
    build, symbol, period = next(iter(environments))
    try:
        environment = MT5Environment(build, symbol, period)
    except ValueError:
        return None, ("native_environment_invalid",)
    reasons = []
    if symbol != expected_symbol.strip().upper():
        reasons.append("native_symbol_mismatch")
    if period != expected_period.strip().upper():
        reasons.append("native_period_mismatch")
    return environment, tuple(reasons)


def qualify_native_indicators(
    features: Iterable[FeatureVector],
    mt5_csv: str,
    *,
    config: FeatureConfig,
    expected_symbol: str,
    expected_period: str,
    observed_at: datetime,
    min_rows: int = 20,
    abs_tolerance: float = 1e-8,
) -> NativeIndicatorProof:
    artifact = ArtifactFingerprint.from_text(
        "mt5_indicator_parity_csv",
        mt5_csv,
        observed_at=observed_at,
        producer="DustyIndicatorParity.mq5",
    )
    environment, environment_reasons = _parse_environment(
        mt5_csv,
        expected_symbol=expected_symbol,
        expected_period=expected_period,
    )
    rows = parse_mt5_indicator_csv(mt5_csv)
    parity = compare_mt5_indicators(
        features,
        rows,
        config=config,
        min_rows=min_rows,
        abs_tolerance=abs_tolerance,
    )
    reasons = tuple(environment_reasons) + tuple(parity.reasons)
    return NativeIndicatorProof(
        artifact,
        environment,
        parity,
        not reasons,
        reasons,
    )


def qualify_native_tester(
    expected: Sequence[ExpectedExecutionEnvelope],
    deals_csv: str,
    *,
    expected_symbol: str,
    expected_period: str,
    observed_at: datetime,
    max_entry_delay_seconds: float,
    max_entry_price_gap: float,
    max_exit_price_gap: float,
    max_volume_gap: float = 1e-9,
    max_time_exit_delay_seconds: float = 60.0,
) -> NativeTesterProof:
    artifact = ArtifactFingerprint.from_text(
        "mt5_tester_deals_csv",
        deals_csv,
        observed_at=observed_at,
        producer="DustyResearchEA.mq5",
    )
    environment, environment_reasons = _parse_environment(
        deals_csv,
        expected_symbol=expected_symbol,
        expected_period=expected_period,
    )
    deals = parse_tester_deals_csv(deals_csv)
    trades = normalize_tester_trades(deals)
    parity = reconcile_execution_envelopes(
        expected,
        trades,
        max_entry_delay_seconds=max_entry_delay_seconds,
        max_entry_price_gap=max_entry_price_gap,
        max_exit_price_gap=max_exit_price_gap,
        max_volume_gap=max_volume_gap,
        max_time_exit_delay_seconds=max_time_exit_delay_seconds,
    )
    reasons = tuple(environment_reasons) + tuple(parity.reasons)
    return NativeTesterProof(
        artifact,
        environment,
        parity,
        not reasons,
        reasons,
    )


def _data_assessment(
    software: SoftwareProof | None,
    probes: Iterable[LiveDataProbe],
) -> CapabilityAssessment:
    if software is None or not software.passed:
        return CapabilityAssessment(
            Capability.DATA_ACQUISITION,
            ProofLevel.FAILED if software is not None else ProofLevel.UNPROVEN,
            ("software_acquisition_tests_not_proven",),
        )
    kinds = {probe.kind for probe in probes}
    required = {
        DataProbeKind.MARKET,
        DataProbeKind.MACRO,
        DataProbeKind.EVENT,
        DataProbeKind.PUBLIC_STRATEGY,
    }
    missing = tuple(sorted((kind.value for kind in required - kinds)))
    if missing:
        return CapabilityAssessment(
            Capability.DATA_ACQUISITION,
            ProofLevel.OPERATIONAL_EVIDENCE_REQUIRED,
            tuple(f"live_probe_missing:{kind}" for kind in missing),
        )
    return CapabilityAssessment(
        Capability.DATA_ACQUISITION,
        ProofLevel.OPERATIONALLY_PROVEN,
        (),
    )


def _indicator_assessment(
    software: SoftwareProof | None,
    native: NativeIndicatorProof | None,
) -> CapabilityAssessment:
    if software is None or not software.passed:
        return CapabilityAssessment(
            Capability.MARKET_FEATURES,
            ProofLevel.FAILED if software is not None else ProofLevel.UNPROVEN,
            ("feature_software_tests_not_proven",),
        )
    if native is None:
        return CapabilityAssessment(
            Capability.MARKET_FEATURES,
            ProofLevel.OPERATIONAL_EVIDENCE_REQUIRED,
            ("native_mt5_indicator_parity_required",),
        )
    if not native.passed:
        return CapabilityAssessment(
            Capability.MARKET_FEATURES,
            ProofLevel.FAILED,
            native.reasons or ("native_mt5_indicator_parity_failed",),
        )
    return CapabilityAssessment(
        Capability.MARKET_FEATURES,
        ProofLevel.OPERATIONALLY_PROVEN,
        (),
    )


def _cognition_assessment(software: SoftwareProof | None) -> CapabilityAssessment:
    if software is None:
        return CapabilityAssessment(
            Capability.EVIDENCE_COGNITION,
            ProofLevel.UNPROVEN,
            ("cognition_software_tests_not_proven",),
        )
    if not software.passed:
        return CapabilityAssessment(
            Capability.EVIDENCE_COGNITION,
            ProofLevel.FAILED,
            ("cognition_software_tests_failed",),
        )
    # This capability is a deterministic software transformation. External market truth is separately
    # certified by data/feature layers, so no broker-native execution artifact is needed to prove the
    # transformation itself.
    return CapabilityAssessment(
        Capability.EVIDENCE_COGNITION,
        ProofLevel.OPERATIONALLY_PROVEN,
        (),
    )


def _lab_assessment(
    software: SoftwareProof | None,
    indicator: NativeIndicatorProof | None,
    tester: NativeTesterProof | None,
    probes: Iterable[LiveDataProbe],
) -> CapabilityAssessment:
    if software is None or not software.passed:
        return CapabilityAssessment(
            Capability.MT5_LABORATORY,
            ProofLevel.FAILED if software is not None else ProofLevel.UNPROVEN,
            ("laboratory_software_tests_not_proven",),
        )
    reasons = []
    if not any(probe.kind is DataProbeKind.MARKET for probe in probes):
        reasons.append("native_mt5_market_data_probe_required")
    if indicator is None:
        reasons.append("native_indicator_artifact_required")
    elif not indicator.passed:
        reasons.extend(indicator.reasons or ("native_indicator_parity_failed",))
    if tester is None:
        reasons.append("native_tester_deal_artifact_required")
    elif not tester.passed:
        reasons.extend(tester.reasons or ("native_tester_parity_failed",))
    if reasons:
        level = ProofLevel.FAILED if (
            indicator is not None and not indicator.passed
            or tester is not None and not tester.passed
        ) else ProofLevel.OPERATIONAL_EVIDENCE_REQUIRED
        return CapabilityAssessment(
            Capability.MT5_LABORATORY,
            level,
            tuple(reasons),
        )
    return CapabilityAssessment(
        Capability.MT5_LABORATORY,
        ProofLevel.OPERATIONALLY_PROVEN,
        (),
    )


def build_m75_trust_report(
    *,
    commit_sha: str,
    software: SoftwareProof | None,
    data_probes: Iterable[LiveDataProbe] = (),
    indicator_proof: NativeIndicatorProof | None = None,
    tester_proof: NativeTesterProof | None = None,
) -> M75TrustReport:
    if len(commit_sha.strip()) < 7:
        raise ValueError("trust report requires commit identity")
    probes = tuple(data_probes)
    assessments = (
        _data_assessment(software, probes),
        _indicator_assessment(software, indicator_proof),
        _cognition_assessment(software),
        _lab_assessment(software, indicator_proof, tester_proof, probes),
    )
    payload = "|".join(
        (
            commit_sha,
            software.commit_sha if software else "no-software-proof",
            software.run_id if software else "",
            *(f"{item.capability.value}:{item.level.value}:{','.join(item.reasons)}" for item in assessments),
            *(f"probe:{probe.kind.value}:{probe.source_id}:{probe.artifact.sha256}" for probe in probes),
            indicator_proof.artifact.sha256 if indicator_proof else "no-indicator-proof",
            tester_proof.artifact.sha256 if tester_proof else "no-tester-proof",
        )
    )
    return M75TrustReport(
        commit_sha.strip(),
        assessments,
        sha256(payload.encode("utf-8")).hexdigest(),
    )
