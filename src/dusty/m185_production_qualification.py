from __future__ import annotations

"""Production qualification planning for the first genuine M185 Champion.

This module does not choose a Champion and does not create M174 evidence.  It
turns the persistent Strategy Estate into immutable, content-addressed
qualification manifests so every candidate/lane can be evaluated through the
real M165-M174 evidence chain before any M185 custody decision is possible.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from typing import Iterable

from .trading_skills import StrategyReconstruction


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 40 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError("qualification source commit requires a full 40-character Git SHA")
    return rendered


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("qualification created_at must be timezone-aware")
    return value.astimezone(timezone.utc)


def _text(value: str, label: str, maximum: int = 512) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be nonempty, one line, and <= {maximum} characters")
    return rendered


QUALIFICATION_STAGES: tuple[str, ...] = (
    "M165_BROKER_ECONOMICS",
    "M166_WALK_FORWARD",
    "M167_PURGED_TEMPORAL_VALIDATION",
    "M168_PARAMETER_STABILITY",
    "M169_REGIME_TORTURE",
    "M170_COST_SLIPPAGE_STRESS",
    "M171_FORWARD_DECAY",
    "M172_TAIL_RISK",
    "M173_STRATEGY_DEPENDENCY",
    "M174_ROBUSTNESS_CERTIFICATION",
)


@dataclass(frozen=True, slots=True)
class ProductionQualificationManifest:
    source_commit: str
    estate_sha256: str
    reconstruction_fingerprint: str
    proposal_fingerprint: str
    strategy_hash: str
    source_id: str
    source_url: str
    source_content_sha256: str
    source_family_fingerprint: str
    title: str
    symbol: str
    timeframe: str
    lane_id: str
    source_claim_complete: bool
    hypothesis_rule_count: int
    required_stages: tuple[str, ...]
    created_at: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit))
        for name in (
            "estate_sha256",
            "reconstruction_fingerprint",
            "proposal_fingerprint",
            "strategy_hash",
            "source_content_sha256",
            "source_family_fingerprint",
        ):
            object.__setattr__(self, name, _sha(getattr(self, name), name))
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id", 128).lower())
        object.__setattr__(self, "source_url", _text(self.source_url, "source_url", 2048))
        object.__setattr__(self, "title", _text(self.title, "title", 256))
        symbol = _text(self.symbol, "symbol", 64).upper()
        timeframe = _text(self.timeframe, "timeframe", 32).upper()
        lane = _text(self.lane_id, "lane_id", 128).lower()
        if lane != f"{symbol}:{timeframe}:{self.source_family_fingerprint[:16]}".lower():
            raise ValueError("qualification lane identity drift")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "timeframe", timeframe)
        object.__setattr__(self, "lane_id", lane)
        if isinstance(self.hypothesis_rule_count, bool) or self.hypothesis_rule_count < 0:
            raise ValueError("hypothesis_rule_count must be nonnegative")
        stages = tuple(self.required_stages)
        if stages != QUALIFICATION_STAGES:
            raise ValueError("qualification stages must exactly match M165-M174")
        object.__setattr__(self, "required_stages", stages)
        object.__setattr__(self, "created_at", _aware(self.created_at))
        if self.schema_version != 1:
            raise ValueError("unsupported production qualification schema")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m185-production-qualification-manifest-v1",
            "schema_version": self.schema_version,
            "source_commit": self.source_commit,
            "estate_sha256": self.estate_sha256,
            "reconstruction_fingerprint": self.reconstruction_fingerprint,
            "proposal_fingerprint": self.proposal_fingerprint,
            "strategy_hash": self.strategy_hash,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_content_sha256": self.source_content_sha256,
            "source_family_fingerprint": self.source_family_fingerprint,
            "title": self.title,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "lane_id": self.lane_id,
            "source_claim_complete": self.source_claim_complete,
            "hypothesis_rule_count": self.hypothesis_rule_count,
            "required_stages": list(self.required_stages),
            "created_at": self.created_at.isoformat(),
            "authority": {
                "broker_write": False,
                "promotion": False,
                "live_write": False,
                "risk_override": False,
                "guardian_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    broker_write_authority = False
    promotion_authority = False
    live_write_authority = False
    risk_override_authority = False
    guardian_override_authority = False


@dataclass(frozen=True, slots=True)
class ProductionQualificationPlan:
    source_commit: str
    estate_sha256: str
    manifests: tuple[ProductionQualificationManifest, ...]
    created_at: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit))
        object.__setattr__(self, "estate_sha256", _sha(self.estate_sha256, "estate_sha256"))
        rows = tuple(self.manifests)
        if not rows:
            raise ValueError("qualification plan requires at least one manifest")
        if any(row.source_commit != self.source_commit or row.estate_sha256 != self.estate_sha256 for row in rows):
            raise ValueError("qualification plan identity drift")
        identities = tuple((row.lane_id, row.reconstruction_fingerprint) for row in rows)
        if len(identities) != len(set(identities)):
            raise ValueError("qualification plan contains duplicate lane/reconstruction identities")
        ordered = tuple(sorted(rows, key=lambda row: (row.lane_id, row.reconstruction_fingerprint)))
        object.__setattr__(self, "manifests", ordered)
        object.__setattr__(self, "created_at", _aware(self.created_at))
        if self.schema_version != 1:
            raise ValueError("unsupported production qualification plan schema")

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m185-production-qualification-plan-v1",
            "schema_version": self.schema_version,
            "source_commit": self.source_commit,
            "estate_sha256": self.estate_sha256,
            "candidate_lane_count": len(self.manifests),
            "manifest_fingerprints": [row.fingerprint for row in self.manifests],
            "created_at": self.created_at.isoformat(),
            "authority": {"broker_write": False, "promotion": False, "live_write": False},
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)


def build_production_qualification_plan(
    reconstructions: Iterable[StrategyReconstruction],
    *,
    estate_sha256: str,
    source_commit: str,
    created_at: datetime,
) -> ProductionQualificationPlan:
    estate = _sha(estate_sha256, "estate_sha256")
    commit = _git_sha(source_commit)
    when = _aware(created_at)
    rows: list[ProductionQualificationManifest] = []
    for reconstruction in tuple(reconstructions):
        for symbol in reconstruction.symbols:
            lane = f"{symbol.upper()}:{reconstruction.timeframe.upper()}:{reconstruction.source_family_fingerprint[:16]}".lower()
            rows.append(
                ProductionQualificationManifest(
                    source_commit=commit,
                    estate_sha256=estate,
                    reconstruction_fingerprint=reconstruction.fingerprint,
                    proposal_fingerprint=reconstruction.proposal_fingerprint,
                    strategy_hash=reconstruction.candidate_spec.strategy_hash,
                    source_id=reconstruction.source_id,
                    source_url=reconstruction.source_url,
                    source_content_sha256=reconstruction.source_content_sha256,
                    source_family_fingerprint=reconstruction.source_family_fingerprint,
                    title=reconstruction.title,
                    symbol=symbol,
                    timeframe=reconstruction.timeframe,
                    lane_id=lane,
                    source_claim_complete=reconstruction.source_claim_complete,
                    hypothesis_rule_count=reconstruction.hypothesis_rule_count,
                    required_stages=QUALIFICATION_STAGES,
                    created_at=when,
                )
            )
    return ProductionQualificationPlan(commit, estate, tuple(rows), when)
