from __future__ import annotations

"""Harvest an already-completed M165 round trip without creating broker effects."""

import argparse
import json
from pathlib import Path
import subprocess
import sys

from dusty.m165_observation_custody import M165ObservationCustody


PROTOCOL = "dusty-m165-completed-roundtrip-harvest-v1"


def _run(command: list[str], *, cwd: Path) -> int:
    return int(subprocess.run(command, cwd=cwd, check=False).returncode)


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Harvest preserved M165 broker evidence with zero broker-write authority")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--forensics", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--expected-start-count", type=int, required=True)
    parser.add_argument("--expected-final-count", type=int, required=True)
    parser.add_argument("--expected-distinct-days", type=int, required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = args.expected_head.strip().lower()
    receipt = Path(args.receipt).resolve()
    forensics = Path(args.forensics).resolve()
    database = Path(args.database).resolve()
    output_root = Path(args.output_root).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    observations = output_root / "observations.json"
    custody_summary = output_root / "custody-summary.json"
    final_summary = output_root / "harvest-summary.json"

    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")
    for path in (receipt, forensics, database):
        if not path.is_file():
            raise FileNotFoundError(path)

    with M165ObservationCustody(database) as store:
        before = store.summary_payload()
    if int(before["observation_count"]) != args.expected_start_count:
        raise RuntimeError("unexpected starting M165 custody count")
    if int(before["distinct_days"]) != args.expected_distinct_days:
        raise RuntimeError("unexpected starting M165 distinct-day count")

    extractor = repo / "tools" / "extract_m165_native_observations.py"
    importer = repo / "tools" / "import_m165_observations.py"

    extract_exit = _run([
        sys.executable,
        str(extractor),
        "--terminal-path", str(Path(args.terminal_path).resolve()),
        "--receipt", str(receipt),
        "--forensics", str(forensics),
        "--output", str(observations),
    ], cwd=repo)
    if extract_exit != 0 or not observations.is_file():
        raise RuntimeError(f"native observation extraction failed; exit={extract_exit}")

    payload = json.loads(observations.read_text(encoding="utf-8"))
    rows = payload.get("observations", [])
    if not isinstance(rows, list) or len(rows) != 2:
        raise RuntimeError("harvest requires exactly two extracted observations")
    if set(str(row.get("side", "")) for row in rows if isinstance(row, dict)) != {"buy", "sell"}:
        raise RuntimeError("harvest requires one BUY and one SELL observation")

    import_exit = _run([
        sys.executable,
        str(importer),
        "--input", str(observations),
        "--database", str(database),
        "--summary", str(custody_summary),
    ], cwd=repo)
    if import_exit != 0 or not custody_summary.is_file():
        raise RuntimeError(f"custody import failed; exit={import_exit}")

    after = json.loads(custody_summary.read_text(encoding="utf-8"))
    if int(after["observation_count"]) != args.expected_final_count:
        raise RuntimeError("harvest did not reach expected final observation count")
    if int(after["distinct_days"]) != args.expected_distinct_days:
        raise RuntimeError("harvest changed distinct-day count unexpectedly")
    last_import = after.get("last_import", {})
    if int(last_import.get("inserted", -1)) != args.expected_final_count - args.expected_start_count:
        raise RuntimeError("harvest inserted unexpected observation count")
    if int(last_import.get("duplicates", -1)) != 0:
        raise RuntimeError("harvest unexpectedly encountered duplicate observations")

    result = {
        "protocol": PROTOCOL,
        "status": "completed",
        "source_commit": expected,
        "authority": {
            "broker_write": False,
            "live_write": False,
            "new_entry": False,
            "recovery_close": False,
            "retry": False,
            "promotion": False,
        },
        "receipt": str(receipt),
        "forensics": str(forensics),
        "observations": str(observations),
        "custody_summary": str(custody_summary),
        "starting_observation_count": args.expected_start_count,
        "final_observation_count": args.expected_final_count,
        "distinct_days": args.expected_distinct_days,
    }
    final_summary.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
