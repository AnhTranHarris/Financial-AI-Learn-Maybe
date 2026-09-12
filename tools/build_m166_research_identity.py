from __future__ import annotations

"""Build a genuine read-only M166 research identity from Strategy Estate + MT5 bars."""

import argparse
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import tempfile

from dusty.m166_research_identity import (
    M166ResearchIdentity,
    canonical_bar_payload,
    dataset_fingerprint,
    dataset_payload,
    parameter_fingerprint,
)
from dusty.mt5worker import MT5BarRequest, ReadOnlyMT5Worker
from dusty.strategy_estate import load_strategy_estate


UTC = timezone.utc
PROTOCOL = "dusty-m166-native-research-identity-builder-v1"


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def _atomic_write(path: Path, data: bytes) -> None:
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


def _load_lane(plan_path: Path, lane_id: str) -> dict[str, object]:
    payload = json.loads(plan_path.read_text(encoding="utf-8"))
    rows = payload.get("manifests")
    if not isinstance(rows, list):
        raise ValueError("qualification plan lacks manifests")
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("lane_id", "")).lower() == lane_id.lower()]
    if len(matches) != 1:
        raise ValueError("qualification plan must contain exactly one requested lane")
    return matches[0]


def _floor_completed_m15(now: datetime) -> datetime:
    when = now.astimezone(UTC)
    minute = (when.minute // 15) * 15
    return when.replace(minute=minute, second=0, microsecond=0)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build read-only M166 research identity from genuine MT5 history")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--strategy-estate", required=True)
    parser.add_argument("--lane-id", required=True)
    parser.add_argument("--terminal-path", required=True)
    parser.add_argument("--lookback-days", type=int, default=730)
    parser.add_argument("--minimum-bars", type=int, default=10000)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dataset-output", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected = str(args.expected_head).strip().lower()
    if len(expected) != 40 or any(ch not in "0123456789abcdef" for ch in expected):
        raise ValueError("expected head requires a full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    plan_path = Path(args.qualification_plan).resolve()
    estate_path = Path(args.strategy_estate).resolve()
    terminal_path = Path(args.terminal_path).resolve()
    for path in (plan_path, estate_path, terminal_path):
        if not path.is_file():
            raise FileNotFoundError(path)

    lookback_days = int(args.lookback_days)
    minimum_bars = int(args.minimum_bars)
    if not 90 <= lookback_days <= 3650:
        raise ValueError("lookback-days must be in [90,3650]")
    if not 1000 <= minimum_bars <= 500000:
        raise ValueError("minimum-bars out of range")

    lane = str(args.lane_id).strip().lower()
    manifest = _load_lane(plan_path, lane)
    symbol = str(manifest.get("symbol", "")).strip().upper()
    timeframe = str(manifest.get("timeframe", "")).strip().upper()
    strategy_hash = str(manifest.get("strategy_hash", "")).strip().lower()
    reconstruction_fp = str(manifest.get("reconstruction_fingerprint", "")).strip().lower()
    if not symbol or not timeframe or len(strategy_hash) != 64:
        raise ValueError("qualification lane identity is incomplete")
    if timeframe != "M15":
        raise ValueError("this builder currently supports the certified M15 qualification lane only")

    reconstructions = load_strategy_estate(estate_path)
    matches = [
        row for row in reconstructions
        if row.fingerprint == reconstruction_fp
        and row.candidate_spec.strategy_hash == strategy_hash
        and symbol in row.symbols
        and row.timeframe == timeframe
    ]
    if len(matches) != 1:
        raise RuntimeError("Strategy Estate does not contain exactly one qualification reconstruction")
    reconstruction = matches[0]

    end = _floor_completed_m15(datetime.now(UTC))
    start = end - timedelta(days=lookback_days)
    request_end = end - timedelta(seconds=1)
    worker = ReadOnlyMT5Worker()
    if worker.broker_write_authorized:
        raise RuntimeError("read-only MT5 worker unexpectedly gained broker-write authority")
    bars = tuple(worker.stream_bars(MT5BarRequest(
        terminal_path=str(terminal_path),
        symbol=symbol,
        timeframe=timeframe,
        start=start,
        end=request_end,
        chunk_days=7,
    )))
    if len(bars) < minimum_bars:
        raise RuntimeError(f"insufficient genuine {symbol} {timeframe} history: {len(bars)} < {minimum_bars}")
    if bars[-1].at >= end:
        raise RuntimeError("dataset contains a bar at/after the frozen completed-bar boundary")

    metadata = dataset_payload(symbol=symbol, timeframe=timeframe, bars=bars)
    data_fp = dataset_fingerprint(symbol=symbol, timeframe=timeframe, bars=bars)
    parameter_fp = parameter_fingerprint(reconstruction.candidate_spec)
    identity = M166ResearchIdentity(lane, strategy_hash, data_fp, parameter_fp, metadata)

    dataset_path = Path(args.dataset_output).resolve()
    dataset_lines = "".join(
        json.dumps(canonical_bar_payload(row), sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
        for row in bars
    ).encode("utf-8")
    _atomic_write(dataset_path, dataset_lines)
    dataset_file_sha = sha256(dataset_lines).hexdigest()

    payload = identity.payload | {
        "builder_protocol": PROTOCOL,
        "source_commit": expected,
        "qualification_manifest_fingerprint": str(manifest.get("manifest_fingerprint", "")),
        "reconstruction_fingerprint": reconstruction.fingerprint,
        "parameter_payload_protocol": "dusty-m166-parameter-set-v1",
        "dataset_file": str(dataset_path),
        "dataset_file_sha256": dataset_file_sha,
        "requested_start_utc": start.isoformat(),
        "requested_end_utc_exclusive": end.isoformat(),
        "lookback_days": lookback_days,
        "authority": {
            "broker_write": False,
            "live_write": False,
            "custody_write": False,
            "research_execution": False,
            "promotion": False,
            "retry": False,
            "risk_override": False,
        },
    }
    output = Path(args.output).resolve()
    _atomic_write(output, (json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8"))
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
