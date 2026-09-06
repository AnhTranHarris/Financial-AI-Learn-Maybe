from __future__ import annotations

"""Persistent research-only Strategy Estate for reconstructed candidates.

The estate is not a mutable strategy database.  It is the latest atomic snapshot
of immutable, content-addressed StrategyReconstruction records.  Exact snapshot
bytes are still SHA-256 pinned when handed to a UI/worker process.  Demo/Live
TradingSkill authority remains outside this file and must be projected from
M185/M194 evidence.
"""

from dataclasses import dataclass
from hashlib import sha256
import os
from pathlib import Path
from typing import Iterable

from .strategy_library_snapshot import load_reconstruction_library, write_reconstruction_library
from .trading_skills import StrategyReconstruction


@dataclass(frozen=True, slots=True)
class StrategyEstateUpdate:
    path: Path
    sha256: str
    added: int
    total: int

    def __post_init__(self) -> None:
        if len(self.sha256) != 64 or any(ch not in "0123456789abcdef" for ch in self.sha256):
            raise ValueError("strategy estate update requires SHA-256")
        if self.added < 0 or self.total < self.added:
            raise ValueError("strategy estate counts are invalid")


broker_write_authority = False
live_write_authority = False
promotion_authority = False
risk_override_authority = False
guardian_override_authority = False


def default_strategy_estate_path(environ: dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    local = str(env.get("LOCALAPPDATA", "")).strip()
    if local:
        return Path(local) / "DustyDragon" / "strategy-estate" / "reconstructions.json"
    data_home = str(env.get("XDG_DATA_HOME", "")).strip()
    root = Path(data_home).expanduser() if data_home else Path.home() / ".local" / "share"
    return root / "DustyDragon" / "strategy-estate" / "reconstructions.json"


def _digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def load_strategy_estate(path: str | Path | None = None) -> tuple[StrategyReconstruction, ...]:
    source = Path(path).resolve() if path is not None else default_strategy_estate_path().resolve()
    if not source.exists():
        return ()
    if not source.is_file():
        raise ValueError("strategy estate path must be a file")
    return load_reconstruction_library(source, _digest(source))


def register_reconstructions(
    rows: Iterable[StrategyReconstruction],
    *,
    path: str | Path | None = None,
) -> StrategyEstateUpdate:
    """Idempotently merge immutable reconstructions into the atomic estate.

    A strategy_id collision with different executable/reconstruction identity is
    rejected rather than silently replacing a prior candidate.
    """

    destination = Path(path).resolve() if path is not None else default_strategy_estate_path().resolve()
    existing = load_strategy_estate(destination)
    incoming = tuple(rows)

    by_fingerprint = {row.fingerprint: row for row in existing}
    id_identity = {
        row.candidate_spec.strategy_id: (row.candidate_spec.strategy_hash, row.fingerprint)
        for row in existing
    }

    added = 0
    for row in incoming:
        current = id_identity.get(row.candidate_spec.strategy_id)
        identity = (row.candidate_spec.strategy_hash, row.fingerprint)
        if current is not None and current != identity:
            raise ValueError("strategy estate strategy_id collision")
        if row.fingerprint in by_fingerprint:
            if by_fingerprint[row.fingerprint] != row:
                raise ValueError("strategy estate fingerprint collision")
            continue
        by_fingerprint[row.fingerprint] = row
        id_identity[row.candidate_spec.strategy_id] = identity
        added += 1

    digest = write_reconstruction_library(destination, by_fingerprint.values())
    return StrategyEstateUpdate(destination, digest, added, len(by_fingerprint))


def register_reconstruction(
    row: StrategyReconstruction,
    *,
    path: str | Path | None = None,
) -> StrategyEstateUpdate:
    return register_reconstructions((row,), path=path)
