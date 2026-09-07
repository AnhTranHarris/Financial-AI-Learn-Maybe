from __future__ import annotations

"""Read-only native M196 Strategy Ecosystem closure and M197 handoff proof."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path

from dusty.estate_a1_refinement import materialize_plan_challengers, plan_a1_refinement
from dusty.multitimeframe_a1_campaign import A1CampaignStatus
from dusty.strategy_ecosystem_certification import (
    StrategyEcosystemCertificationStatus,
    certify_strategy_ecosystem,
)
from dusty.strategy_estate import load_strategy_estate
from dusty.trading_skills import ReconstructionActor

from validate_m19610_native import _economics, _git_head
from validate_m19613_native import _select_estate_candidate
from validate_m19614_native import _run_campaign


UTC = timezone.utc
CREATED_AT = datetime(2026, 9, 7, 20, 15, tzinfo=UTC)


def _file_sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--estate-path", required=True)
    parser.add_argument("--symbol", default="EURUSD")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    terminal = str(Path(args.terminal_path).resolve())
    estate_path = Path(args.estate_path).resolve()
    symbol = args.symbol.strip().upper()
    expected_head = args.expected_head.strip().lower()

    if _git_head(repo) != expected_head:
        raise RuntimeError("repository HEAD does not match expected M196.15 SHA")
    if not Path(terminal).is_file():
        raise RuntimeError(f"terminal missing: {terminal}")
    if not estate_path.is_file():
        raise RuntimeError(f"Strategy Estate missing: {estate_path}")

    estate_before = _file_sha256(estate_path)
    estate_rows = tuple(load_strategy_estate(estate_path))
    if len(estate_rows) < 1:
        raise RuntimeError("M196.15 requires at least one persisted Strategy Estate reconstruction")

    economics = _economics(terminal, symbol)
    parent = _select_estate_candidate(estate_path, symbol)
    if parent.actor is not ReconstructionActor.OLLAMA:
        raise RuntimeError("M196.15 native fixture must originate from governed Ollama reconstruction")

    print("\n=== M196.15 STRATEGY ECOSYSTEM ROOT ===")
    print(f"Estate records:             {len(estate_rows)}")
    print(f"Parent title:               {parent.title}")
    print(f"Parent reconstruction:      {parent.fingerprint}")
    print(f"Parent strategy hash:       {parent.candidate_spec.strategy_hash}")
    print(f"Parent actor:               {parent.actor.value}")
    print(f"Persisted timeframe:        {parent.timeframe}")

    parent_campaign, parent_runtime_trades = _run_campaign(
        parent,
        terminal=terminal,
        symbol=symbol,
        economics=economics,
        seed=19615,
        label="M196.15 PARENT",
    )

    plan = plan_a1_refinement(parent, parent_campaign, maximum_challengers=1)
    child = None
    child_campaign = None
    child_runtime_trades = 0
    if parent_campaign.status is not A1CampaignStatus.PROMISING:
        children = materialize_plan_challengers(parent, plan, created_at=CREATED_AT)
        if len(children) != 1:
            raise RuntimeError("M196.15 non-promising parent did not produce exactly one bounded child")
        child = children[0]
        child_campaign, child_runtime_trades = _run_campaign(
            child,
            terminal=terminal,
            symbol=symbol,
            economics=economics,
            seed=1961501,
            label="M196.15 CHILD",
        )

    estate_after = _file_sha256(estate_path)
    certification = certify_strategy_ecosystem(
        parent,
        parent_campaign,
        plan,
        child=child,
        child_campaign=child_campaign,
        estate_sha256_before=estate_before,
        estate_sha256_after=estate_after,
        minimum_chronological_windows=5,
    )

    print("\n=== M196.15 FINAL STRATEGY ECOSYSTEM CERTIFICATION ===")
    print(f"Certification status:       {certification.status.value}")
    print(f"Engineering handoff M197:   {certification.engineering_handoff_to_m197}")
    print(f"Strategy A1 qualified:      {certification.strategy_a1_qualified}")
    print(f"Parent campaign status:     {parent_campaign.status.value}")
    print(f"Parent runtime trades:      {parent_runtime_trades}")
    if child_campaign is not None:
        print(f"Final campaign status:      {child_campaign.status.value}")
        print(f"Final runtime trades:       {child_runtime_trades}")
    else:
        print(f"Final campaign status:      {parent_campaign.status.value}")
        print(f"Final runtime trades:       {parent_runtime_trades}")
    print(f"Estate SHA256 before:       {estate_before}")
    print(f"Estate SHA256 after:        {estate_after}")
    print(f"Certification hash:         {certification.certification_hash}")
    print(f"Blockers:                   {','.join(certification.blockers) or 'NONE'}")

    if certification.status is not StrategyEcosystemCertificationStatus.READY_FOR_M197:
        raise RuntimeError(f"M196.15 closure blocked: {','.join(certification.blockers)}")
    if not certification.engineering_handoff_to_m197:
        raise RuntimeError("M196.15 failed to authorize engineering handoff to M197")
    if estate_before != estate_after:
        raise RuntimeError("M196.15 Strategy Estate changed during native closure")
    if any(
        (
            certification.broker_write_authority,
            certification.live_write_authority,
            certification.promotion_authority,
            certification.risk_override_authority,
            certification.guardian_override_authority,
        )
    ):
        raise RuntimeError("M196.15 certification gained prohibited operational authority")

    print("\nStrategy discovery/reconstruction estate: PASS")
    print("Persisted executable StrategySpecV2:     PASS")
    print("PIT completed-bar research path:         PASS")
    print("Minimum-lot financial ledger:            PASS")
    print("A1 reliability assessment:               PASS")
    print("Five-window chronological campaign:      PASS")
    print("Bounded M158 refinement:                  PASS")
    print("Child chronological retest:              PASS" if child is not None else "Child chronological retest:              NOT REQUIRED")
    print("Estate immutability:                     PASS")
    print("Engineering handoff to M197:             AUTHORIZED")
    print("Strategy promotion implied:              NO")
    print("Broker/live/promotion authority:         NONE")
    print("Orders sent:                             NONE")
    print("\nRESULT: M196 STRATEGY ECOSYSTEM CLOSED — READY FOR M197")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
