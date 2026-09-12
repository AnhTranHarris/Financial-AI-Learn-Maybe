from __future__ import annotations

"""Guard the provisional quant runner with durable failure evidence.

This launcher deliberately owns no retry or research semantics. It delegates one
exact execution to ``run_m166_m173_provisional_quant.py`` and, only when that
child fails, atomically records bounded diagnostic evidence in the same output
root. A failure receipt is not a stage artifact and grants no authority.
"""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


PROTOCOL = "dusty-m166-m173-provisional-quant-failure-v1"
MAX_DIAGNOSTIC_CHARS = 20000


def _atomic_json(path: Path, payload: object) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass


def _output_root(arguments: list[str]) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--output-root", required=True)
    known, _ = parser.parse_known_args(arguments)
    return Path(known.output_root).resolve()


def main() -> int:
    arguments = sys.argv[1:]
    root = _output_root(arguments)
    child = Path(__file__).with_name("run_m166_m173_provisional_quant.py")
    result = subprocess.run(
        [sys.executable, str(child), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )

    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)

    if result.returncode == 0:
        return 0

    failure = {
        "protocol": PROTOCOL,
        "status": "failed",
        "failed_at_utc": datetime.now(timezone.utc).isoformat(),
        "child_returncode": result.returncode,
        "stdout_tail": result.stdout[-MAX_DIAGNOSTIC_CHARS:],
        "stderr_tail": result.stderr[-MAX_DIAGNOSTIC_CHARS:],
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "promotion": False,
            "retry": False,
            "risk_override": False,
        },
    }
    _atomic_json(root / "provisional-quant-failure.json", failure)
    print(f"Failure receipt: {root / 'provisional-quant-failure.json'}", file=sys.stderr)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
