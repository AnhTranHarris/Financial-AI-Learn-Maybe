from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import subprocess

from dusty.m165_native_history_discovery import discover_native_history
from dusty.m185_production_qualification import (
    ProductionQualificationManifest,
    ProductionQualificationPlan,
)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed with exit {proc.returncode}")
    return proc.stdout.strip()


def _load_verified_plan(path: Path) -> tuple[ProductionQualificationPlan, str]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("protocol") != "dusty-m185-production-qualification-bootstrap-v1":
        raise ValueError("qualification plan protocol mismatch")
    rows = payload.get("manifests")
    if not isinstance(rows, list) or not rows:
        raise ValueError("qualification plan has no candidate manifests")

    manifests: list[ProductionQualificationManifest] = []
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError("qualification manifest must be an object")
        manifest = ProductionQualificationManifest(
            source_commit=raw["source_commit"],
            estate_sha256=raw["estate_sha256"],
            reconstruction_fingerprint=raw["reconstruction_fingerprint"],
            proposal_fingerprint=raw["proposal_fingerprint"],
            strategy_hash=raw["strategy_hash"],
            source_id=raw["source_id"],
            source_url=raw["source_url"],
            source_content_sha256=raw["source_content_sha256"],
            source_family_fingerprint=raw["source_family_fingerprint"],
            title=raw["title"],
            symbol=raw["symbol"],
            timeframe=raw["timeframe"],
            lane_id=raw["lane_id"],
            source_claim_complete=bool(raw["source_claim_complete"]),
            hypothesis_rule_count=raw["hypothesis_rule_count"],
            required_stages=tuple(raw["required_stages"]),
            created_at=datetime.fromisoformat(raw["created_at"]),
            schema_version=raw.get("schema_version", 1),
        )
        if raw.get("manifest_fingerprint") != manifest.fingerprint:
            raise ValueError("qualification manifest fingerprint mismatch")
        manifests.append(manifest)

    source_commit = str(payload.get("source_commit", "")).strip().lower()
    estate_sha = str(payload.get("estate_sha256", "")).strip().lower()
    created_values = {row.created_at for row in manifests}
    if len(created_values) != 1:
        raise ValueError("qualification plan manifests must share one created_at")
    plan = ProductionQualificationPlan(
        source_commit=source_commit,
        estate_sha256=estate_sha,
        manifests=tuple(manifests),
        created_at=next(iter(created_values)),
    )
    expected_fp = str(payload.get("plan_fingerprint", "")).strip().lower()
    if expected_fp != plan.fingerprint:
        raise ValueError("qualification plan fingerprint mismatch")
    if payload.get("candidate_lane_count") != len(plan.manifests):
        raise ValueError("qualification plan candidate count mismatch")
    if payload.get("status") != "planned":
        raise ValueError("qualification plan status mismatch")
    return plan, expected_fp


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only M165 native history discovery")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--output")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = args.expected_head.strip().lower()
    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires full 40-character Git SHA")
    actual = _git(repo, "rev-parse", "HEAD").lower()
    if actual != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    plan, plan_fingerprint = _load_verified_plan(Path(args.qualification_plan).resolve())
    symbols = tuple(sorted({row.symbol for row in plan.manifests}))

    import MetaTrader5 as mt5

    initialized = False
    try:
        if not mt5.initialize(path=str(Path(args.terminal_path).resolve())):
            raise RuntimeError("MetaTrader5 initialize failed")
        initialized = True
        report = discover_native_history(
            mt5,
            symbols=symbols,
            captured_at=datetime.now(timezone.utc),
            lookback_days=args.lookback_days,
        )
        payload = dict(report.payload)
        payload["fingerprint"] = report.fingerprint
        payload["source_commit"] = expected
        payload["qualification_plan_fingerprint"] = plan_fingerprint
        payload["qualification_plan_source_commit"] = plan.source_commit
        rendered = json.dumps(payload, indent=2, sort_keys=True)
        print(rendered)
        if args.output:
            destination = Path(args.output).resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(rendered + "\n", encoding="utf-8")
        return 0 if report.demo_account and report.connected else 2
    finally:
        if initialized:
            mt5.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
