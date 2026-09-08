from __future__ import annotations

"""Read-only workstation discovery for genuine M185 custody inputs.

The scanner never creates a Champion, opens SQLite databases writable, or
interprets test fixtures as production evidence. It only inventories candidate
M174/M184/selection artifacts so a later custody step can be evidence-backed.
"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import sqlite3


INTERESTING_KEYS = {
    "protocol",
    "lane_id",
    "strategy_family",
    "strategy_fingerprint",
    "strategy_hash",
    "generation_id",
    "selection_evidence_fingerprint",
    "robustness_fingerprint",
    "forecast_integration_fingerprint",
    "artifact_fingerprint",
    "source_commit",
    "status",
}
INTERESTING_TERMS = (
    "champion",
    "robust",
    "forecast",
    "strategy",
    "selection",
    "artifact",
    "experiment",
    "certif",
)
SUFFIXES = {".json", ".jsonl", ".db", ".sqlite", ".sqlite3"}
MAX_FILE_BYTES = 32 * 1024 * 1024


def _file_sha(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _interesting_mapping(value: object) -> dict[str, object]:
    found: dict[str, object] = {}

    def visit(node: object) -> None:
        if isinstance(node, dict):
            for key, item in node.items():
                rendered = str(key)
                if rendered in INTERESTING_KEYS and rendered not in found:
                    if isinstance(item, (str, int, float, bool)) or item is None:
                        found[rendered] = item
                visit(item)
        elif isinstance(node, list):
            for item in node[:1000]:
                visit(item)

    visit(value)
    return found


def _json_candidate(path: Path) -> dict[str, object] | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        text = path.read_text(encoding="utf-8-sig", errors="strict")
        payload: object
        if path.suffix.lower() == ".jsonl":
            rows = []
            for line in text.splitlines()[:2000]:
                if line.strip():
                    rows.append(json.loads(line))
            payload = rows
        else:
            payload = json.loads(text)
        identities = _interesting_mapping(payload)
        if not identities:
            return None
        return {
            "kind": "json",
            "path": str(path.resolve()),
            "size": path.stat().st_size,
            "sha256": _file_sha(path),
            "identities": identities,
        }
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def _sqlite_candidate(path: Path) -> dict[str, object] | None:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return None
        uri = path.resolve().as_uri() + "?mode=ro"
        db = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            rows = db.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            matched = []
            for name, sql in rows:
                haystack = f"{name} {sql or ''}".lower()
                if any(term in haystack for term in INTERESTING_TERMS):
                    matched.append(str(name))
            if not matched:
                return None
            return {
                "kind": "sqlite",
                "path": str(path.resolve()),
                "size": path.stat().st_size,
                "sha256": _file_sha(path),
                "matching_tables": matched,
            }
        finally:
            db.close()
    except (OSError, sqlite3.Error):
        return None


def discover(roots: tuple[Path, ...]) -> dict[str, object]:
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    scanned = 0
    for root in roots:
        resolved_root = root.resolve()
        if not resolved_root.exists():
            continue
        paths = [resolved_root] if resolved_root.is_file() else resolved_root.rglob("*")
        for path in paths:
            if not path.is_file() or path.suffix.lower() not in SUFFIXES:
                continue
            key = str(path.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            scanned += 1
            row = (
                _json_candidate(path)
                if path.suffix.lower() in {".json", ".jsonl"}
                else _sqlite_candidate(path)
            )
            if row is not None:
                candidates.append(row)
    candidates.sort(key=lambda row: str(row["path"]).lower())
    return {
        "protocol": "dusty-m1941-m185-custody-discovery-v1",
        "read_only": True,
        "roots": [str(root.resolve()) for root in roots],
        "files_scanned": scanned,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Read-only M185 custody evidence discovery")
    root.add_argument("--root", action="append", required=True)
    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    report = discover(tuple(Path(value) for value in args.root))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
