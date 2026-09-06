from __future__ import annotations

"""M196.5 Trading Skills Library, reconstruction, and AUTO routing.

This module is deliberately a projection/orchestration boundary.  It does not
replace M157 strategy compilation, M174/M184 research certification, M185
Champion custody, M194 Demo certification, M195 portfolio risk, or M196
concentration control.  It turns those immutable identities into two useful
surfaces:

* explicit reconstructed *research* packages whose unknown source rules remain
  visibly hypothetical; and
* immutable Trading Skill projections that an AUTO router may rank for
  research/shadow use without acquiring broker, risk, Guardian, or promotion
  authority.

An LLM may propose reconstruction fields, but constructing these dataclasses is
the deterministic validation boundary.  Catalog metadata alone is never
executable.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable

from .champion_registry import (
    ChampionLifecycleState,
    FrozenChampionRecord,
)
from .cognition import CognitionPolicy
from .features import FEATURE_NUMERICS_VERSION, FeatureConfig
from .reviewed_strategies import ReviewedResearchPackage, reviewed_research_packages
from .runtime import CompiledStrategy, compile_strategy
from .single_desk_demo_certification import (
    SingleDeskDemoCertification,
    SingleDeskDemoStatus,
)
from .source_intake import EvidenceClass, StrategyProposal
from .strategy_catalog import OperatingMode, StrategyCatalogEntry, StrategyStage
from .strategy_ir import (
    EligibilityStatus,
    StrategySpecV2,
    assess_strategy_eligibility,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires SHA-256 identity")
    return rendered


def _git_sha(value: str, label: str) -> str:
    rendered = str(value).strip().lower()
    if len(rendered) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in rendered):
        raise ValueError(f"{label} requires a 40- or 64-character commit identity")
    return rendered


def _text(value: str, label: str, *, maximum: int = 512) -> str:
    rendered = str(value).strip()
    if not rendered or "\n" in rendered or "\r" in rendered or len(rendered) > maximum:
        raise ValueError(f"{label} must be nonempty, one line, and <= {maximum} characters")
    return rendered


def _aware(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(timezone.utc)


def _unique_text(values: Iterable[str], label: str, *, upper: bool = False) -> tuple[str, ...]:
    rendered = tuple(_text(value, label, maximum=128) for value in values)
    normalized = tuple(value.upper() if upper else value.lower() for value in rendered)
    if not rendered or len(normalized) != len(set(normalized)):
        raise ValueError(f"{label} values must be unique and nonempty")
    return tuple(sorted((value.upper() if upper else value for value in rendered), key=str.casefold))


def _timeframe_minutes(value: str) -> int | None:
    text = value.strip().upper()
    if text.startswith("M") and text[1:].isdigit():
        return int(text[1:])
    if text.startswith("H") and text[1:].isdigit():
        return int(text[1:]) * 60
    return None


class ReconstructionRuleBasis(StrEnum):
    SOURCE_DECLARED = "source_declared"
    RESEARCH_HYPOTHESIS = "research_hypothesis"


class ReconstructionActor(StrEnum):
    DETERMINISTIC_TRANSLATOR = "deterministic_translator"
    OLLAMA = "ollama"
    CARSON_USER_REVIEW = "carson_user_review"
    DUSTY_RESEARCH = "dusty_research"


@dataclass(frozen=True, slots=True)
class ReconstructionRule:
    name: str
    value: str
    basis: ReconstructionRuleBasis

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _text(self.name, "reconstruction rule name", maximum=128).lower())
        object.__setattr__(self, "value", _text(self.value, "reconstruction rule value", maximum=1024))
        if not isinstance(self.basis, ReconstructionRuleBasis):
            raise ValueError("reconstruction rule basis is invalid")

    @property
    def payload(self) -> tuple[str, str, str]:
        return self.name, self.value, self.basis.value


@dataclass(frozen=True, slots=True)
class StrategyReconstruction:
    """Executable research hypothesis with explicit source-vs-inferred provenance.

    ``unresolved_source_rules`` is intentionally preserved even when the supplied
    research candidate fills those unknowns with hypotheses.  The candidate can
    therefore be backtested without pretending the source published those rules.
    """

    proposal_fingerprint: str
    source_id: str
    source_url: str
    source_content_sha256: str
    source_family_fingerprint: str
    title: str
    symbols: tuple[str, ...]
    timeframe: str
    candidate_spec: StrategySpecV2
    rules: tuple[ReconstructionRule, ...]
    unresolved_source_rules: tuple[str, ...]
    actor: ReconstructionActor
    actor_fingerprint: str
    created_at: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        object.__setattr__(self, "proposal_fingerprint", _sha(self.proposal_fingerprint, "proposal"))
        object.__setattr__(self, "source_content_sha256", _sha(self.source_content_sha256, "source content"))
        object.__setattr__(self, "source_family_fingerprint", _sha(self.source_family_fingerprint, "source family"))
        object.__setattr__(self, "source_id", _text(self.source_id, "source_id", maximum=128).lower())
        object.__setattr__(self, "source_url", _text(self.source_url, "source_url", maximum=2048))
        if not (self.source_url.startswith("https://") or self.source_url.startswith("http://localhost")):
            raise ValueError("reconstruction source URL must be HTTPS or certified localhost")
        object.__setattr__(self, "title", _text(self.title, "reconstruction title", maximum=256))
        object.__setattr__(self, "symbols", _unique_text(self.symbols, "reconstruction symbol", upper=True))
        timeframe = _text(self.timeframe, "reconstruction timeframe", maximum=32).upper()
        minutes = _timeframe_minutes(timeframe)
        if minutes is None or minutes != self.candidate_spec.decision_timeframe_minutes:
            raise ValueError("reconstruction timeframe must exactly match candidate decision timeframe")
        object.__setattr__(self, "timeframe", timeframe)
        if not isinstance(self.candidate_spec, StrategySpecV2):
            raise ValueError("reconstruction candidate must use StrategySpecV2")
        assessment = assess_strategy_eligibility(self.candidate_spec)
        if assessment.status is EligibilityStatus.PROHIBITED:
            raise ValueError("reconstruction candidate violates strategy constitution: " + ",".join(assessment.reasons))
        rules = tuple(sorted(self.rules, key=lambda row: (row.name, row.basis.value, row.value)))
        if not rules or len({(row.name, row.basis) for row in rules}) != len(rules):
            raise ValueError("reconstruction rules require unique name/basis pairs")
        object.__setattr__(self, "rules", rules)
        unresolved = tuple(sorted({_text(value, "unresolved source rule", maximum=128).lower() for value in self.unresolved_source_rules}))
        object.__setattr__(self, "unresolved_source_rules", unresolved)
        if not isinstance(self.actor, ReconstructionActor):
            raise ValueError("reconstruction actor is invalid")
        object.__setattr__(self, "actor_fingerprint", _sha(self.actor_fingerprint, "reconstruction actor"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "reconstruction created_at"))
        if self.schema_version != 1:
            raise ValueError("unsupported reconstruction schema version")

    @property
    def source_claim_complete(self) -> bool:
        return not self.unresolved_source_rules and all(
            row.basis is ReconstructionRuleBasis.SOURCE_DECLARED for row in self.rules
        )

    @property
    def hypothesis_rule_count(self) -> int:
        return sum(row.basis is ReconstructionRuleBasis.RESEARCH_HYPOTHESIS for row in self.rules)

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m1965-strategy-reconstruction-v1",
            "schema_version": self.schema_version,
            "proposal_fingerprint": self.proposal_fingerprint,
            "source_id": self.source_id,
            "source_url": self.source_url,
            "source_content_sha256": self.source_content_sha256,
            "source_family_fingerprint": self.source_family_fingerprint,
            "title": self.title,
            "symbols": self.symbols,
            "timeframe": self.timeframe,
            "candidate_strategy_hash": self.candidate_spec.strategy_hash,
            "rules": tuple(row.payload for row in self.rules),
            "unresolved_source_rules": self.unresolved_source_rules,
            "source_claim_complete": self.source_claim_complete,
            "actor": self.actor.value,
            "actor_fingerprint": self.actor_fingerprint,
            "created_at": self.created_at.isoformat(),
            "authority": {
                "broker_write": False,
                "promotion": False,
                "risk_override": False,
                "guardian_override": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def reconstruct_strategy(
    proposal: StrategyProposal,
    *,
    candidate_spec: StrategySpecV2,
    symbols: Iterable[str],
    timeframe: str,
    rules: Iterable[ReconstructionRule],
    actor: ReconstructionActor,
    actor_fingerprint: str,
    created_at: datetime,
) -> StrategyReconstruction:
    """Validate a supplied reconstruction without inferring any missing field.

    LLMs/Carson/Dusty may *supply* hypothesis rules, but only this deterministic
    boundary labels them.  A SOURCE_DECLARED attribution must exactly match the
    archived proposal.  The proposal's unresolved list can never be erased by a
    guessed executable hypothesis.
    """

    if proposal.evidence_class is not EvidenceClass.STRATEGY_HYPOTHESIS:
        raise ValueError("only strategy-hypothesis evidence can be reconstructed as a research strategy")
    supplied = tuple(rules)
    source_rules = {name.strip().lower(): value.strip() for name, value in proposal.declared_rules}
    for row in supplied:
        if row.basis is ReconstructionRuleBasis.SOURCE_DECLARED:
            if source_rules.get(row.name) != row.value:
                raise ValueError(f"source-declared reconstruction rule does not match archived proposal: {row.name}")
    # A source rule must not silently disappear from the reconstruction record.
    attributed_source = {row.name for row in supplied if row.basis is ReconstructionRuleBasis.SOURCE_DECLARED}
    missing_source_attribution = sorted(set(source_rules) - attributed_source)
    if missing_source_attribution:
        raise ValueError("declared source rules missing reconstruction attribution: " + ",".join(missing_source_attribution))
    unresolved = tuple(sorted(value.strip().lower() for value in proposal.unresolved if value.strip()))
    return StrategyReconstruction(
        proposal.fingerprint,
        proposal.snapshot.source_id,
        proposal.snapshot.url,
        proposal.snapshot.content_sha256,
        proposal.family_fingerprint,
        proposal.title,
        tuple(symbols),
        timeframe,
        candidate_spec,
        supplied,
        unresolved,
        actor,
        actor_fingerprint,
        created_at,
    )


@dataclass(frozen=True, slots=True)
class ReconstructedResearchPackage:
    reconstruction: StrategyReconstruction
    features: FeatureConfig = FeatureConfig()
    cognition: CognitionPolicy = CognitionPolicy()

    @property
    def compiled(self) -> CompiledStrategy:
        return compile_strategy(self.reconstruction.candidate_spec)

    @property
    def fingerprint(self) -> str:
        return _digest({
            "reconstruction": self.reconstruction.fingerprint,
            "feature_numerics": FEATURE_NUMERICS_VERSION,
            "features": self.features,
            "cognition": self.cognition,
        })

    @property
    def catalog_entry(self) -> StrategyCatalogEntry:
        return StrategyCatalogEntry(
            self.reconstruction.candidate_spec.strategy_id,
            "RECONSTRUCTED RESEARCH: " + self.reconstruction.title,
            self.reconstruction.candidate_spec.strategy_hash,
            StrategyStage.BACKTEST_CANDIDATE,
            allowed_symbols=self.reconstruction.symbols,
            source_url=self.reconstruction.source_url,
            timeframe=self.reconstruction.timeframe,
        )


class SkillEvidenceKind(StrEnum):
    ROBUSTNESS = "robustness"
    FORECAST = "forecast"
    DEMO = "demo"
    EXECUTION = "execution"
    DRIFT = "drift"
    DEPENDENCY = "dependency"
    REGIME = "regime"
    SESSION = "session"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class SkillEvidenceRef:
    kind: SkillEvidenceKind
    fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SkillEvidenceKind):
            raise ValueError("skill evidence kind is invalid")
        object.__setattr__(self, "fingerprint", _sha(self.fingerprint, "skill evidence"))

    @property
    def payload(self) -> tuple[str, str]:
        return self.kind.value, self.fingerprint


@dataclass(frozen=True, slots=True)
class TradingSkill:
    """Immutable, non-authoritative projection of one frozen Champion."""

    champion_fingerprint: str
    champion_deployment_fingerprint: str
    strategy_fingerprint: str
    strategy_family: str
    source_commit: str
    lifecycle_state: ChampionLifecycleState
    symbols: tuple[str, ...]
    timeframes: tuple[str, ...]
    evidence: tuple[SkillEvidenceRef, ...]
    demo_certification_fingerprint: str | None
    created_at: datetime
    schema_version: int = 1

    def __post_init__(self) -> None:
        for field, label in (
            ("champion_fingerprint", "skill Champion"),
            ("champion_deployment_fingerprint", "skill deployment"),
            ("strategy_fingerprint", "skill strategy"),
        ):
            object.__setattr__(self, field, _sha(getattr(self, field), label))
        object.__setattr__(self, "strategy_family", _text(self.strategy_family, "skill family", maximum=128).lower())
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "skill source commit"))
        if not isinstance(self.lifecycle_state, ChampionLifecycleState):
            raise ValueError("skill lifecycle state is invalid")
        object.__setattr__(self, "symbols", _unique_text(self.symbols, "skill symbol", upper=True))
        object.__setattr__(self, "timeframes", _unique_text(self.timeframes, "skill timeframe", upper=True))
        evidence = tuple(sorted(self.evidence, key=lambda row: (row.kind.value, row.fingerprint)))
        if not evidence or len({row.fingerprint for row in evidence}) != len(evidence):
            raise ValueError("Trading Skill requires unique evidence")
        object.__setattr__(self, "evidence", evidence)
        if self.demo_certification_fingerprint is not None:
            object.__setattr__(self, "demo_certification_fingerprint", _sha(self.demo_certification_fingerprint, "Demo certification"))
        object.__setattr__(self, "created_at", _aware(self.created_at, "skill created_at"))
        if self.schema_version != 1:
            raise ValueError("unsupported Trading Skill schema version")

    @property
    def stage(self) -> StrategyStage:
        if self.lifecycle_state in {ChampionLifecycleState.RETIRED, ChampionLifecycleState.SUPERSEDED}:
            return StrategyStage.RETIRED
        if self.lifecycle_state is ChampionLifecycleState.SUSPENDED:
            return StrategyStage.RESTRICTED
        if self.demo_certification_fingerprint:
            return StrategyStage.DEMO_CERTIFIED
        return StrategyStage.BACKTEST_CERTIFIED

    @property
    def catalog_entry(self) -> StrategyCatalogEntry:
        return StrategyCatalogEntry(
            "skill-" + self.champion_fingerprint[:16],
            f"DD SKILL: {self.strategy_family} [{self.stage.value}]",
            self.strategy_fingerprint,
            self.stage,
            allowed_symbols=self.symbols,
            timeframe=",".join(self.timeframes),
        )

    @property
    def payload(self) -> dict[str, object]:
        return {
            "protocol": "dusty-m1965-trading-skill-v1",
            "schema_version": self.schema_version,
            "champion_fingerprint": self.champion_fingerprint,
            "champion_deployment_fingerprint": self.champion_deployment_fingerprint,
            "strategy_fingerprint": self.strategy_fingerprint,
            "strategy_family": self.strategy_family,
            "source_commit": self.source_commit,
            "lifecycle_state": self.lifecycle_state.value,
            "symbols": self.symbols,
            "timeframes": self.timeframes,
            "evidence": tuple(row.payload for row in self.evidence),
            "demo_certification_fingerprint": self.demo_certification_fingerprint,
            "created_at": self.created_at.isoformat(),
            "authority": {
                "broker_write": False,
                "live_write": False,
                "promotion": False,
                "risk_override": False,
                "guardian_override": False,
                "capital_allocation": False,
            },
        }

    @property
    def fingerprint(self) -> str:
        return _digest(self.payload)

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def promotion_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False


def build_trading_skill(
    champion: FrozenChampionRecord,
    *,
    lifecycle_state: ChampionLifecycleState,
    symbols: Iterable[str],
    timeframes: Iterable[str],
    evidence: Iterable[SkillEvidenceRef],
    demo_certification: SingleDeskDemoCertification | None,
    created_at: datetime,
) -> TradingSkill:
    demo_fingerprint: str | None = None
    if demo_certification is not None:
        if demo_certification.status is not SingleDeskDemoStatus.CERTIFIED:
            raise ValueError("Trading Skill Demo qualification requires an actual M194 CERTIFIED result")
        if demo_certification.champion_fingerprint != champion.fingerprint:
            raise ValueError("M194 certification belongs to a different Champion")
        if demo_certification.live_write_authorized:
            raise ValueError("M194 certification must not authorize live writes")
        demo_fingerprint = _sha(demo_certification.certification_fingerprint, "M194 certification")
    refs = tuple(evidence)
    required = {
        champion.robustness_fingerprint,
        champion.selection_evidence_fingerprint,
    }
    supplied = {row.fingerprint for row in refs}
    if not required.issubset(supplied):
        raise ValueError("Trading Skill evidence must retain Champion selection and robustness provenance")
    if champion.forecast_integration_fingerprint and champion.forecast_integration_fingerprint not in supplied:
        raise ValueError("forecast-enabled Champion skill must retain M184 provenance")
    if demo_fingerprint and demo_fingerprint not in supplied:
        raise ValueError("Demo-qualified Trading Skill must retain M194 evidence")
    return TradingSkill(
        champion.fingerprint,
        champion.deployment_fingerprint,
        champion.strategy_fingerprint,
        champion.strategy_family,
        champion.source_commit,
        lifecycle_state,
        tuple(symbols),
        tuple(timeframes),
        refs,
        demo_fingerprint,
        created_at,
    )


@dataclass(frozen=True, slots=True)
class SkillCompetency:
    """Upstream-measured context fitness; M196.5 never manufactures the score."""

    skill_fingerprint: str
    symbol: str
    timeframe: str
    session: str
    regime: str
    upstream_eligible: bool
    rank_score: float
    evidence_fingerprint: str
    evaluated_at: datetime
    valid_until: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "skill_fingerprint", _sha(self.skill_fingerprint, "competency skill"))
        object.__setattr__(self, "symbol", _text(self.symbol, "competency symbol", maximum=64).upper())
        object.__setattr__(self, "timeframe", _text(self.timeframe, "competency timeframe", maximum=32).upper())
        object.__setattr__(self, "session", _text(self.session, "competency session", maximum=64).lower())
        object.__setattr__(self, "regime", _text(self.regime, "competency regime", maximum=128).lower())
        if not isinstance(self.upstream_eligible, bool):
            raise ValueError("competency eligibility must be boolean")
        if isinstance(self.rank_score, bool) or not math.isfinite(float(self.rank_score)):
            raise ValueError("competency rank score must be finite")
        object.__setattr__(self, "rank_score", float(self.rank_score))
        object.__setattr__(self, "evidence_fingerprint", _sha(self.evidence_fingerprint, "competency evidence"))
        evaluated = _aware(self.evaluated_at, "competency evaluated_at")
        valid_until = _aware(self.valid_until, "competency valid_until")
        if valid_until <= evaluated:
            raise ValueError("competency validity must extend beyond evaluation")
        object.__setattr__(self, "evaluated_at", evaluated)
        object.__setattr__(self, "valid_until", valid_until)

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m1965-skill-competency-v1",
            self.skill_fingerprint,
            self.symbol,
            self.timeframe,
            self.session,
            self.regime,
            self.upstream_eligible,
            self.rank_score,
            self.evidence_fingerprint,
            self.evaluated_at.isoformat(),
            self.valid_until.isoformat(),
        ))


class SkillRouteStatus(StrEnum):
    SELECTED = "selected"
    NO_MATCH = "no_match"
    AMBIGUOUS = "ambiguous"
    LIVE_LOCKED = "live_locked"


@dataclass(frozen=True, slots=True)
class SkillRouteContext:
    at: datetime
    symbol: str
    timeframe: str
    session: str
    regime: str
    mode: OperatingMode
    source_commit: str
    shadow_only: bool = True

    def __post_init__(self) -> None:
        object.__setattr__(self, "at", _aware(self.at, "route time"))
        object.__setattr__(self, "symbol", _text(self.symbol, "route symbol", maximum=64).upper())
        object.__setattr__(self, "timeframe", _text(self.timeframe, "route timeframe", maximum=32).upper())
        object.__setattr__(self, "session", _text(self.session, "route session", maximum=64).lower())
        object.__setattr__(self, "regime", _text(self.regime, "route regime", maximum=128).lower())
        if not isinstance(self.mode, OperatingMode):
            raise ValueError("route mode is invalid")
        object.__setattr__(self, "source_commit", _git_sha(self.source_commit, "route source commit"))
        if not isinstance(self.shadow_only, bool):
            raise ValueError("shadow_only must be boolean")


@dataclass(frozen=True, slots=True)
class SkillRouteDecision:
    status: SkillRouteStatus
    selected_skill_fingerprint: str | None
    candidate_skill_fingerprints: tuple[str, ...]
    evidence_fingerprints: tuple[str, ...]
    reasons: tuple[str, ...]
    context_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.status, SkillRouteStatus):
            raise ValueError("route status is invalid")
        if self.selected_skill_fingerprint is not None:
            object.__setattr__(self, "selected_skill_fingerprint", _sha(self.selected_skill_fingerprint, "selected skill"))
        candidates = tuple(_sha(value, "route candidate") for value in self.candidate_skill_fingerprints)
        if len(candidates) != len(set(candidates)):
            raise ValueError("route candidates must be unique")
        object.__setattr__(self, "candidate_skill_fingerprints", candidates)
        evidence = tuple(sorted(_sha(value, "route evidence") for value in self.evidence_fingerprints))
        if len(evidence) != len(set(evidence)):
            raise ValueError("route evidence must be unique")
        object.__setattr__(self, "evidence_fingerprints", evidence)
        object.__setattr__(self, "reasons", tuple(_text(value, "route reason", maximum=256) for value in self.reasons))
        object.__setattr__(self, "context_fingerprint", _sha(self.context_fingerprint, "route context"))
        if self.status is SkillRouteStatus.SELECTED:
            if self.selected_skill_fingerprint is None or self.selected_skill_fingerprint not in candidates:
                raise ValueError("selected route must identify one candidate")
        elif self.selected_skill_fingerprint is not None:
            raise ValueError("non-selected route cannot carry a selected skill")

    @property
    def fingerprint(self) -> str:
        return _digest((
            "dusty-m1965-skill-route-decision-v1",
            self.status.value,
            self.selected_skill_fingerprint,
            self.candidate_skill_fingerprints,
            self.evidence_fingerprints,
            self.reasons,
            self.context_fingerprint,
            self.downstream_gates,
            False,
        ))

    @property
    def downstream_gates(self) -> tuple[str, ...]:
        # M197 is named but not implemented here.  Selection never implies that
        # minimum-lot/margin feasibility has passed.
        return ("Guardian", "M195_PORTFOLIO_RISK", "M196_CONCENTRATION", "M197_FEASIBILITY")

    @property
    def broker_write_authority(self) -> bool:
        return False

    @property
    def live_write_authority(self) -> bool:
        return False

    @property
    def risk_override_authority(self) -> bool:
        return False

    @property
    def guardian_override_authority(self) -> bool:
        return False

    @property
    def capital_allocation_authority(self) -> bool:
        return False


def _route_context_fingerprint(context: SkillRouteContext) -> str:
    return _digest((
        "dusty-m1965-route-context-v1",
        context.at.isoformat(),
        context.symbol,
        context.timeframe,
        context.session,
        context.regime,
        context.mode.value,
        context.source_commit,
        context.shadow_only,
    ))


def route_auto_skill(
    context: SkillRouteContext,
    skills: Iterable[TradingSkill],
    competencies: Iterable[SkillCompetency],
) -> SkillRouteDecision:
    """Deterministically rank already-eligible ACTIVE skills; never trade.

    There is deliberately no learned threshold here.  Upstream certified
    evidence says whether a competency is eligible; ``rank_score`` only orders
    surviving candidates.  Equal top scores fail closed instead of hiding a
    tie-break rule.  LIVE is shadow-only until the post-M204 transition exists.
    """

    context_fp = _route_context_fingerprint(context)
    if context.mode is OperatingMode.LIVE and not context.shadow_only:
        return SkillRouteDecision(
            SkillRouteStatus.LIVE_LOCKED,
            None,
            (),
            (),
            ("live_write_remains_false_until_post_M204_transition",),
            context_fp,
        )

    by_fp = {row.fingerprint: row for row in skills}
    if len(by_fp) != len(tuple(skills)) if isinstance(skills, tuple) else False:
        raise ValueError("duplicate Trading Skill identity")
    # Normalize generic iterables once without relying on caller re-iterability.
    skill_rows = tuple(by_fp.values())
    competency_rows = tuple(competencies)
    matches: list[tuple[float, str, SkillCompetency]] = []
    for competency in competency_rows:
        skill = by_fp.get(competency.skill_fingerprint)
        if skill is None:
            continue
        if skill.lifecycle_state is not ChampionLifecycleState.ACTIVE:
            continue
        if context.source_commit != skill.source_commit:
            continue
        if context.symbol not in skill.symbols or context.timeframe not in skill.timeframes:
            continue
        if context.mode is OperatingMode.DEMO and skill.stage is not StrategyStage.DEMO_CERTIFIED:
            continue
        if context.mode is OperatingMode.LIVE and skill.stage is not StrategyStage.DEMO_CERTIFIED:
            # M196.5 has no LIVE_ELIGIBLE creator.  Demo-certified skills may be
            # *shadow-routed* for later production evaluation only.
            continue
        if not competency.upstream_eligible:
            continue
        if competency.symbol != context.symbol or competency.timeframe != context.timeframe:
            continue
        if competency.session != context.session or competency.regime != context.regime:
            continue
        if competency.evaluated_at > context.at or context.at > competency.valid_until:
            continue
        matches.append((competency.rank_score, skill.fingerprint, competency))

    if not matches:
        return SkillRouteDecision(
            SkillRouteStatus.NO_MATCH,
            None,
            (),
            (),
            ("no_active_context_eligible_skill",),
            context_fp,
        )
    matches.sort(key=lambda row: (-row[0], row[1]))
    candidates = tuple(row[1] for row in matches)
    evidence = tuple(row[2].evidence_fingerprint for row in matches)
    top_score = matches[0][0]
    top = tuple(row for row in matches if row[0] == top_score)
    if len(top) != 1:
        return SkillRouteDecision(
            SkillRouteStatus.AMBIGUOUS,
            None,
            candidates,
            evidence,
            ("top_skill_rank_tie_requires_abstention",),
            context_fp,
        )
    return SkillRouteDecision(
        SkillRouteStatus.SELECTED,
        matches[0][1],
        candidates,
        evidence,
        ("unique_highest_upstream_eligible_skill", "downstream_governance_still_required"),
        context_fp,
    )


def strategy_catalog_projection(
    *,
    reconstructions: Iterable[StrategyReconstruction] = (),
    skills: Iterable[TradingSkill] = (),
    include_reviewed_benchmarks: bool = True,
) -> tuple[StrategyCatalogEntry, ...]:
    """Build the PC/UI catalog without turning metadata into execution authority."""

    entries: list[StrategyCatalogEntry] = []
    if include_reviewed_benchmarks:
        entries.extend(package.catalog_entry for package in reviewed_research_packages())
    entries.extend(ReconstructedResearchPackage(row).catalog_entry for row in reconstructions)
    entries.extend(row.catalog_entry for row in skills)
    by_id: dict[str, StrategyCatalogEntry] = {}
    for row in entries:
        existing = by_id.get(row.strategy_id)
        if existing is not None and existing != row:
            raise ValueError(f"strategy catalog identity collision: {row.strategy_id}")
        by_id[row.strategy_id] = row
    return tuple(sorted(by_id.values(), key=lambda row: (row.stage.value, row.title.casefold(), row.strategy_id)))


def resolve_research_package_from_library(
    entry: StrategyCatalogEntry,
    reconstructions: Iterable[StrategyReconstruction],
) -> ReviewedResearchPackage | ReconstructedResearchPackage:
    """Resolve only exact in-memory executable packages; never arbitrary catalog JSON."""

    for package in reviewed_research_packages():
        if package.catalog_entry == entry:
            return package
    for row in reconstructions:
        package = ReconstructedResearchPackage(row)
        if package.catalog_entry == entry:
            return package
    raise ValueError("strategy catalog entry has no exact executable research artifact")
