from __future__ import annotations

"""M196.14 bounded A1 refinement for Strategy Estate reconstructions.

The Strategy Estate stores immutable StrategySpecV2 research reconstructions,
while M158/M160 evolution operates on StrategyGenome identities.  This adapter
creates a deterministic genome view only from already-typed reconstruction
fields.  Source-declared rules are LOCKED, Ollama/Dusty hypothesis rules are
RESEARCHABLE, permanent safety constraints remain FORBIDDEN, and no natural
language is interpreted here.

A failed or insufficient A1 campaign may produce at most a caller-bounded set
of one-variable M158 challengers.  The current planner changes only numeric
thresholds of hypothesis entry clauses, because those fields have an exact
round-trip identity in the M196.5 reconstruction protocol.  Successful A1
campaigns advance without mutation.  Infrastructure failures are not modeled
here and must remain exact retries in the M160 governor.
"""

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import math
from typing import Iterable

from .controlled_evolution import (
    ChallengerCandidate,
    EvolutionAction,
    EvolutionDecision,
    ExperimentOutcome,
    ExperimentOutcomeType,
    MutationInstruction,
    decide_evolution,
)
from .feature_registry import FeatureRegistry, standard_feature_registry
from .multitimeframe_a1_campaign import A1CampaignAssessment, A1CampaignStatus
from .research import Clause, RuleOp
from .strategy_genome_v2 import (
    ClauseKind,
    ClauseResolution,
    CompiledStrategyGenomeV2,
    GenomeClauseSpec,
    compile_strategy_genome_v2,
)
from .strategy_ir import RuleGroup, StrategySpecV2
from .strategy_lab import (
    PERMANENT_FORBIDDEN,
    ConstraintMode,
    StrategyConstraint,
    StrategyGenome,
    StrategyOrigin,
)
from .trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
)


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _entry_rule_value(clause: Clause) -> str:
    return f"{clause.feature} {clause.op.value} {clause.value!r}"


def _constraint_rows(reconstruction: StrategyReconstruction) -> tuple[StrategyConstraint, ...]:
    constraints: list[StrategyConstraint] = []
    seen: set[str] = set()
    for rule in reconstruction.rules:
        key = rule.name.lower()
        if key in seen:
            raise ValueError("M196.14 reconstruction rule names must be unique across provenance bases")
        seen.add(key)
        mode = (
            ConstraintMode.LOCKED
            if rule.basis is ReconstructionRuleBasis.SOURCE_DECLARED
            else ConstraintMode.RESEARCHABLE
        )
        constraints.append(StrategyConstraint(key, rule.value, mode))
    for unresolved in reconstruction.unresolved_source_rules:
        key = unresolved.lower()
        if not key.startswith("unresolved."):
            key = f"unresolved.{key}"
        if key in seen:
            continue
        seen.add(key)
        constraints.append(StrategyConstraint(key, "research_required", ConstraintMode.RESEARCHABLE))
    for row in PERMANENT_FORBIDDEN:
        key = row.key.lower()
        if key in seen:
            existing = next(item for item in constraints if item.key.lower() == key)
            if existing.mode is not ConstraintMode.FORBIDDEN:
                raise ValueError("M196.14 reconstruction collides with permanent forbidden constraint")
            continue
        seen.add(key)
        constraints.append(row)
    return tuple(sorted(constraints, key=lambda row: row.key.lower()))


