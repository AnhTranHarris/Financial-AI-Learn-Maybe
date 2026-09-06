from __future__ import annotations

"""Bounded local Ollama adapter for M196.5 strategy reconstruction.

The model does not write code and cannot declare what the archived source said.
It may only propose a small typed executable *research hypothesis* within caller-
supplied symbols, timeframes, sessions, and numeric features. Source-declared
rules are copied from the immutable StrategyProposal by deterministic code.

Ollama JSON-schema enforcement is treated as advisory transport. Every returned
field is parsed again with exact keys/types/enums before StrategySpecV2 and the
M196.5 reconstruction boundary are constructed.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
import math
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from .experience import TradeSide
from .ollama_quant_reviewer import Transport, _urllib_transport
from .research import Clause, RuleOp
from .source_intake import EvidenceClass, StrategyProposal
from .strategy_ir import ExecutionSensitivity, ExitPlan, GroupMode, RuleGroup, StrategySpecV2
from .trading_skills import (
    ReconstructionActor,
    ReconstructionRule,
    ReconstructionRuleBasis,
    StrategyReconstruction,
    reconstruct_strategy,
)


KEEP_ALIVE = "10m"
NUM_PREDICT = 768


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: object) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def _one_line(value: object, label: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    value = value.strip()
    if not value or "\n" in value or "\r" in value or len(value) > maximum:
        raise ValueError(f"{label} must be nonempty, one line, and <= {maximum} characters")
    return value


def _string_list(value: object, label: str, *, maximum_items: int = 32) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or len(value) > maximum_items:
        raise ValueError(f"{label} must be a nonempty bounded list")
    rows = tuple(_one_line(row, label, 128) for row in value)
    if len({row.casefold() for row in rows}) != len(rows):
        raise ValueError(f"{label} must not contain duplicates")
    return rows


def _integer(value: object, label: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _number(value: object, label: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result) or result < minimum:
        raise ValueError(f"{label} must be finite and >= {minimum}")
    return result


def _exact_keys(value: object, expected: set[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError(f"{label} schema mismatch")
    return value


def _unique(values: Iterable[str], label: str, *, upper: bool = False) -> tuple[str, ...]:
    rows = tuple(_one_line(value, label, 128) for value in values)
    normalized = tuple(row.upper() if upper else row.casefold() for row in rows)
    if not rows or len(set(normalized)) != len(rows):
        raise ValueError(f"{label} must be unique and nonempty")
    return tuple(row.upper() if upper else row for row in rows)


def _tf_minutes(value: str) -> int:
    value = value.upper()
    if len(value) < 2 or not value[1:].isdigit() or value[0] not in {"M", "H"}:
        raise ValueError("allowed timeframe must be M<n> or H<n>")
    amount = int(value[1:])
    if amount <= 0:
        raise ValueError("allowed timeframe must be positive")
    return amount if value[0] == "M" else amount * 60


@dataclass(frozen=True, slots=True)
class OllamaReconstructionRequest:
    request_id: str
    proposal: StrategyProposal
    model_tag: str
    model_digest: str
    allowed_symbols: tuple[str, ...]
    allowed_timeframes: tuple[str, ...]
    allowed_features: tuple[str, ...]
    allowed_sessions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_id", _one_line(self.request_id, "reconstruction request id", 128))
        if self.proposal.evidence_class is not EvidenceClass.STRATEGY_HYPOTHESIS:
            raise ValueError("Ollama reconstruction requires a strategy-hypothesis proposal")
        object.__setattr__(self, "model_tag", _one_line(self.model_tag, "Ollama model tag", 128))
        digest = str(self.model_digest).strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Ollama reconstruction requires exact model SHA-256")
        object.__setattr__(self, "model_digest", digest)
        symbols = _unique(self.allowed_symbols, "allowed symbol", upper=True)
        timeframes = _unique(self.allowed_timeframes, "allowed timeframe", upper=True)
        features = _unique(self.allowed_features, "allowed feature")
        sessions = () if not self.allowed_sessions else _unique(self.allowed_sessions, "allowed session")
        for timeframe in timeframes:
            if _tf_minutes(timeframe) < 5:
                raise ValueError("Ollama reconstruction cannot receive a sub-M5 timeframe")
        object.__setattr__(self, "allowed_symbols", symbols)
        object.__setattr__(self, "allowed_timeframes", timeframes)
        object.__setattr__(self, "allowed_features", features)
        object.__setattr__(self, "allowed_sessions", sessions)

    @property
    def fingerprint(self) -> str:
        return _digest({
            "protocol": "dusty-m1965-ollama-reconstruction-v1",
            "request_id": self.request_id,
            "proposal_fingerprint": self.proposal.fingerprint,
            "model_tag": self.model_tag,
            "model_digest": self.model_digest,
            "allowed_symbols": self.allowed_symbols,
            "allowed_timeframes": self.allowed_timeframes,
            "allowed_features": self.allowed_features,
            "allowed_sessions": self.allowed_sessions,
        })


class ReconstructionAvailability(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class LocalStrategyReconstructionResult:
    status: ReconstructionAvailability
    reconstruction: StrategyReconstruction | None = None
    request_fingerprint: str = ""
    raw_response_sha256: str = ""
    error: str = ""

    def __post_init__(self) -> None:
        if self.status is ReconstructionAvailability.AVAILABLE:
            if self.reconstruction is None or self.error:
                raise ValueError("available reconstruction requires reconstruction only")
            for value in (self.request_fingerprint, self.raw_response_sha256):
                if len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
                    raise ValueError("available reconstruction requires SHA-256 provenance")
        elif self.reconstruction is not None or not self.error.strip():
            raise ValueError("unavailable reconstruction requires error only")

    @property
    def available(self) -> bool:
        return self.status is ReconstructionAvailability.AVAILABLE


class OllamaStrategyReconstructor:
    """Optional localhost reconstructor. Any transport/schema fault => UNAVAILABLE."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 180.0,
        transport: Transport = _urllib_transport,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("strategy reconstruction Ollama endpoint must be localhost HTTP")
        if parsed.path not in {"", "/"} or parsed.query or parsed.fragment or not 0 < timeout_seconds <= 600:
            raise ValueError("strategy reconstruction Ollama endpoint/timeout invalid")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self._transport = transport

    broker_write_authority = live_write_authority = promotion_authority = risk_override_authority = guardian_override_authority = property(lambda self: False)

    def reconstruct(self, request: OllamaReconstructionRequest, *, created_at: datetime) -> LocalStrategyReconstructionResult:
        try:
            installed = self._model_digest(request.model_tag)
            if installed != request.model_digest:
                return self._unavailable("ollama_model_digest_mismatch")
            schema = _response_schema(request)
            response = self._transport(
                "POST",
                f"{self.base_url}/api/chat",
                {
                    "model": request.model_tag,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You reconstruct trading theories for research only. Return only the required JSON. "
                                "Every field you add is a hypothesis to falsify, never a claim about what the source used. "
                                "You have no broker, trading, promotion, risk, Guardian, capital, tool, or credential authority."
                            ),
                        },
                        {"role": "user", "content": _canonical(_prompt_payload(request))},
                    ],
                    "stream": False,
                    "think": False,
                    "format": schema,
                    "keep_alive": KEEP_ALIVE,
                    "options": {"temperature": 0, "num_predict": NUM_PREDICT},
                },
                self.timeout_seconds,
            )
            if response.get("done_reason") == "length":
                return self._unavailable("ollama_reconstruction_truncated")
            message = response.get("message")
            if not isinstance(message, dict) or not isinstance(message.get("content"), str):
                return self._unavailable("ollama_chat_response_missing_content")
            raw_text = message["content"]
            raw_sha = sha256(raw_text.encode("utf-8")).hexdigest()
            reconstruction = _parse_response(request, raw_text, raw_sha, created_at)
            return LocalStrategyReconstructionResult(
                ReconstructionAvailability.AVAILABLE,
                reconstruction,
                request.fingerprint,
                raw_sha,
            )
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
            return self._unavailable(f"ollama_strategy_reconstruction_failed:{type(exc).__name__}:{exc}")

    def _model_digest(self, model_tag: str) -> str:
        response = self._transport("GET", f"{self.base_url}/api/tags", None, min(self.timeout_seconds, 30.0))
        models = response.get("models")
        if not isinstance(models, list):
            raise ValueError("Ollama model list missing")
        matches = []
        for row in models:
            if isinstance(row, dict) and model_tag in {str(row.get("name", "")), str(row.get("model", ""))}:
                matches.append(str(row.get("digest", "")).lower())
        if len(matches) != 1:
            raise ValueError("Ollama model tag missing or ambiguous")
        digest = matches[0]
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError("Ollama model digest is not SHA-256")
        return digest

    @staticmethod
    def _unavailable(error: str) -> LocalStrategyReconstructionResult:
        rendered = " ".join(error.strip().split())[:1000] or "ollama_strategy_reconstruction_unavailable"
        return LocalStrategyReconstructionResult(ReconstructionAvailability.UNAVAILABLE, error=rendered)


