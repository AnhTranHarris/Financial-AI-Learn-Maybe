from __future__ import annotations

"""Source firewall and strategy-genome intake for M115-M134.

External claims are research priors only. Headline performance never grants
promotion, trade, sizing, or broker authority.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from hashlib import sha256
import json
from typing import Iterable, Mapping
from urllib.parse import urlparse

from .vibe_research_contract import VibeResearchEvidence


class SourceAccess(StrEnum):
    STRUCTURED_PUBLIC = "structured_public"
    AUTHENTICATED_TOOL = "authenticated_tool"
    MANUAL_REVIEW = "manual_review"
    NARRATIVE_ONLY = "narrative_only"


class EvidenceClass(StrEnum):
    STRUCTURED_CONTEXT = "structured_context"
    STRATEGY_HYPOTHESIS = "strategy_hypothesis"
    FORWARD_OBSERVATION = "forward_observation"
    BEHAVIORAL_CASE = "behavioral_case"
    ANTI_PATTERN = "anti_pattern"
    NARRATIVE_CONTEXT = "narrative_context"


class ProposalCompleteness(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    CONCEPT_ONLY = "concept_only"
    BEHAVIORAL_CASE = "behavioral_case"
    ANTI_PATTERN = "anti_pattern"
    UNUSABLE = "unusable"


@dataclass(frozen=True, slots=True)
class SourcePolicy:
    source_id: str
    host: str
    access: SourceAccess
    evidence_classes: tuple[EvidenceClass, ...]
    automated_acquisition_allowed: bool
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.host.strip():
            raise ValueError("source policy identity required")
        if not self.evidence_classes:
            raise ValueError("source policy evidence class required")
        if self.automated_acquisition_allowed and self.access in (
            SourceAccess.MANUAL_REVIEW,
            SourceAccess.NARRATIVE_ONLY,
        ):
            raise ValueError("manual/narrative source cannot be automatically acquired")


def default_source_policies() -> tuple[SourcePolicy, ...]:
    """Conservative initial policy; permissions can be expanded only by review."""

    return (
        SourcePolicy(
            "forexfactory-calendar",
            "forexfactory.com",
            SourceAccess.STRUCTURED_PUBLIC,
            (EvidenceClass.STRUCTURED_CONTEXT,),
            True,
            "Use documented calendar export formats; archive point-in-time snapshots.",
        ),
        SourcePolicy(
            "forexfactory-trades",
            "forexfactory.com",
            SourceAccess.MANUAL_REVIEW,
            (EvidenceClass.STRUCTURED_CONTEXT, EvidenceClass.BEHAVIORAL_CASE),
            False,
            "Crowd/positioning research only until a supported machine-access route is certified.",
        ),
        SourcePolicy(
            "myfxbook",
            "myfxbook.com",
            SourceAccess.MANUAL_REVIEW,
            (
                EvidenceClass.STRATEGY_HYPOTHESIS,
                EvidenceClass.FORWARD_OBSERVATION,
                EvidenceClass.BEHAVIORAL_CASE,
                EvidenceClass.ANTI_PATTERN,
            ),
            False,
            "Published strategy results are claims, not ground truth.",
        ),
        SourcePolicy(
            "forexcom",
            "forex.com",
            SourceAccess.NARRATIVE_ONLY,
            (EvidenceClass.STRATEGY_HYPOTHESIS, EvidenceClass.NARRATIVE_CONTEXT),
            False,
        ),
        SourcePolicy(
            "trader-dev",
            "mcp-api.trader.dev",
            SourceAccess.AUTHENTICATED_TOOL,
            (EvidenceClass.STRATEGY_HYPOTHESIS,),
            True,
            "Only through an explicitly configured authenticated connector.",
        ),
        SourcePolicy(
            "quantconnect",
            "quantconnect.com",
            SourceAccess.NARRATIVE_ONLY,
            (EvidenceClass.STRATEGY_HYPOTHESIS, EvidenceClass.NARRATIVE_CONTEXT),
            False,
        ),
        SourcePolicy(
            "stonehillforex",
            "stonehillforex.com",
            SourceAccess.MANUAL_REVIEW,
            (EvidenceClass.STRATEGY_HYPOTHESIS, EvidenceClass.NARRATIVE_CONTEXT),
            False,
        ),
        SourcePolicy(
            "tradingview",
            "tradingview.com",
            SourceAccess.MANUAL_REVIEW,
            (EvidenceClass.STRATEGY_HYPOTHESIS, EvidenceClass.NARRATIVE_CONTEXT),
            False,
            "Concept/licensing review required; no unsupported scraping.",
        ),
        SourcePolicy(
            "investopedia",
            "investopedia.com",
            SourceAccess.NARRATIVE_ONLY,
            (EvidenceClass.NARRATIVE_CONTEXT,),
            False,
        ),
        SourcePolicy(
            "motley-fool",
            "fool.com",
            SourceAccess.NARRATIVE_ONLY,
            (EvidenceClass.NARRATIVE_CONTEXT,),
            False,
        ),
        SourcePolicy(
            "quantpedia",
            "quantpedia.com",
            SourceAccess.MANUAL_REVIEW,
            (EvidenceClass.STRATEGY_HYPOTHESIS, EvidenceClass.NARRATIVE_CONTEXT),
            False,
        ),
        SourcePolicy(
            "vibe-trading",
            "localhost",
            SourceAccess.AUTHENTICATED_TOOL,
            (EvidenceClass.STRATEGY_HYPOTHESIS,),
            True,
            "Local allowlisted research contractor only.",
        ),
    )


def canonical_json(payload: object) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def digest(payload: object) -> str:
    return sha256(canonical_json(payload).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class SourceSnapshot:
    source_id: str
    url: str
    captured_at: datetime
    content_sha256: str
    access: SourceAccess
    automated: bool

    def __post_init__(self) -> None:
        if not self.source_id.strip() or not self.url.strip():
            raise ValueError("source snapshot identity required")
        if self.captured_at.tzinfo is None or self.captured_at.utcoffset() is None:
            raise ValueError("source snapshot capture time must be timezone-aware")
        if len(self.content_sha256) != 64:
            raise ValueError("source snapshot requires SHA-256 content identity")


def _host_matches(url: str, policy_host: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    policy_host = policy_host.lower()
    if policy_host == "localhost":
        return host in {"localhost", "127.0.0.1", "::1"}
    return host == policy_host or host.endswith("." + policy_host)


def make_snapshot(
    policy: SourcePolicy,
    *,
    url: str,
    captured_at: datetime,
    content: str,
    automated: bool,
) -> SourceSnapshot:
    if not _host_matches(url, policy.host):
        raise ValueError("source URL does not match policy host")
    if automated and not policy.automated_acquisition_allowed:
        raise PermissionError("source policy blocks automated acquisition")
    return SourceSnapshot(
        policy.source_id,
        url,
        captured_at,
        sha256(content.encode("utf-8")).hexdigest(),
        policy.access,
        automated,
    )


@dataclass(frozen=True, slots=True)
class StrategyProposal:
    proposal_id: str
    snapshot: SourceSnapshot
    evidence_class: EvidenceClass
    completeness: ProposalCompleteness
    title: str
    symbols: tuple[str, ...] = ()
    timeframes: tuple[str, ...] = ()
    components: tuple[str, ...] = ()
    declared_rules: tuple[tuple[str, str], ...] = ()
    unresolved: tuple[str, ...] = ()
    claimed_performance: tuple[tuple[str, str], ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.proposal_id.strip() or not self.title.strip():
            raise ValueError("strategy proposal identity required")
        if self.evidence_class not in (
            EvidenceClass.STRATEGY_HYPOTHESIS,
            EvidenceClass.FORWARD_OBSERVATION,
            EvidenceClass.BEHAVIORAL_CASE,
            EvidenceClass.ANTI_PATTERN,
        ):
            raise ValueError("proposal must be strategy/case evidence")
        for values in (self.symbols, self.timeframes, self.components, self.unresolved, self.tags):
            if len(set(values)) != len(values):
                raise ValueError("proposal tuple values must be unique")
        if len({name for name, _ in self.declared_rules}) != len(self.declared_rules):
            raise ValueError("declared rule names must be unique")

    @property
    def fingerprint(self) -> str:
        return digest(
            {
                "proposal_id": self.proposal_id,
                "source": self.snapshot.content_sha256,
                "evidence_class": self.evidence_class.value,
                "completeness": self.completeness.value,
                "title": self.title,
                "symbols": sorted(self.symbols),
                "timeframes": sorted(self.timeframes),
                "components": sorted(self.components),
                "declared_rules": sorted(self.declared_rules),
                "unresolved": sorted(self.unresolved),
                "claimed_performance": sorted(self.claimed_performance),
                "tags": sorted(self.tags),
            }
        )

    @property
    def family_fingerprint(self) -> str:
        """Deduplicate research genetics without trusting marketing performance."""

        return digest(
            {
                "symbols": sorted(value.upper() for value in self.symbols),
                "timeframes": sorted(value.upper() for value in self.timeframes),
                "components": sorted(value.lower() for value in self.components),
                "declared_rules": sorted((name.lower(), value.lower()) for name, value in self.declared_rules),
                "tags": sorted(value.lower() for value in self.tags if not value.startswith("source:")),
            }
        )


def proposal_priority_key(proposal: StrategyProposal) -> tuple[int, int, int, str]:
    """Research ordering never uses claimed return, win rate, or headline profit."""

    completeness = {
        ProposalCompleteness.COMPLETE: 0,
        ProposalCompleteness.PARTIAL: 1,
        ProposalCompleteness.CONCEPT_ONLY: 2,
        ProposalCompleteness.BEHAVIORAL_CASE: 3,
        ProposalCompleteness.ANTI_PATTERN: 4,
        ProposalCompleteness.UNUSABLE: 5,
    }[proposal.completeness]
    evidence = {
        EvidenceClass.FORWARD_OBSERVATION: 0,
        EvidenceClass.STRATEGY_HYPOTHESIS: 1,
        EvidenceClass.BEHAVIORAL_CASE: 2,
        EvidenceClass.ANTI_PATTERN: 3,
    }[proposal.evidence_class]
    return completeness, evidence, len(proposal.unresolved), proposal.family_fingerprint


def deduplicate_proposals(proposals: Iterable[StrategyProposal]) -> tuple[StrategyProposal, ...]:
    """Keep the most research-ready representative of each strategy family."""

    selected: dict[str, StrategyProposal] = {}
    for proposal in proposals:
        current = selected.get(proposal.family_fingerprint)
        if current is None or proposal_priority_key(proposal) < proposal_priority_key(current):
            selected[proposal.family_fingerprint] = proposal
    return tuple(sorted(selected.values(), key=proposal_priority_key))


def _vibe_snapshot(evidence: VibeResearchEvidence) -> SourceSnapshot:
    # Vibe's existing evidence does not carry a capture timestamp. Use a stable
    # sentinel instead of inventing nondeterministic wall-clock provenance.
    return SourceSnapshot(
        source_id="vibe-trading",
        url="http://localhost/vibe-trading/research",
        captured_at=datetime(1970, 1, 1, tzinfo=timezone.utc),
        content_sha256=evidence.response_sha256,
        access=SourceAccess.AUTHENTICATED_TOOL,
        automated=True,
    )


def _json_result(text: str) -> Mapping[str, object]:
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("Vibe strategy result must be a JSON object")
    if raw.get("status") != "ok":
        raise ValueError("Vibe strategy result is not successful")
    result = raw.get("result")
    if not isinstance(result, dict):
        raise ValueError("Vibe strategy result payload missing")
    return result


def proposals_from_vibe(evidence: VibeResearchEvidence) -> tuple[StrategyProposal, ...]:
    """Translate Vibe catalog/evidence output into hypothesis-only proposals."""

    if evidence.tool not in {"alpha_zoo", "list_strategies", "query_strategies", "get_strategy_evidence"}:
        raise ValueError("Vibe evidence does not represent a strategy surface")
    result = _json_result(evidence.result_text)
    items = result.get("items")
    if items is None:
        items = [result]
    if not isinstance(items, list):
        raise ValueError("Vibe strategy items must be a list")
    snapshot = _vibe_snapshot(evidence)
    proposals: list[StrategyProposal] = []
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        source_id = str(item.get("id") or item.get("strategy_id") or f"item-{index}")
        title = str(item.get("nickname") or item.get("name") or source_id)
        columns = item.get("columns_required") or []
        themes = item.get("theme") or []
        timeframes = item.get("frequency") or []
        if isinstance(themes, str):
            themes = [themes]
        if isinstance(timeframes, str):
            timeframes = [timeframes]
        components = tuple(
            sorted(
                {
                    *(str(value) for value in columns if str(value).strip()),
                    *(str(value) for value in themes if str(value).strip()),
                }
            )
        )
        formula = item.get("formula_latex") or item.get("formula")
        declared = (("formula", str(formula)),) if formula else ()
        proposals.append(
            StrategyProposal(
                proposal_id=f"vibe:{source_id}",
                snapshot=snapshot,
                evidence_class=EvidenceClass.STRATEGY_HYPOTHESIS,
                completeness=ProposalCompleteness.CONCEPT_ONLY,
                title=title,
                timeframes=tuple(sorted(str(value) for value in timeframes if str(value).strip())),
                components=components,
                declared_rules=declared,
                unresolved=("entry_logic", "exit_logic", "risk_logic"),
                tags=("source:vibe", "research_only"),
            )
        )
    return tuple(proposals)