def reconstruction_genome(reconstruction: StrategyReconstruction) -> StrategyGenome:
    """Create an immutable M143 genome view without guessing reconstruction semantics."""

    rules = tuple((row.name.lower(), row.value) for row in reconstruction.rules)
    rule_map = dict(rules)
    if len(rule_map) != len(rules):
        raise ValueError("M196.14 reconstruction rule identity collision")

    spec = reconstruction.candidate_spec
    for group_index, group in enumerate(spec.entry_groups):
        for clause_index, clause in enumerate(group.clauses):
            key = f"hypothesis.entry.{group_index}.{clause_index}"
            if rule_map.get(key) != _entry_rule_value(clause):
                raise ValueError(f"M196.14 typed entry rule provenance mismatch: {key}")

    expected = {
        "hypothesis.direction": spec.direction.value,
        "hypothesis.stop": spec.exit_plan.stop_rule,
        "hypothesis.target": spec.exit_plan.target_rule or "off",
        "hypothesis.trailing": spec.exit_plan.trailing_rule or "off",
        "hypothesis.horizon_minutes": str(spec.intended_horizon_minutes),
    }
    for key, value in expected.items():
        if rule_map.get(key) != value:
            raise ValueError(f"M196.14 typed reconstruction provenance mismatch: {key}")

    components = tuple(
        sorted(
            {
                key.split(".", 2)[1] if key.startswith("hypothesis.") and "." in key else key.split(".", 1)[0]
                for key in rule_map
            }
        )
    )
    return StrategyGenome(
        genome_id=f"estate:{reconstruction.fingerprint[:20]}",
        origin=StrategyOrigin.EXTERNAL,
        title=reconstruction.title,
        source_fingerprint=reconstruction.fingerprint,
        parent_fingerprints=(),
        symbols=reconstruction.symbols,
        timeframes=(reconstruction.timeframe,),
        components=components,
        rules=tuple(sorted(rules)),
        unresolved=reconstruction.unresolved_source_rules,
        constraints=_constraint_rows(reconstruction),
        generation=0,
    )


def _registry_key(feature: str) -> str:
    value = feature.strip().lower()
    aliases = {
        "open": "open@v1",
        "high": "high@v1",
        "low": "low@v1",
        "close": "close@v1",
        "spread_points": "spread_points@v1",
        "tick_volume": "tick_volume@v1",
        "return_1": "return_1@v1",
        "sma": "sma_20@v1",
        "sma_20": "sma_20@v1",
        "ema": "ema_20@v1",
        "ema_20": "ema_20@v1",
        "atr": "atr_14@v1",
        "atr_14": "atr_14@v1",
        "rsi": "rsi_14@v1",
        "rsi_14": "rsi_14@v1",
    }
    try:
        return aliases[value]
    except KeyError as exc:
        raise ValueError(f"M196.14 feature lacks deterministic M156 binding: {feature}") from exc


def _price_features(rule: str) -> tuple[str, ...]:
    raw = rule.strip().lower()
    return ("atr_14@v1",) if raw.startswith("atr:") else ()


def compile_reconstruction_genome(
    reconstruction: StrategyReconstruction,
    *,
    registry: FeatureRegistry | None = None,
) -> tuple[StrategyGenome, CompiledStrategyGenomeV2]:
    genome = reconstruction_genome(reconstruction)
    chosen = registry or standard_feature_registry()
    specs: list[GenomeClauseSpec] = []
    for group_index, group in enumerate(reconstruction.candidate_spec.entry_groups):
        for clause_index, clause in enumerate(group.clauses):
            key = f"hypothesis.entry.{group_index}.{clause_index}"
            specs.append(
                GenomeClauseSpec(
                    clause_id=f"trigger.{group_index}.{clause_index}",
                    kind=ClauseKind.TRIGGER,
                    source_key=key,
                    resolution=ClauseResolution.RESOLVED,
                    value=_entry_rule_value(clause),
                    feature_keys=(_registry_key(clause.feature),),
                    parameters=(("operator", clause.op.value), ("group_mode", group.mode.value)),
                )
            )
    specs.append(
        GenomeClauseSpec(
            clause_id="exit.target",
            kind=ClauseKind.EXIT,
            source_key="hypothesis.target",
            resolution=ClauseResolution.RESOLVED,
            value=reconstruction.candidate_spec.exit_plan.target_rule or "off",
            feature_keys=_price_features(reconstruction.candidate_spec.exit_plan.target_rule),
        )
    )
    specs.append(
        GenomeClauseSpec(
            clause_id="risk.stop",
            kind=ClauseKind.RISK,
            source_key="hypothesis.stop",
            resolution=ClauseResolution.RESOLVED,
            value=reconstruction.candidate_spec.exit_plan.stop_rule,
            feature_keys=_price_features(reconstruction.candidate_spec.exit_plan.stop_rule),
        )
    )
    return genome, compile_strategy_genome_v2(genome, tuple(specs), chosen)


