from __future__ import annotations

"""Production-only M185 custody envelope.

The legacy Frozen Champion Registry remains an append-only storage primitive.
Production custody must additionally bind the exact M185 qualification manifest,
M165-M174 production evidence chain, external deterministic selection evidence,
and frozen deployment into one immutable envelope.
"""

from dataclasses import dataclass
from hashlib import sha256
import json

from .champion_registry import FrozenChampionRecord, freeze_champion_record
from .forecast_integration_certification import ForecastIntegrationCertification
from .m174_production_chain import M174ProductionEvidenceChain
from .m185_production_qualification import ProductionQualificationManifest
from .robustness_gate import RobustnessCertification
from .strategy_v3 import FrozenStrategyDeployment


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


@dataclass(frozen=True, slots=True)
class ProductionChampionCustodyEnvelope:
    champion_record: FrozenChampionRecord
    qualification_manifest_fingerprint: str
    production_chain_fingerprint: str
    selection_evidence_fingerprint: str

    broker_write_authority = False
    live_write_authority = False
    retry_authority = False
    promotion_authority = False
    strategy_mutation_authority = False
    risk_override_authority = False

    def __post_init__(self) -> None:
        for name in (
            "qualification_manifest_fingerprint",
            "production_chain_fingerprint",
            "selection_evidence_fingerprint",
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        if self.champion_record.selection_evidence_fingerprint != self.selection_evidence_fingerprint:
            raise ValueError("Champion record selection evidence does not match production custody envelope")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m185-production-champion-custody-v1",
            "champion_fingerprint": self.champion_record.fingerprint,
            "deployment_fingerprint": self.champion_record.deployment_fingerprint,
            "lane_id": self.champion_record.lane_id,
            "strategy_fingerprint": self.champion_record.strategy_fingerprint,
            "qualification_manifest_fingerprint": self.qualification_manifest_fingerprint,
            "production_chain_fingerprint": self.production_chain_fingerprint,
            "selection_evidence_fingerprint": self.selection_evidence_fingerprint,
            "robustness_fingerprint": self.champion_record.robustness_fingerprint,
            "forecast_integration_fingerprint": self.champion_record.forecast_integration_fingerprint,
            "source_commit": self.champion_record.source_commit,
            "authority": {
                "broker_write": False,
                "live_write": False,
                "retry": False,
                "promotion": False,
                "strategy_mutation": False,
                "risk_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def freeze_production_champion(
    *,
    manifest: ProductionQualificationManifest,
    chain: M174ProductionEvidenceChain,
    strategy_family: str,
    deployment: FrozenStrategyDeployment,
    selection_evidence_fingerprint: str,
    robustness: RobustnessCertification,
    forecast_integration: ForecastIntegrationCertification | None,
    parent_champion_fingerprint: str | None,
    created_at,
) -> ProductionChampionCustodyEnvelope:
    selection = _sha(selection_evidence_fingerprint, "production selection evidence")
    if chain.qualification_manifest_fingerprint != manifest.fingerprint:
        raise ValueError("M174 production chain does not match M185 qualification manifest")
    if chain.strategy_fingerprint != manifest.strategy_hash:
        raise ValueError("M174 production strategy does not match M185 qualification manifest")
    if deployment.strategy_hash != manifest.strategy_hash:
        raise ValueError("frozen deployment strategy does not match M185 qualification manifest")
    if chain.robustness_fingerprint != robustness.fingerprint:
        raise ValueError("M174 production chain robustness does not match supplied M174 certificate")

    record = freeze_champion_record(
        lane_id=manifest.lane_id,
        strategy_family=strategy_family,
        deployment=deployment,
        source_commit=manifest.source_commit,
        selection_evidence_fingerprint=selection,
        robustness=robustness,
        forecast_integration=forecast_integration,
        parent_champion_fingerprint=parent_champion_fingerprint,
        created_at=created_at,
    )
    if record.robustness_fingerprint != chain.robustness_fingerprint:
        raise RuntimeError("Frozen Champion robustness drifted from production chain")
    return ProductionChampionCustodyEnvelope(
        champion_record=record,
        qualification_manifest_fingerprint=manifest.fingerprint,
        production_chain_fingerprint=chain.fingerprint,
        selection_evidence_fingerprint=selection,
    )
