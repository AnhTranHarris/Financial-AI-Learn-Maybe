from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    args = parser.parse_args()

    repo = Path(args.repo)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo, text=True).strip()
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=repo,
        text=True,
    ).strip()

    if head != args.expected_head:
        raise RuntimeError(f"unexpected HEAD: {head}")
    if not branch:
        raise RuntimeError("detached HEAD prohibited")
    if dirty:
        raise RuntimeError("working tree must be clean")

    print("M199 exact-head binding: PASS")
    print("Six-desk graduation authority: CERTIFICATION ONLY")
    print("Broker/live/promotion authority: NONE")
    print("RESULT: M199 SIX-DESK SOFTWARE/NATIVE-SHELL GATE PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