def _threshold_instruction(clause: Clause, key: str, *, relax: bool, fraction: float) -> MutationInstruction | None:
    if clause.op not in {RuleOp.GT, RuleOp.GE, RuleOp.LT, RuleOp.LE}:
        return None
    if isinstance(clause.value, bool) or not isinstance(clause.value, (int, float)):
        return None
    value = float(clause.value)
    if not math.isfinite(value):
        return None
    step = max(abs(value) * fraction, 1e-6)
    if clause.op in {RuleOp.GT, RuleOp.GE}:
        candidate = value - step if relax else value + step
    else:
        candidate = value + step if relax else value - step
    if not math.isfinite(candidate):
        return None
    rendered = f"{clause.feature} {clause.op.value} {candidate!r}"
    rationale = (
        "A1 evidence is trade-count insufficient; relax one hypothesis entry threshold"
        if relax
        else "A1 evidence has sufficient activity but failed edge; tighten one hypothesis entry threshold"
    )
    return MutationInstruction(key, rendered, rationale)


def _instruction_groups(
    reconstruction: StrategyReconstruction,
    campaign: A1CampaignAssessment,
    *,
    maximum_challengers: int,
) -> tuple[tuple[MutationInstruction, ...], ...]:
    if maximum_challengers < 1:
        raise ValueError("M196.14 maximum_challengers must be positive")
    relax = (
        campaign.status is A1CampaignStatus.INSUFFICIENT
        or "insufficient_total_trades" in campaign.blockers
        or "underlying_window_insufficient" in campaign.blockers
    )
    groups: list[tuple[MutationInstruction, ...]] = []
    for fraction in (0.05, 0.10):
        for group_index, group in enumerate(reconstruction.candidate_spec.entry_groups):
            for clause_index, clause in enumerate(group.clauses):
                key = f"hypothesis.entry.{group_index}.{clause_index}"
                instruction = _threshold_instruction(clause, key, relax=relax, fraction=fraction)
                if instruction is None:
                    continue
                groups.append((instruction,))
                if len(groups) >= maximum_challengers:
                    return tuple(groups)
    return tuple(groups)


@dataclass(frozen=True, slots=True)
class EstateA1RefinementPlan:
    reconstruction_fingerprint: str
    campaign_fingerprint: str
    genome_fingerprint: str
    compiled_genome_fingerprint: str
    outcome_fingerprint: str
    evolution: EvolutionDecision

    @property
    def fingerprint(self) -> str:
        return _digest(
            {
                "protocol": "dusty-m19614-estate-a1-refinement-v1",
                "reconstruction": self.reconstruction_fingerprint,
                "campaign": self.campaign_fingerprint,
                "genome": self.genome_fingerprint,
                "compiled": self.compiled_genome_fingerprint,
                "outcome": self.outcome_fingerprint,
                "action": self.evolution.action.value,
                "challengers": tuple(row.compiled_genome.execution_fingerprint for row in self.evolution.challengers),
            }
        )

    broker_write_authority = False
    live_write_authority = False
    promotion_authority = False
    risk_override_authority = False
    guardian_override_authority = False


def plan_a1_refinement(
    reconstruction: StrategyReconstruction,
    campaign: A1CampaignAssessment,
    *,
    maximum_challengers: int = 2,
) -> EstateA1RefinementPlan:
    if campaign.strategy_hash != reconstruction.candidate_spec.strategy_hash:
        raise ValueError("M196.14 A1 campaign does not bind Estate candidate strategy hash")
    genome, compiled = compile_reconstruction_genome(reconstruction)
    passed = campaign.status is A1CampaignStatus.PROMISING
    outcome = ExperimentOutcome(
        genome.fingerprint,
        ExperimentOutcomeType.PASSED if passed else ExperimentOutcomeType.RESEARCH_FAILED,
        "A1 chronological campaign promising" if passed else "A1 chronological campaign requires bounded refinement",
        (campaign.fingerprint,),
    )
    groups = () if passed else _instruction_groups(
        reconstruction,
        campaign,
        maximum_challengers=maximum_challengers,
    )
    evolution = decide_evolution(
        genome,
        compiled,
        standard_feature_registry(),
        outcome,
        candidate_instructions=groups,
        maximum_challengers=maximum_challengers,
    )
    return EstateA1RefinementPlan(
        reconstruction.fingerprint,
        campaign.fingerprint,
        genome.fingerprint,
        compiled.fingerprint,
        outcome.fingerprint,
        evolution,
    )