def _prompt_payload(request: OllamaReconstructionRequest) -> dict[str, object]:
    proposal = request.proposal
    return {
        "protocol": "dusty-m1965-ollama-reconstruction-v1",
        "request_sha256": request.fingerprint,
        "source": {
            "proposal_fingerprint": proposal.fingerprint,
            "title": proposal.title,
            "symbols": proposal.symbols,
            "timeframes": proposal.timeframes,
            "components": proposal.components,
            "declared_rules": proposal.declared_rules,
            "unresolved": proposal.unresolved,
        },
        "allowed": {
            "symbols": request.allowed_symbols,
            "timeframes": request.allowed_timeframes,
            "features": request.allowed_features,
            "sessions": request.allowed_sessions,
            "directions": ("long", "short"),
            "operators": tuple(value.value for value in RuleOp),
            "group_modes": tuple(value.value for value in GroupMode),
            "execution_sensitivity": ("low", "normal", "high"),
        },
        "instructions": (
            "Build one small falsifiable candidate. Use only allowed values. Numeric clauses only. "
            "Do not infer source authorship or performance. Prefer simple rules. A missing edge should be rejected later by testing, not hidden with complexity."
        ),
    }


def _response_schema(request: OllamaReconstructionRequest) -> dict[str, object]:
    clause = {
        "type": "object",
        "properties": {
            "feature": {"type": "string", "enum": list(request.allowed_features)},
            "op": {"type": "string", "enum": [value.value for value in RuleOp]},
            "value": {"type": "number"},
        },
        "required": ["feature", "op", "value"],
        "additionalProperties": False,
    }
    group = {
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": [value.value for value in GroupMode]},
            "clauses": {"type": "array", "items": clause, "minItems": 1, "maxItems": 6},
        },
        "required": ["mode", "clauses"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "direction": {"type": "string", "enum": ["long", "short"]},
            "symbols": {"type": "array", "items": {"type": "string", "enum": list(request.allowed_symbols)}, "minItems": 1, "maxItems": len(request.allowed_symbols), "uniqueItems": True},
            "timeframe": {"type": "string", "enum": list(request.allowed_timeframes)},
            "entry_groups": {"type": "array", "items": group, "minItems": 1, "maxItems": 3},
            "stop": _price_schema(("pct", "atr")),
            "target": _price_schema(("off", "pct", "atr", "rr")),
            "trailing": _price_schema(("off", "pct", "atr")),
            "breakeven_rr": {"type": "number", "minimum": 0},
            "max_hold_steps": {"type": "integer", "minimum": 1, "maximum": 500},
            "intended_horizon_minutes": {"type": "integer", "minimum": 15, "maximum": 10080},
            "cooldown_steps": {"type": "integer", "minimum": 0, "maximum": 500},
            "session_filters": {"type": "array", "items": {"type": "string", "enum": list(request.allowed_sessions)}, "maxItems": len(request.allowed_sessions), "uniqueItems": True},
            "event_exclusion_minutes": {"type": "integer", "minimum": 0, "maximum": 1440},
            "execution_sensitivity": {"type": "string", "enum": ["low", "normal", "high"]},
        },
        "required": [
            "direction", "symbols", "timeframe", "entry_groups", "stop", "target", "trailing",
            "breakeven_rr", "max_hold_steps", "intended_horizon_minutes", "cooldown_steps",
            "session_filters", "event_exclusion_minutes", "execution_sensitivity",
        ],
        "additionalProperties": False,
    }


