from __future__ import annotations

"""Guard the M165 day controller with read-only native/session eligibility."""

import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile


PROTOCOL = "dusty-m165-calibration-day-guarded-runner-v1"


def _run(command: list[str], *, cwd: Path) -> int:
    return int(subprocess.run(command, cwd=cwd, check=False).returncode)


def main() -> int:
    parser = argparse.ArgumentParser(description="Session-guarded M165 calibration day runner")
    parser.add_argument("--day", required=True, type=int, choices=(2, 3))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--custody-root", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--confirm-demo-day", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    eligibility_tool = repo / "tools" / "check_m165_calibration_day_eligibility.py"
    controller = repo / "tools" / "run_m165_calibration_day.py"
    if not eligibility_tool.is_file() or not controller.is_file():
        raise FileNotFoundError("guarded runner dependencies missing")

    with tempfile.TemporaryDirectory(prefix="dusty-m165-session-gate-") as temp_dir:
        eligibility_path = Path(temp_dir) / "eligibility.json"
        eligibility_exit = _run(
            [
                sys.executable,
                str(eligibility_tool),
                "--day", str(args.day),
                "--repo", str(repo),
                "--expected-head", args.expected_head,
                "--terminal-path", str(Path(args.terminal_path).resolve()),
                "--database", str(Path(args.database).resolve()),
                "--output", str(eligibility_path),
            ],
            cwd=repo,
        )
        if eligibility_exit != 0 or not eligibility_path.is_file():
            raise RuntimeError(f"M165 eligibility probe failed; exit={eligibility_exit}")
        eligibility = json.loads(eligibility_path.read_text(encoding="utf-8"))
        if eligibility.get("eligible") is not True or eligibility.get("status") != "eligible":
            print(json.dumps({
                "protocol": PROTOCOL,
                "status": "blocked_no_send",
                "reason": str(eligibility.get("reason", "not eligible")),
                "eligibility": eligibility,
                "authority": {
                    "broker_write": False,
                    "live_write": False,
                    "retry": False,
                    "promotion": False,
                },
            }, indent=2, sort_keys=True))
            return 3

    return _run(
        [
            sys.executable,
            str(controller),
            "--day", str(args.day),
            "--repo", str(repo),
            "--expected-head", args.expected_head,
            "--terminal-path", str(Path(args.terminal_path).resolve()),
            "--qualification-plan", str(Path(args.qualification_plan).resolve()),
            "--custody-root", str(Path(args.custody_root).resolve()),
            "--database", str(Path(args.database).resolve()),
            "--output-root", str(Path(args.output_root).resolve()),
            "--summary", str(Path(args.summary).resolve()),
            "--confirm-demo-day", args.confirm_demo_day,
        ],
        cwd=repo,
    )


if __name__ == "__main__":
    raise SystemExit(main())
