from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from dusty.integrated_research_cycle import (
    IntegratedResearchCycleConfig,
    run_integrated_research_cycle,
)
from dusty.local_terminal import TerminalInstallation, WindowsMT5Discovery
from dusty.mt5worker import MT5BarRequest, ReadOnlyMT5Worker
from dusty.provider_multi_service import ForecastContractorManager
from dusty.provider_registry import ProviderRegistry
from dusty.research_runtime import SQLiteResearchCycleStore


def _emit(payload: dict[str, object]) -> None:
    print(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True), flush=True)


def _select_terminal(explicit_path: str) -> TerminalInstallation:
    if explicit_path.strip():
        matches = WindowsMT5Discovery(
            manual_paths=(explicit_path,),
            search_roots=(),
        ).discover()
        if len(matches) != 1:
            raise RuntimeError("explicit_mt5_terminal_not_found")
        return matches[0]

    installations = WindowsMT5Discovery().discover()
    running = tuple(row for row in installations if row.running_process_ids)
    if len(running) == 1:
        return running[0]
    if len(installations) == 1:
        return installations[0]
    if not installations:
        raise RuntimeError("mt5_terminal_not_found")
    candidates = tuple(row.executable_path for row in installations)
    raise RuntimeError(
        "mt5_terminal_selection_ambiguous:"
        + json.dumps(candidates, ensure_ascii=True, separators=(",", ":"))
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M135 integrated research-cycle hardware smoke")
    parser.add_argument("--provider-root", default=str(Path.home() / "DustyProviders"))
    parser.add_argument("--terminal-path", default="")
    parser.add_argument("--symbol", default="EURUSD")
    parser.add_argument("--timeframe", default="M15")
    parser.add_argument("--history-days", type=int, default=10)
    parser.add_argument("--context-observations", type=int, default=256)
    parser.add_argument("--horizon-steps", type=int, default=4)
    parser.add_argument("--work-root", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args(argv)

    work_root = Path(args.work_root).expanduser().resolve()
    report = Path(args.report).expanduser().resolve()
    work_root.mkdir(parents=True, exist_ok=True)
    report.parent.mkdir(parents=True, exist_ok=True)

    safety = {
        "mt5_read_only": True,
        "broker_credentials": False,
        "orders": False,
        "broker_write": False,
        "entry_veto": False,
        "promotion": False,
        "risk_override": False,
        "skill_certification": False,
    }
    payload: dict[str, object] = {
        "protocol": "dusty-m135-integrated-research-cycle-v1",
        "status": "fail",
        "safety": safety,
    }
    store: SQLiteResearchCycleStore | None = None

    _emit(
        {
            "event": "m135_start",
            "symbol": args.symbol.upper(),
            "timeframe": args.timeframe.upper(),
            "safety": safety,
        }
    )

    try:
        if args.history_days < 1 or args.history_days > 3650:
            raise ValueError("history_days_out_of_bounds")
        terminal = _select_terminal(args.terminal_path)
        _emit(
            {
                "event": "m135_stage",
                "name": "terminal",
                "status": "pass",
                "source": [value.value for value in terminal.sources],
                "running": bool(terminal.running_process_ids),
            }
        )

        end = datetime.now(timezone.utc)
        start = end - timedelta(days=args.history_days)
        request = MT5BarRequest(
            terminal.executable_path,
            args.symbol.upper(),
            args.timeframe.upper(),
            start,
            end,
        )
        raw_bars = tuple(ReadOnlyMT5Worker().stream_bars(request))
        _emit(
            {
                "event": "m135_stage",
                "name": "mt5_history",
                "status": "pass",
                "raw_bars": len(raw_bars),
            }
        )

        registry = ProviderRegistry(Path(args.provider_root))
        manager = ForecastContractorManager(registry)
        config = IntegratedResearchCycleConfig(
            symbol=args.symbol,
            timeframe=args.timeframe,
            context_observations=args.context_observations,
            horizon_steps=args.horizon_steps,
        )
        store_path = work_root / "m135-research-cycle.sqlite3"
        store = SQLiteResearchCycleStore(store_path)
        result = run_integrated_research_cycle(
            raw_bars,
            manager,
            store,
            config=config,
        )
        latest = store.latest(result.cycle_id)
        if latest is None or latest.fingerprint != result.checkpoint.fingerprint:
            raise RuntimeError("m135_checkpoint_reopen_identity_failed")
        if not store.integrity_ok():
            raise RuntimeError("m135_sqlite_integrity_failed")

        forecasts = []
        for row in result.forecast_results:
            evidence = row.result.evidence
            forecasts.append(
                {
                    "provider_id": row.result.provider_id,
                    "status": row.result.status.value,
                    "error": row.result.error,
                    "evidence_fingerprint": None if evidence is None else evidence.fingerprint,
                    "predicted_return_p50": None
                    if evidence is None
                    else evidence.predicted_return_p50,
                    "distribution_method": row.provenance.distribution_method,
                    "sample_count": row.provenance.sample_count,
                }
            )

        payload.update(
            {
                "status": "pass",
                "cycle_id": result.cycle_id,
                "symbol": result.config.symbol.upper(),
                "timeframe": result.config.timeframe.upper(),
                "pit_as_of": result.pit_context.as_of.isoformat(),
                "pit_context_hash": result.pit_context.context_hash,
                "future_schedule_basis": result.future_schedule_basis,
                "future_times": [value.isoformat() for value in result.future_times],
                "skill_certification_eligible": result.skill_certification_eligible,
                "disagreement": {
                    "state": result.disagreement.state.value,
                    "provider_directions": list(result.disagreement.provider_directions),
                },
                "forecasts": forecasts,
                "blackboard_fingerprint": result.blackboard.fingerprint,
                "checkpoint_fingerprint": result.checkpoint.fingerprint,
                "cycle_fingerprint": result.fingerprint,
                "sqlite_path": str(store_path),
            }
        )
        _emit(
            {
                "event": "m135_stage",
                "name": "integrated_cycle",
                "status": "pass",
                "disagreement": result.disagreement.state.value,
                "providers": len(forecasts),
            }
        )
        _emit({"event": "m135_complete", "status": "pass", "report": str(report)})
        return_code = 0
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}:{exc}"
        _emit(
            {
                "event": "m135_complete",
                "status": "fail",
                "error": payload["error"],
                "report": str(report),
            }
        )
        return_code = 2
    finally:
        if store is not None:
            store.close()
        report.write_text(
            json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True),
            encoding="utf-8",
        )
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
