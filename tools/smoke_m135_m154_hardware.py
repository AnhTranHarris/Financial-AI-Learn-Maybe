from __future__ import annotations

"""CLI for M154.1 local read-only hardware certification."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback

from dusty.hardware_certification import (
    HardwareCertificationConfig,
    render_hardware_report,
    run_hardware_certification,
)


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, sort_keys=True, indent=2, allow_nan=False, default=str) + "\n",
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dusty M154.1 local hardware certification")
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--provider-root", type=Path, default=Path.home() / "DustyProviders")
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--native-symbol", default="")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--history-days", type=int, default=14)
    parser.add_argument("--context-observations", type=int, default=256)
    parser.add_argument("--horizon-steps", type=int, default=4)
    parser.add_argument("--ollama-model", default="qwen3:1.7b")
    parser.add_argument("--ollama-base-url", default="http://127.0.0.1:11434")
    args = parser.parse_args(argv)

    native = args.native_symbol.strip() or args.symbol.strip()
    safety = {
        "mt5_orders": False,
        "broker_credentials": False,
        "broker_write": False,
        "entry_veto": False,
        "promotion": False,
        "risk_override": False,
    }
    try:
        result = run_hardware_certification(
            HardwareCertificationConfig(
                terminal_path=args.terminal_path,
                provider_root=args.provider_root,
                work_root=args.work_root,
                symbol=args.symbol,
                native_symbol=native,
                timeframe=args.timeframe,
                history_days=args.history_days,
                context_observations=args.context_observations,
                horizon_steps=args.horizon_steps,
                ollama_model=args.ollama_model,
                ollama_base_url=args.ollama_base_url,
            ),
            now=datetime.now(timezone.utc),
        )
        payload = render_hardware_report(result)
        _write(args.report, payload)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    except Exception as exc:  # certification must preserve the exact failing boundary
        payload = {
            "protocol": "dusty-m1541-local-hardware-certification-v1",
            "status": "fail",
            "error_type": type(exc).__name__,
            "error": " ".join(str(exc).split())[:2000],
            "safety": safety,
        }
        _write(args.report, payload)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        traceback.print_exc(file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
