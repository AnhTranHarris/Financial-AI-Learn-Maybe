from __future__ import annotations

"""Local no-LLM smoke for Dusty's Vibe research contractor."""

import argparse
import json
from pathlib import Path

from .vibe_research_service import VibeResearchContractor


def _emit(payload: object) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True), flush=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--vibe-root", type=Path, required=True)
    parser.add_argument("--work-root", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=180)
    args = parser.parse_args(argv)

    contractor = VibeResearchContractor(
        args.vibe_root,
        args.work_root,
        timeout_seconds=args.timeout,
    )
    _emit(
        {
            "event": "vibe_research_smoke_start",
            "safety": {
                "llm_agent": False,
                "mt5": False,
                "broker_credentials": False,
                "orders": False,
                "shell_tools": False,
            },
        }
    )

    checks = (
        ("alpha_zoo_health", "alpha_zoo", {"action": "health"}),
        (
            "alpha_zoo_catalog",
            "alpha_zoo",
            {"action": "list_alphas", "zoo": "alpha101", "limit": 3},
        ),
    )
    failed = False
    for name, tool, tool_args in checks:
        result = contractor.invoke(tool, tool_args)
        payload: dict[str, object] = {
            "event": "vibe_research_smoke_stage",
            "name": name,
            "tool": tool,
            "status": result.status.value,
            "error": result.error,
        }
        if result.evidence is not None:
            payload.update(
                {
                    "vibe_version": result.evidence.vibe_version,
                    "surface_sha256": result.evidence.surface_sha256,
                    "fingerprint": result.evidence.fingerprint,
                    "result_preview": result.evidence.result_text[:600],
                    "authority": {
                        "broker_write": result.evidence.broker_write_authority,
                        "entry_veto": result.evidence.entry_veto_authority,
                        "promotion": result.evidence.promotion_authority,
                    },
                }
            )
        else:
            failed = True
        _emit(payload)

    _emit({"event": "vibe_research_smoke_complete", "status": "fail" if failed else "pass"})
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
