from __future__ import annotations

"""Small in-house seed set for first Strategy Estate population.

These are Dusty-originated research hypotheses, not claimed profitable systems
and not reconstructions of third-party hidden rules.  They exist so the local
estate has meaningful EURUSD/XAUUSD/NASUSD candidates while Vibe, manually
reviewed websites, User/Carson ideas, and later Dusty research add further
StrategyProposals through the same builder.
"""

from datetime import datetime, timezone
from hashlib import sha256
import json

from .source_intake import EvidenceClass, ProposalCompleteness, SourceAccess, SourceSnapshot, StrategyProposal


_SEEDS = (
    (
        "dusty:eurusd-asian-compression-session-handoff",
        "Asian compression to London/New York expansion continuation",
        ("EURUSD",),
        ("M15",),
        ("session_handoff", "compression", "trend", "momentum"),
    ),
    (
        "dusty:eurusd-momentum-pullback",
        "Momentum pullback trend continuation",
        ("EURUSD",),
        ("M15",),
        ("momentum", "pullback", "trend"),
    ),
    (
        "dusty:xauusd-volatility-breakout",
        "Volatility expansion price-structure breakout",
        ("XAUUSD",),
        ("M15",),
        ("volatility_expansion", "breakout", "price_structure"),
    ),
    (
        "dusty:xauusd-exhaustion-reversal",
        "Momentum exhaustion reversal",
        ("XAUUSD",),
        ("M15",),
        ("momentum", "exhaustion", "reversal"),
    ),
    (
        "dusty:nasusd-trend-momentum",
        "Trend momentum continuation",
        ("NASUSD",),
        ("M15",),
        ("trend", "momentum", "continuation"),
    ),
    (
        "dusty:nasusd-range-reversal",
        "Range exhaustion reversal",
        ("NASUSD",),
        ("M15",),
        ("range", "exhaustion", "reversal"),
    ),
)


def starter_strategy_proposals() -> tuple[StrategyProposal, ...]:
    captured = datetime(1970, 1, 1, tzinfo=timezone.utc)
    proposals = []
    for proposal_id, title, symbols, timeframes, components in _SEEDS:
        source_payload = {
            "proposal_id": proposal_id,
            "title": title,
            "symbols": symbols,
            "timeframes": timeframes,
            "components": components,
        }
        content_hash = sha256(
            json.dumps(source_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        snapshot = SourceSnapshot(
            "dusty-research",
            "http://localhost/dusty/research-seed",
            captured,
            content_hash,
            SourceAccess.AUTHENTICATED_TOOL,
            True,
        )
        proposals.append(
            StrategyProposal(
                proposal_id,
                snapshot,
                EvidenceClass.STRATEGY_HYPOTHESIS,
                ProposalCompleteness.CONCEPT_ONLY,
                title,
                symbols=symbols,
                timeframes=timeframes,
                components=components,
                declared_rules=(("research_family", title),),
                unresolved=("entry_logic", "exit_logic", "risk_logic"),
                tags=("source:dusty", "research_only", "starter_estate"),
            )
        )
    return tuple(proposals)
