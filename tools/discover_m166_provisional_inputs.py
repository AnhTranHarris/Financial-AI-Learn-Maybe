from __future__ import annotations

"""Discover explicit M166 research identities from existing local JSON artifacts.

This tool is intentionally read-only.  It does not infer a dataset or parameter
identity from generic/nearby fields.  A candidate is usable only when one JSON
object explicitly contains the production strategy identity plus SHA-256 dataset
and parameter identities.  Optional lane identity, when present, must match.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import subprocess
from typing import Any, Iterable


PROTOCOL = "dusty-m166-provisional-input-discovery-v1"
MAX_JSON_BYTES = 10 * 1024 * 1024
MAX_FILES_DEFAULT = 5000

STRATEGY_KEYS = ("strategy_fingerprint", "strategy_hash", "strategy_execution_fingerprint")
DATASET_KEYS = ("dataset_fingerprint",)
PARAMETER_KEYS = ("parameter_fingerprint", "center_parameter_fingerprint")
LANE_KEYS = ("lane_id",)


def _sha(value: object) -> str | None:
    rendered = str(value).strip().lower()
    if len(rendered) != 64 or any(ch not in "0123456789abcdef" for ch in rendered):
        return None
    return rendered


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo, text=True, capture_output=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or proc.stdout.strip() or "git command failed")
    return proc.stdout.strip()


def _walk_objects(value: Any, path: str = "$") -> Iterable[tuple[str, dict[str, Any]]]:
    if isinstance(value, dict):
        yield path, value
        for key, child in value.items():
            yield from _walk_objects(child, f"{path}.{key}")
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_objects(child, f"{path}[{index}]")


def _first_explicit(row: dict[str, Any], keys: tuple[str, ...]) -> tuple[str, str] | None:
    for key in keys:
        if key in row:
            identity = _sha(row[key])
            if identity is not None:
                return key, identity
    return None


def discover_in_payload(
    payload: Any,
    *,
    expected_strategy: str,
    lane_id: str,
) -> tuple[dict[str, object], ...]:
    expected = _sha(expected_strategy)
    if expected is None:
        raise ValueError("expected strategy requires SHA-256 identity")
    lane = str(lane_id).strip().lower()
    if not lane:
        raise ValueError("lane_id required")

    candidates: list[dict[str, object]] = []
    for object_path, row in _walk_objects(payload):
        strategy = _first_explicit(row, STRATEGY_KEYS)
        dataset = _first_explicit(row, DATASET_KEYS)
        parameter = _first_explicit(row, PARAMETER_KEYS)
        if strategy is None or dataset is None or parameter is None:
            continue
        if strategy[1] != expected:
            continue
        lane_value = None
        lane_key = None
        for key in LANE_KEYS:
            if key in row:
                lane_key = key
                lane_value = str(row[key]).strip().lower()
                break
        if lane_value is not None and lane_value != lane:
            continue
        candidates.append(
            {
                "object_path": object_path,
                "strategy_key": strategy[0],
                "strategy_fingerprint": strategy[1],
                "dataset_key": dataset[0],
                "dataset_fingerprint": dataset[1],
                "parameter_key": parameter[0],
                "parameter_fingerprint": parameter[1],
                "lane_key": lane_key,
                "lane_id": lane_value,
            }
        )
    return tuple(candidates)


def _qualification_manifest(plan: dict[str, Any], lane_id: str) -> dict[str, Any]:
    rows = plan.get("manifests")
    if not isinstance(rows, list):
        raise ValueError("qualification plan lacks manifests")
    matches = [row for row in rows if isinstance(row, dict) and str(row.get("lane_id", "")).lower() == lane_id.lower()]
    if len(matches) != 1:
        raise ValueError("qualification plan must contain exactly one requested lane")
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover explicit existing inputs for provisional M166 research")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--expected-head", required=True)
    parser.add_argument("--qualification-plan", required=True)
    parser.add_argument("--lane-id", required=True)
    parser.add_argument("--search-root", action="append", required=True)
    parser.add_argument("--max-files", type=int, default=MAX_FILES_DEFAULT)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    expected_head = str(args.expected_head).strip().lower()
    if len(expected_head) != 40 or any(ch not in "0123456789abcdef" for ch in expected_head):
        raise ValueError("expected head requires full 40-character Git SHA")
    if _git(repo, "rev-parse", "HEAD").lower() != expected_head:
        raise RuntimeError("workstation Git HEAD does not match expected head")
    if _git(repo, "status", "--porcelain=v1", "--untracked-files=all"):
        raise RuntimeError("workstation repository must be clean")

    qualification_path = Path(args.qualification_plan).resolve()
    if not qualification_path.is_file():
        raise FileNotFoundError(qualification_path)
    qualification = json.loads(qualification_path.read_text(encoding="utf-8"))
    lane = str(args.lane_id).strip().lower()
    manifest = _qualification_manifest(qualification, lane)
    strategy = _sha(manifest.get("strategy_hash"))
    if strategy is None:
        raise ValueError("qualification manifest strategy_hash is invalid")

    max_files = int(args.max_files)
    if not 1 <= max_files <= 50000:
        raise ValueError("max-files out of range")

    roots = tuple(Path(value).resolve() for value in args.search_root)
    files: list[Path] = []
    for root in roots:
        if root.is_file() and root.suffix.lower() == ".json":
            files.append(root)
        elif root.is_dir():
            files.extend(sorted(root.rglob("*.json")))
    files = sorted(set(files), key=lambda path: str(path).casefold())[:max_files]

    candidates: list[dict[str, object]] = []
    skipped_oversize = 0
    parse_failures = 0
    for path in files:
        try:
            if path.stat().st_size > MAX_JSON_BYTES:
                skipped_oversize += 1
                continue
            raw = path.read_bytes()
            payload = json.loads(raw.decode("utf-8"))
        except Exception:
            parse_failures += 1
            continue
        for candidate in discover_in_payload(payload, expected_strategy=strategy, lane_id=lane):
            candidates.append(
                {
                    **candidate,
                    "source_path": str(path),
                    "source_sha256": sha256(raw).hexdigest(),
                }
            )

    # Deduplicate exact identity triples while preserving every source reference.
    grouped: dict[tuple[str, str, str], dict[str, object]] = {}
    for row in candidates:
        key = (
            str(row["strategy_fingerprint"]),
            str(row["dataset_fingerprint"]),
            str(row["parameter_fingerprint"]),
        )
        record = grouped.setdefault(
            key,
            {
                "strategy_fingerprint": key[0],
                "dataset_fingerprint": key[1],
                "parameter_fingerprint": key[2],
                "sources": [],
            },
        )
        sources = record["sources"]
        assert isinstance(sources, list)
        sources.append(
            {
                "source_path": row["source_path"],
                "source_sha256": row["source_sha256"],
                "object_path": row["object_path"],
                "strategy_key": row["strategy_key"],
                "dataset_key": row["dataset_key"],
                "parameter_key": row["parameter_key"],
                "lane_key": row["lane_key"],
                "lane_id": row["lane_id"],
            }
        )

    identity_sets = sorted(grouped.values(), key=lambda row: (str(row["dataset_fingerprint"]), str(row["parameter_fingerprint"])))
    status = "unique_candidate" if len(identity_sets) == 1 else ("no_candidate" if not identity_sets else "ambiguous_candidates")
    result = {
        "protocol": PROTOCOL,
        "source_commit": expected_head,
        "status": status,
        "lane_id": lane,
        "qualification_strategy_hash": strategy,
        "files_scanned": len(files),
        "skipped_oversize": skipped_oversize,
        "parse_failures": parse_failures,
        "identity_set_count": len(identity_sets),
        "identity_sets": identity_sets,
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
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