def _parse_threshold_rule(text: str, expected: Clause) -> Clause:
    parts = text.strip().split()
    if len(parts) != 3:
        raise ValueError("M196.14 challenger entry rule must remain feature op numeric-threshold")
    feature, op_text, raw_value = parts
    if feature != expected.feature or op_text != expected.op.value:
        raise ValueError("M196.14 challenger attempted to change entry feature/operator outside bounded plan")
    try:
        value = float(raw_value)
    except ValueError as exc:
        raise ValueError("M196.14 challenger threshold is not numeric") from exc
    if not math.isfinite(value):
        raise ValueError("M196.14 challenger threshold must be finite")
    return Clause(feature, expected.op, value)


def materialize_a1_challenger(
    parent: StrategyReconstruction,
    challenger: ChallengerCandidate,
    *,
    created_at: datetime,
) -> StrategyReconstruction:
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise ValueError("M196.14 challenger created_at must be timezone-aware")
    parent_genome, _ = compile_reconstruction_genome(parent)
    if challenger.parent_genome_fingerprint != parent_genome.fingerprint:
        raise ValueError("M196.14 Challenger does not descend from Estate reconstruction genome")
    child_rules = dict(challenger.source_genome.rules)
    spec = parent.candidate_spec
    groups: list[RuleGroup] = []
    for group_index, group in enumerate(spec.entry_groups):
        clauses: list[Clause] = []
        for clause_index, clause in enumerate(group.clauses):
            key = f"hypothesis.entry.{group_index}.{clause_index}"
            if key not in child_rules:
                raise ValueError(f"M196.14 Challenger lost typed rule: {key}")
            clauses.append(_parse_threshold_rule(child_rules[key], clause))
        groups.append(RuleGroup(tuple(clauses), group.mode))

    child_spec = replace(
        spec,
        strategy_id=f"m19614-{spec.strategy_id[:48]}-{challenger.mutation_fingerprint[:10]}",
        entry_groups=tuple(groups),
    )
    if child_spec.strategy_hash == spec.strategy_hash:
        raise ValueError("M196.14 Challenger did not change executable strategy identity")

    rebuilt_rules: list[ReconstructionRule] = []
    for row in parent.rules:
        if row.basis is ReconstructionRuleBasis.SOURCE_DECLARED:
            rebuilt_rules.append(row)
            continue
        if row.name not in child_rules:
            raise ValueError(f"M196.14 Challenger lost hypothesis provenance: {row.name}")
        rebuilt_rules.append(ReconstructionRule(row.name, child_rules[row.name], row.basis))

    actor_fp = _digest(
        {
            "protocol": "dusty-m19614-challenger-materialization-v1",
            "parent_reconstruction": parent.fingerprint,
            "challenger": challenger.source_genome.fingerprint,
            "mutation": challenger.mutation_fingerprint,
            "outcome": challenger.outcome_fingerprint,
        }
    )
    return StrategyReconstruction(
        parent.proposal_fingerprint,
        parent.source_id,
        parent.source_url,
        parent.source_content_sha256,
        parent.source_family_fingerprint,
        f"{parent.title} — A1 bounded refinement",
        parent.symbols,
        parent.timeframe,
        child_spec,
        tuple(rebuilt_rules),
        parent.unresolved_source_rules,
        ReconstructionActor.DUSTY_RESEARCH,
        actor_fp,
        created_at.astimezone(timezone.utc),
    )


def materialize_plan_challengers(
    reconstruction: StrategyReconstruction,
    plan: EstateA1RefinementPlan,
    *,
    created_at: datetime,
) -> tuple[StrategyReconstruction, ...]:
    if plan.reconstruction_fingerprint != reconstruction.fingerprint:
        raise ValueError("M196.14 refinement plan/reconstruction identity drift")
    if plan.evolution.action is not EvolutionAction.CREATE_CHALLENGER:
        return ()
    return tuple(
        materialize_a1_challenger(reconstruction, row, created_at=created_at)
        for row in plan.evolution.challengers
    )
