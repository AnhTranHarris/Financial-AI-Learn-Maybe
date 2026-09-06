from __future__ import annotations

"""Populate the persistent Strategy Estate from already-governed proposals.

Source acquisition remains owned by source_intake/Vibe/manual review. This
service starts only after a StrategyProposal exists. It reconstructs one bounded
candidate with the existing Ollama adapter, obtains a bounded quant taxonomy
classification, and atomically registers successful immutable reconstructions.
No result can promote a Champion, unlock Demo/Live, size risk, or send an order.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from pathlib import Path
from typing import Iterable

from .ollama_strategy_classifier import OllamaStrategyClassifier, attach_quant_identity
from .ollama_strategy_reconstruction import OllamaReconstructionRequest, OllamaStrategyReconstructor
from .source_intake import EvidenceClass, StrategyProposal, deduplicate_proposals
from .strategy_estate import StrategyEstateUpdate, register_reconstructions
from .trading_skills import StrategyReconstruction


class EstatePopulationStatus(StrEnum):
    ADDED = "added"
    RECONSTRUCTION_UNAVAILABLE = "reconstruction_unavailable"
    CLASSIFICATION_UNAVAILABLE = "classification_unavailable"
    INELIGIBLE_PROPOSAL = "ineligible_proposal"


@dataclass(frozen=True, slots=True)
class EstatePopulationRow:
    proposal_id: str
    status: EstatePopulationStatus
    reconstruction_fingerprint: str = ""
    reason: str = ""

    def __post_init__(self) -> None:
        if not self.proposal_id.strip():
            raise ValueError("estate population row requires proposal identity")
        if self.status is EstatePopulationStatus.ADDED:
            if len(self.reconstruction_fingerprint) != 64 or self.reason:
                raise ValueError("added estate row requires reconstruction fingerprint only")
        elif self.reconstruction_fingerprint or not self.reason.strip():
            raise ValueError("non-added estate row requires reason only")


@dataclass(frozen=True, slots=True)
class EstatePopulationResult:
    rows: tuple[EstatePopulationRow, ...]
    estate_update: StrategyEstateUpdate | None

    @property
    def added_count(self) -> int:
        return sum(row.status is EstatePopulationStatus.ADDED for row in self.rows)


class StrategyEstateBuilder:
    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False

    def __init__(
        self,
        *,
        reconstructor: OllamaStrategyReconstructor | None = None,
        classifier: OllamaStrategyClassifier | None = None,
    ) -> None:
        self.reconstructor = reconstructor or OllamaStrategyReconstructor()
        self.classifier = classifier or OllamaStrategyClassifier()

    def populate(
        self,
        proposals: Iterable[StrategyProposal],
        *,
        model_tag: str,
        model_digest: str,
        allowed_symbols: tuple[str, ...],
        allowed_timeframes: tuple[str, ...],
        allowed_features: tuple[str, ...],
        allowed_sessions: tuple[str, ...] = (),
        estate_path: str | Path | None = None,
        created_at: datetime | None = None,
    ) -> EstatePopulationResult:
        chosen_time = created_at or datetime.now(timezone.utc)
        if chosen_time.tzinfo is None or chosen_time.utcoffset() is None:
            raise ValueError("strategy estate population time must be timezone-aware")
        now = chosen_time.astimezone(timezone.utc)

        universe_symbols = tuple(dict.fromkeys(value.strip().upper() for value in allowed_symbols if value.strip()))
        universe_timeframes = tuple(dict.fromkeys(value.strip().upper() for value in allowed_timeframes if value.strip()))
        features = tuple(dict.fromkeys(value.strip() for value in allowed_features if value.strip()))
        sessions = tuple(dict.fromkeys(value.strip().upper() for value in allowed_sessions if value.strip()))
        if not universe_symbols or not universe_timeframes or not features:
            raise ValueError("strategy estate builder requires symbol, timeframe, and feature universes")

        successful: list[StrategyReconstruction] = []
        outcomes: list[EstatePopulationRow] = []
        for proposal in deduplicate_proposals(proposals):
            if proposal.evidence_class is not EvidenceClass.STRATEGY_HYPOTHESIS:
                outcomes.append(EstatePopulationRow(
                    proposal.proposal_id,
                    EstatePopulationStatus.INELIGIBLE_PROPOSAL,
                    reason="proposal_is_not_strategy_hypothesis",
                ))
                continue

            symbols = _intersect_or_default(proposal.symbols, universe_symbols)
            timeframes = _eligible_timeframes(proposal.timeframes, universe_timeframes)
            if not symbols:
                outcomes.append(EstatePopulationRow(
                    proposal.proposal_id,
                    EstatePopulationStatus.INELIGIBLE_PROPOSAL,
                    reason="proposal_has_no_allowed_symbol",
                ))
                continue
            if not timeframes:
                outcomes.append(EstatePopulationRow(
                    proposal.proposal_id,
                    EstatePopulationStatus.INELIGIBLE_PROPOSAL,
                    reason="proposal_has_no_m5_plus_allowed_timeframe",
                ))
                continue

            request = OllamaReconstructionRequest(
                request_id=f"estate:{proposal.proposal_id}",
                proposal=proposal,
                model_tag=model_tag,
                model_digest=model_digest,
                allowed_symbols=symbols,
                allowed_timeframes=timeframes,
                allowed_features=features,
                allowed_sessions=sessions,
            )
            reconstructed = self.reconstructor.reconstruct(request, created_at=now)
            if not reconstructed.available or reconstructed.reconstruction is None:
                outcomes.append(EstatePopulationRow(
                    proposal.proposal_id,
                    EstatePopulationStatus.RECONSTRUCTION_UNAVAILABLE,
                    reason=reconstructed.error or "reconstruction_unavailable",
                ))
                continue

            classified = self.classifier.classify(
                reconstructed.reconstruction,
                model_tag=model_tag,
                model_digest=model_digest,
            )
            if not classified.available or classified.classification is None:
                outcomes.append(EstatePopulationRow(
                    proposal.proposal_id,
                    EstatePopulationStatus.CLASSIFICATION_UNAVAILABLE,
                    reason=classified.error or "classification_unavailable",
                ))
                continue

            row = attach_quant_identity(reconstructed.reconstruction, classified.classification)
            successful.append(row)
            outcomes.append(EstatePopulationRow(
                proposal.proposal_id,
                EstatePopulationStatus.ADDED,
                reconstruction_fingerprint=row.fingerprint,
            ))

        update = register_reconstructions(successful, path=estate_path) if successful else None
        return EstatePopulationResult(tuple(outcomes), update)


def _intersect_or_default(proposal_values: tuple[str, ...], universe: tuple[str, ...]) -> tuple[str, ...]:
    if not proposal_values:
        return universe
    allowed = set(universe)
    return tuple(value.upper() for value in proposal_values if value.upper() in allowed)


def _minutes(value: str) -> int | None:
    value = value.strip().upper()
    if len(value) < 2 or not value[1:].isdigit() or value[0] not in {"M", "H"}:
        return None
    amount = int(value[1:])
    if amount <= 0:
        return None
    return amount if value[0] == "M" else amount * 60


def _eligible_timeframes(proposal_values: tuple[str, ...], universe: tuple[str, ...]) -> tuple[str, ...]:
    source = tuple(value.upper() for value in proposal_values) if proposal_values else universe
    allowed = set(universe)
    return tuple(
        value for value in source
        if value in allowed and (_minutes(value) or 0) >= 5
    )