def _price_schema(kinds: tuple[str, ...]) -> dict[str, object]:
    return {
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": list(kinds)},
            "value": {"type": "number", "minimum": 0},
        },
        "required": ["kind", "value"],
        "additionalProperties": False,
    }


def _parse_response(request: OllamaReconstructionRequest, response_text: str, raw_sha: str, created_at: datetime) -> StrategyReconstruction:
    raw = json.loads(response_text)
    expected = {
        "direction", "symbols", "timeframe", "entry_groups", "stop", "target", "trailing",
        "breakeven_rr", "max_hold_steps", "intended_horizon_minutes", "cooldown_steps",
        "session_filters", "event_exclusion_minutes", "execution_sensitivity",
    }
    row = _exact_keys(raw, expected, "Ollama reconstruction response")
    direction_text = _one_line(row["direction"], "direction", 16)
    if direction_text not in {"long", "short"}:
        raise ValueError("Ollama reconstruction direction invalid")
    symbols = _string_list(row["symbols"], "symbols")
    if any(symbol.upper() not in request.allowed_symbols for symbol in symbols):
        raise ValueError("Ollama reconstruction used an unapproved symbol")
    symbols = tuple(symbol.upper() for symbol in symbols)
    timeframe = _one_line(row["timeframe"], "timeframe", 32).upper()
    if timeframe not in request.allowed_timeframes:
        raise ValueError("Ollama reconstruction used an unapproved timeframe")
    timeframe_minutes = _tf_minutes(timeframe)

    groups_raw = row["entry_groups"]
    if not isinstance(groups_raw, list) or not 1 <= len(groups_raw) <= 3:
        raise ValueError("entry_groups must contain 1-3 groups")
    groups: list[RuleGroup] = []
    hypothesis_rules: list[ReconstructionRule] = []
    for group_index, group_raw in enumerate(groups_raw):
        group = _exact_keys(group_raw, {"mode", "clauses"}, "entry group")
        mode_text = _one_line(group["mode"], "group mode", 16)
        try:
            mode = GroupMode(mode_text)
        except ValueError as exc:
            raise ValueError("entry group mode invalid") from exc
        clauses_raw = group["clauses"]
        if not isinstance(clauses_raw, list) or not 1 <= len(clauses_raw) <= 6:
            raise ValueError("entry group clauses must contain 1-6 clauses")
        clauses: list[Clause] = []
        for clause_index, clause_raw in enumerate(clauses_raw):
            clause = _exact_keys(clause_raw, {"feature", "op", "value"}, "entry clause")
            feature = _one_line(clause["feature"], "clause feature", 128)
            if feature not in request.allowed_features:
                raise ValueError("Ollama reconstruction used an unapproved feature")
            try:
                op = RuleOp(_one_line(clause["op"], "clause op", 16))
            except ValueError as exc:
                raise ValueError("entry clause operator invalid") from exc
            value = _number(clause["value"], "clause value", -1e300)
            clauses.append(Clause(feature, op, value))
            hypothesis_rules.append(ReconstructionRule(
                f"hypothesis.entry.{group_index}.{clause_index}",
                f"{feature} {op.value} {value!r}",
                ReconstructionRuleBasis.RESEARCH_HYPOTHESIS,
            ))
        groups.append(RuleGroup(tuple(clauses), mode))

    stop = _price_rule(row["stop"], "stop", {"pct", "atr"}, allow_off=False)
    target = _price_rule(row["target"], "target", {"off", "pct", "atr", "rr"}, allow_off=True)
    trailing = _price_rule(row["trailing"], "trailing", {"off", "pct", "atr"}, allow_off=True)
    breakeven = _number(row["breakeven_rr"], "breakeven_rr")
    max_hold = _integer(row["max_hold_steps"], "max_hold_steps", 1)
    horizon = _integer(row["intended_horizon_minutes"], "intended_horizon_minutes", 15)
    cooldown = _integer(row["cooldown_steps"], "cooldown_steps", 0)
    event_exclusion = _integer(row["event_exclusion_minutes"], "event_exclusion_minutes", 0)
    sessions_raw = row["session_filters"]
    if not isinstance(sessions_raw, list):
        raise ValueError("session_filters must be a list")
    sessions = tuple(_one_line(value, "session filter", 128) for value in sessions_raw)
    if len({value.casefold() for value in sessions}) != len(sessions):
        raise ValueError("session_filters must be unique")
    if any(value not in request.allowed_sessions for value in sessions):
        raise ValueError("Ollama reconstruction used an unapproved session")
    sensitivity_text = _one_line(row["execution_sensitivity"], "execution_sensitivity", 32)
    if sensitivity_text not in {"low", "normal", "high"}:
        raise ValueError("execution_sensitivity invalid")

    raw_identity = _digest({
        "request": request.fingerprint,
        "model_tag": request.model_tag,
        "model_digest": request.model_digest,
        "raw_response_sha256": raw_sha,
    })
    strategy_id = f"ollama-recon-{request.proposal.fingerprint[:12]}-{raw_sha[:12]}"
    spec = StrategySpecV2(
        strategy_id=strategy_id,
        direction=TradeSide(direction_text),
        entry_groups=tuple(groups),
        exit_plan=ExitPlan(
            stop,
            target,
            trailing,
            "off" if breakeven == 0 else f"rr:{breakeven:g}",
            max_hold,
        ),
        decision_timeframe_minutes=timeframe_minutes,
        intended_horizon_minutes=horizon,
        session_filters=sessions,
        event_exclusion_minutes=event_exclusion,
        cooldown_steps=cooldown,
        execution_sensitivity=ExecutionSensitivity(sensitivity_text),
    )
    source_rules = tuple(
        ReconstructionRule(name, value, ReconstructionRuleBasis.SOURCE_DECLARED)
        for name, value in request.proposal.declared_rules
    )
    hypothesis_rules.extend((
        ReconstructionRule("hypothesis.direction", direction_text, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.stop", stop, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.target", target, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.trailing", trailing, ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
        ReconstructionRule("hypothesis.horizon_minutes", str(horizon), ReconstructionRuleBasis.RESEARCH_HYPOTHESIS),
    ))
    return reconstruct_strategy(
        request.proposal,
        candidate_spec=spec,
        symbols=symbols,
        timeframe=timeframe,
        rules=(*source_rules, *hypothesis_rules),
        actor=ReconstructionActor.OLLAMA,
        actor_fingerprint=raw_identity,
        created_at=created_at,
    )


def _price_rule(value: object, label: str, allowed: set[str], *, allow_off: bool) -> str:
    row = _exact_keys(value, {"kind", "value"}, label)
    kind = _one_line(row["kind"], f"{label} kind", 16)
    if kind not in allowed:
        raise ValueError(f"{label} kind invalid")
    number = _number(row["value"], f"{label} value")
    if kind == "off":
        if not allow_off or number != 0:
            raise ValueError(f"{label} off requires zero value")
        return "off"
    if number <= 0:
        raise ValueError(f"{label} active value must be positive")
    return f"{kind}:{number:g}"
