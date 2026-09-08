from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from dusty.m185_production_qualification import build_production_qualification_plan
from dusty.strategy_estate import default_strategy_estate_path, load_strategy_estate


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip() or f"git {' '.join(args)} failed")
    return completed.stdout.strip()


def _atomic_write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Build immutable M165-M174 qualification plans from the research-only Strategy Estate.")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--estate")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = args.expected_head.strip().lower()
    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise SystemExit("--expected-head must be a full 40-character Git SHA")

    actual = _git(repo, "rev-parse", "HEAD").lower()
    if actual != expected:
        raise SystemExit(f"HEAD mismatch: expected {expected}, got {actual}")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise SystemExit("repository must be clean before qualification bootstrap")

    estate_path = Path(args.estate).resolve() if args.estate else default_strategy_estate_path().resolve()
    if not estate_path.is_file():
        print(json.dumps({"protocol":"dusty-m185-production-qualification-bootstrap-v1","status":"blocked","blockers":["strategy_estate_missing"],"estate_path":str(estate_path)}, indent=2, sort_keys=True))
        return 2

    estate_sha = sha256(estate_path.read_bytes()).hexdigest()
    reconstructions = load_strategy_estate(estate_path)
    if not reconstructions:
        print(json.dumps({"protocol":"dusty-m185-production-qualification-bootstrap-v1","status":"blocked","blockers":["strategy_estate_empty"],"estate_path":str(estate_path),"estate_sha256":estate_sha}, indent=2, sort_keys=True))
        return 2

    now = datetime.now(timezone.utc)
    plan = build_production_qualification_plan(
        reconstructions,
        estate_sha256=estate_sha,
        source_commit=actual,
        created_at=now,
    )
    output = Path(args.output).resolve()
    payload = {
        "protocol": "dusty-m185-production-qualification-bootstrap-v1",
        "status": "planned",
        "source_commit": actual,
        "estate_path": str(estate_path),
        "estate_sha256": estate_sha,
        "plan_fingerprint": plan.fingerprint,
        "candidate_lane_count": len(plan.manifests),
        "manifests": [row.payload | {"manifest_fingerprint": row.fingerprint} for row in plan.manifests],
        "authority": {"broker_write": False, "promotion": False, "live_write": False},
    }
    _atomic_write_json(output, payload)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
