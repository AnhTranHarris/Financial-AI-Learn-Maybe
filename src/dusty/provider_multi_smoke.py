from __future__ import annotations

"""Synthetic local hardware validation for all forecast contractors.

No MT5 connection is opened. No broker/account data is read. The command starts
all three isolated workers sequentially, reuses them for two forecast rounds,
and then shuts every owned worker down.
"""

import argparse
from datetime import timedelta
from pathlib import Path
from time import perf_counter

from .provider_forecast_adapter import _smoke_bars, canonical_json
from .provider_multi_service import ForecastContractorManager, ForecastSelectionMode
from .provider_process import ProviderWorkerState
from .provider_registry import ProviderRegistry


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="M114 all-three synthetic contractor validation")
    parser.add_argument("--provider-root", type=Path, required=True)
    parser.add_argument("--rounds", type=int, default=2)
    args = parser.parse_args(argv)
    if not 1 <= args.rounds <= 3:
        parser.error("--rounds must be 1 to 3")

    registry = ProviderRegistry(args.provider_root)
    manager = ForecastContractorManager(registry)
    manager.select(ForecastSelectionMode.ALL_THREE)
    bars = _smoke_bars()
    future = tuple(
        bars[-1].at + timedelta(minutes=15 * (index + 1))
        for index in range(16)
    )
    failed = False
    try:
        before = perf_counter()
        startup_states = manager.start_selected()
        startup_elapsed = round(perf_counter() - before, 3)
        pids = manager.pids()
        for provider_id in manager.selected_provider_ids:
            state = startup_states[provider_id]
            print(
                canonical_json(
                    {
                        "event": "startup",
                        "provider_id": provider_id,
                        "state": state.value,
                        "pid": pids[provider_id],
                        "all_selected_startup_seconds": startup_elapsed,
                    }
                ),
                flush=True,
            )
            if state is not ProviderWorkerState.READY:
                failed = True

        if failed:
            return 2

        initial_pids = manager.pids()
        for round_number in range(1, args.rounds + 1):
            before = perf_counter()
            results = manager.forecast_selected(
                bars,
                symbol="EURUSD",
                timeframe="M15",
                horizon_steps=16,
                future_times=future,
            )
            elapsed = round(perf_counter() - before, 3)
            for result in results:
                item = result.result
                evidence = item.evidence
                row = {
                    "event": "forecast",
                    "round": round_number,
                    "provider_id": item.provider_id,
                    "status": item.status.value,
                    "distribution_method": result.provenance.distribution_method,
                    "sample_count": result.provenance.sample_count,
                    "pid": manager.pids()[item.provider_id],
                    "elapsed_round_seconds": elapsed,
                    "error": item.error,
                    "authority": {
                        "broker_write": False if evidence is None else evidence.broker_write_authority,
                        "entry_veto": False if evidence is None else evidence.entry_veto_authority,
                        "promotion": False if evidence is None else evidence.promotion_authority,
                    },
                    "p50": None if evidence is None else evidence.p50,
                    "fingerprint": None if evidence is None else evidence.fingerprint,
                }
                print(canonical_json(row), flush=True)
                if not item.available or evidence is None:
                    failed = True
                elif (
                    evidence.broker_write_authority
                    or evidence.entry_veto_authority
                    or evidence.promotion_authority
                ):
                    failed = True
            if manager.pids() != initial_pids:
                print(
                    canonical_json(
                        {
                            "event": "failure",
                            "error": "provider_pid_changed_between_persistent_rounds",
                            "initial_pids": initial_pids,
                            "current_pids": manager.pids(),
                        }
                    ),
                    flush=True,
                )
                failed = True
            if failed:
                break
        return 2 if failed else 0
    finally:
        stopped = manager.stop_all()
        print(
            canonical_json(
                {
                    "event": "shutdown",
                    "states": {key: value.value for key, value in stopped.items()},
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    raise SystemExit(main())
