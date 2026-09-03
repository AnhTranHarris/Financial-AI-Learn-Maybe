"""Small durable research-cycle primitive inspired by proven external patterns.

This module does not trade, access MT5, choose strategies, or mutate production code.
It only gives Dusty one deterministic, content-addressed experiment loop with atomic
stage checkpoints. Re-running an identical request and stage plan reuses verified
work; interrupted runs resume from the first missing stage.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime
from enum import Enum
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Callable, Mapping
from uuid import uuid4


CYCLE_PROTOCOL = "dusty-research-cycle-v1"
JsonObject = dict[str, Any]
StageRunner = Callable[[Mapping[str, Any]], Any]


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise TypeError("research_cycle_rejects_naive_datetime")
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    raise TypeError(f"unsupported_research_cycle_value:{type(value).__name__}")


def canonical_json(value: object) -> str:
    """Canonical JSON used for experiment and checkpoint identities."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
        default=_json_default,
    )


def fingerprint(value: object) -> str:
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = canonical_json(value).encode("utf-8")
    temporary = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
    with temporary.open("xb") as stream:
        stream.write(payload)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


@dataclass(frozen=True, slots=True)
class ResearchStage:
    """One bounded stage. Version changes whenever its semantics change."""

    name: str
    version: str
    run: StageRunner

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("-", "_").isalnum():
            raise ValueError("research_stage_name_must_be_simple")
        if not self.version.strip():
            raise ValueError("research_stage_version_required")
        if not callable(self.run):
            raise ValueError("research_stage_runner_required")

    @property
    def identity(self) -> JsonObject:
        return {"name": self.name, "version": self.version}


@dataclass(frozen=True, slots=True)
class ResearchCycleResult:
    cycle_fingerprint: str
    run_directory: Path
    outputs: tuple[tuple[str, Any], ...]
    reused_stages: tuple[str, ...]

    @property
    def cache_hit(self) -> bool:
        return bool(self.outputs) and len(self.reused_stages) == len(self.outputs)

    def output_map(self) -> dict[str, Any]:
        return dict(self.outputs)


class ResearchCycle:
    """Execute or resume one immutable research experiment.

    The request and ordered stage identities define the experiment fingerprint. Each
    completed stage is stored in a hash-verified envelope. Existing valid stages are
    reused; corrupt or mismatched checkpoints fail closed instead of being silently
    overwritten. A stage sees only outputs from earlier stages.
    """

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()

    def _identity(self, request: Mapping[str, Any], stages: tuple[ResearchStage, ...]) -> JsonObject:
        if not stages:
            raise ValueError("research_cycle_requires_stages")
        names = tuple(stage.name for stage in stages)
        if len(set(names)) != len(names):
            raise ValueError("research_cycle_stage_names_must_be_unique")
        # Refuse ambiguous experiments: code identity must be frozen by the caller.
        code_commit = request.get("code_commit")
        if not isinstance(code_commit, str) or not code_commit.strip():
            raise ValueError("research_cycle_requires_code_commit")
        return {
            "protocol": CYCLE_PROTOCOL,
            "request": dict(request),
            "stages": [stage.identity for stage in stages],
        }

    def run(self, request: Mapping[str, Any], stages: tuple[ResearchStage, ...]) -> ResearchCycleResult:
        identity = self._identity(request, stages)
        cycle_fingerprint = fingerprint(identity)
        directory = self.root / cycle_fingerprint
        directory.mkdir(parents=True, exist_ok=True)
        identity_path = directory / "request.json"
        if identity_path.exists():
            if _read_json(identity_path) != json.loads(canonical_json(identity)):
                raise ValueError("research_cycle_identity_collision_or_corruption")
        else:
            _atomic_write_json(identity_path, identity)

        outputs: dict[str, Any] = {}
        reused: list[str] = []
        stage_fingerprints: dict[str, str] = {}
        for index, stage in enumerate(stages):
            checkpoint = directory / f"{index:02d}-{stage.name}.json"
            if checkpoint.exists():
                envelope = _read_json(checkpoint)
                payload = self._validated_checkpoint(
                    envelope,
                    cycle_fingerprint=cycle_fingerprint,
                    stage=stage,
                    prior_stage_fingerprints=stage_fingerprints,
                )
                reused.append(stage.name)
            else:
                payload = stage.run(dict(outputs))
                payload_fingerprint = fingerprint(payload)
                envelope = {
                    "schema": 1,
                    "cycle_fingerprint": cycle_fingerprint,
                    "stage": stage.identity,
                    "prior_stage_fingerprints": dict(stage_fingerprints),
                    "payload": payload,
                    "payload_fingerprint": payload_fingerprint,
                }
                _atomic_write_json(checkpoint, envelope)
            payload_fingerprint = fingerprint(payload)
            stage_fingerprints[stage.name] = payload_fingerprint
            outputs[stage.name] = payload

        final = {
            "schema": 1,
            "state": "COMPLETED",
            "cycle_fingerprint": cycle_fingerprint,
            "stage_fingerprints": stage_fingerprints,
        }
        final_path = directory / "result.json"
        if final_path.exists():
            if _read_json(final_path) != final:
                raise ValueError("research_cycle_result_mismatch_or_corruption")
        else:
            _atomic_write_json(final_path, final)
        return ResearchCycleResult(
            cycle_fingerprint,
            directory,
            tuple(outputs.items()),
            tuple(reused),
        )

    @staticmethod
    def _validated_checkpoint(
        envelope: Any,
        *,
        cycle_fingerprint: str,
        stage: ResearchStage,
        prior_stage_fingerprints: Mapping[str, str],
    ) -> Any:
        if not isinstance(envelope, dict) or envelope.get("schema") != 1:
            raise ValueError("research_cycle_checkpoint_schema_invalid")
        if envelope.get("cycle_fingerprint") != cycle_fingerprint:
            raise ValueError("research_cycle_checkpoint_wrong_experiment")
        if envelope.get("stage") != stage.identity:
            raise ValueError("research_cycle_checkpoint_wrong_stage")
        if envelope.get("prior_stage_fingerprints") != dict(prior_stage_fingerprints):
            raise ValueError("research_cycle_checkpoint_prior_chain_mismatch")
        payload = envelope.get("payload")
        if envelope.get("payload_fingerprint") != fingerprint(payload):
            raise ValueError("research_cycle_checkpoint_payload_corrupt")
        return payload
