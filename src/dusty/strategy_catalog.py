from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping

from .demo_session import AccountMode
from .local_terminal import BrokerSymbolOption, TerminalSnapshot


class OperatingMode(StrEnum):
    BACKTEST = "backtest"
    DEMO = "demo"
    LIVE = "live"


class StrategyStage(StrEnum):
    DISCOVERED = "discovered"
    QUARANTINED = "quarantined"
    TRANSLATED = "translated"
    BACKTEST_CANDIDATE = "backtest_candidate"
    BACKTEST_CERTIFIED = "backtest_certified"
    DEMO_CERTIFIED = "demo_certified"
    LIVE_ELIGIBLE = "live_eligible"
    RESTRICTED = "restricted"
    RETIRED = "retired"


_BACKTEST_STAGES = {
    StrategyStage.TRANSLATED,
    StrategyStage.BACKTEST_CANDIDATE,
    StrategyStage.BACKTEST_CERTIFIED,
    StrategyStage.DEMO_CERTIFIED,
    StrategyStage.LIVE_ELIGIBLE,
    StrategyStage.RESTRICTED,
}


@dataclass(frozen=True, slots=True)
class StrategyCatalogEntry:
    strategy_id: str
    title: str
    strategy_hash: str
    stage: StrategyStage
    allowed_symbols: tuple[str, ...] = ()
    allowed_category_prefixes: tuple[str, ...] = ()
    universal_symbol_compatibility: bool = False
    source_url: str = ""
    timeframe: str = ""

    def __post_init__(self) -> None:
        if not self.strategy_id.strip() or not self.title.strip() or not _is_hex(self.strategy_hash, 64):
            raise ValueError("strategy catalog identity is incomplete")
        if self.universal_symbol_compatibility and (self.allowed_symbols or self.allowed_category_prefixes):
            raise ValueError("universal strategy cannot also declare symbol restrictions")
        if len({item.casefold() for item in self.allowed_symbols}) != len(self.allowed_symbols):
            raise ValueError("allowed strategy symbols must be unique")

    def compatible_with(self, symbol: BrokerSymbolOption) -> bool:
        if self.universal_symbol_compatibility:
            return True
        raw = symbol.symbol.casefold()
        category = symbol.category.casefold()
        exact = any(item.casefold() == raw for item in self.allowed_symbols)
        category_match = any(category.startswith(item.casefold()) for item in self.allowed_category_prefixes)
        return exact or category_match

    @property
    def backtest_executable(self) -> bool:
        return self.stage in _BACKTEST_STAGES


@dataclass(frozen=True, slots=True)
class QualificationBinding:
    terminal_identity_key: str
    server: str
    account_mode: AccountMode
    symbol: str
    strategy_hash: str
    code_commit: str
    model_fingerprints: tuple[str, ...] = ()
    tool_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not all(
            (
                self.terminal_identity_key.strip(),
                self.server.strip(),
                self.symbol.strip(),
                _is_hex(self.strategy_hash, 64),
                _is_hex(self.code_commit, 40),
            )
        ):
            raise ValueError("qualification binding is incomplete")
        if any(not _is_hex(value, 64) for value in (*self.model_fingerprints, *self.tool_fingerprints)):
            raise ValueError("model and tool fingerprints must be sha256 values")

    @property
    def fingerprint(self) -> str:
        payload = {
            "terminal": self.terminal_identity_key,
            "server": self.server,
            "account_mode": self.account_mode.value,
            "symbol": self.symbol.upper(),
            "strategy_hash": self.strategy_hash,
            "code_commit": self.code_commit,
            "models": tuple(sorted(self.model_fingerprints)),
            "tools": tuple(sorted(self.tool_fingerprints)),
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class ModeProof:
    binding_fingerprint: str
    backtest_passed: bool
    native_parity_passed: bool
    demo_campaign_passed: bool = False
    six_desk_campaign_passed: bool = False
    user_demo_confirmation: bool = False
    user_live_confirmation: bool = False

    def __post_init__(self) -> None:
        if not _is_hex(self.binding_fingerprint, 64):
            raise ValueError("mode proof requires exact qualification binding")


@dataclass(frozen=True, slots=True)
class ModeGate:
    mode: OperatingMode
    available: bool
    reasons: tuple[str, ...]


def compatible_strategies(
    catalog: Iterable[StrategyCatalogEntry],
    symbol: BrokerSymbolOption,
) -> tuple[StrategyCatalogEntry, ...]:
    return tuple(
        sorted(
            (entry for entry in catalog if entry.compatible_with(symbol)),
            key=lambda entry: (entry.title.casefold(), entry.strategy_id.casefold()),
        )
    )


def load_strategy_catalog(path: str | Path) -> tuple[StrategyCatalogEntry, ...]:
    """Load a small reviewed catalog export; this never loads or executes strategy code."""
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("strategy catalog must be a JSON list")
    entries = []
    allowed_keys = {
        "strategy_id",
        "title",
        "strategy_hash",
        "stage",
        "allowed_symbols",
        "allowed_category_prefixes",
        "universal_symbol_compatibility",
        "source_url",
        "timeframe",
    }
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("strategy catalog rows must be objects")
        unknown = sorted(set(row) - allowed_keys)
        if unknown:
            raise ValueError(f"strategy catalog contains unsupported fields: {','.join(unknown)}")
        entries.append(
            StrategyCatalogEntry(
                strategy_id=_required_string(row, "strategy_id"),
                title=_required_string(row, "title"),
                strategy_hash=_required_string(row, "strategy_hash"),
                stage=StrategyStage(_required_string(row, "stage")),
                allowed_symbols=_string_tuple(row.get("allowed_symbols", ()), "allowed_symbols"),
                allowed_category_prefixes=_string_tuple(
                    row.get("allowed_category_prefixes", ()), "allowed_category_prefixes"
                ),
                universal_symbol_compatibility=_optional_bool(row, "universal_symbol_compatibility"),
                source_url=_optional_string(row, "source_url"),
                timeframe=_optional_string(row, "timeframe"),
            )
        )
    if len({entry.strategy_id for entry in entries}) != len(entries):
        raise ValueError("strategy catalog identifiers must be unique")
    return tuple(entries)


def _string_tuple(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"strategy catalog {field} must be a list")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"strategy catalog {field} must contain nonempty strings")
    return tuple(value)


def _required_string(row: Mapping[str, object], field: str) -> str:
    value = row.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"strategy catalog {field} must be a nonempty string")
    return value


def _optional_string(row: Mapping[str, object], field: str) -> str:
    value = row.get(field, "")
    if not isinstance(value, str):
        raise ValueError(f"strategy catalog {field} must be a string")
    return value


def _optional_bool(row: Mapping[str, object], field: str) -> bool:
    value = row.get(field, False)
    if not isinstance(value, bool):
        raise ValueError(f"strategy catalog {field} must be a boolean")
    return value


def _is_hex(value: str, length: int) -> bool:
    return len(value) == length and all(character in "0123456789abcdefABCDEF" for character in value)


def assess_mode_gates(
    terminal: TerminalSnapshot,
    symbol: BrokerSymbolOption,
    strategy: StrategyCatalogEntry,
    *,
    binding: QualificationBinding,
    proof: ModeProof | None,
    live_engineering_authorized: bool = False,
) -> tuple[ModeGate, ...]:
    """Evaluate revocable UI gates; a green UI state is never broker authority by itself."""
    if binding.terminal_identity_key != terminal.installation.identity_key:
        raise ValueError("qualification binding belongs to another terminal")
    if binding.server != terminal.account.server or binding.account_mode is not terminal.account.mode:
        raise ValueError("qualification binding belongs to another account environment")
    if binding.symbol.upper() != symbol.symbol.upper() or binding.strategy_hash != strategy.strategy_hash:
        raise ValueError("qualification binding belongs to another strategy selection")

    exact_proof = proof is not None and proof.binding_fingerprint == binding.fingerprint
    backtest_reasons: list[str] = []
    if not terminal.connected:
        backtest_reasons.append("terminal_not_connected")
    if not strategy.backtest_executable:
        backtest_reasons.append(f"strategy_not_backtest_executable:{strategy.stage.value}")
    if not strategy.compatible_with(symbol):
        backtest_reasons.append("strategy_symbol_incompatible")

    demo_reasons = list(backtest_reasons)
    if terminal.account.mode is not AccountMode.DEMO:
        demo_reasons.append("selected_account_is_not_demo")
    if not exact_proof:
        demo_reasons.append("exact_backtest_proof_missing")
    else:
        if not proof.backtest_passed:
            demo_reasons.append("backtest_not_passed")
        if not proof.native_parity_passed:
            demo_reasons.append("native_mt5_parity_not_passed")
        if not proof.user_demo_confirmation:
            demo_reasons.append("demo_terminal_not_user_confirmed")
    if not terminal.account.trade_allowed or not terminal.account.expert_trading_allowed:
        demo_reasons.append("demo_trading_permission_unavailable")

    live_reasons: list[str] = []
    if not live_engineering_authorized:
        live_reasons.append("live_execution_not_implemented")
    if terminal.account.mode is not AccountMode.REAL:
        live_reasons.append("selected_account_is_not_live")
    if strategy.stage is not StrategyStage.LIVE_ELIGIBLE:
        live_reasons.append("strategy_not_live_eligible")
    if not exact_proof:
        live_reasons.append("exact_demo_proof_missing")
    else:
        if not proof.backtest_passed:
            live_reasons.append("backtest_not_passed")
        if not proof.native_parity_passed:
            live_reasons.append("native_mt5_parity_not_passed")
        if not proof.demo_campaign_passed:
            live_reasons.append("demo_campaign_not_passed")
        if not proof.six_desk_campaign_passed:
            live_reasons.append("six_desk_campaign_not_passed")
        if not proof.user_live_confirmation:
            live_reasons.append("live_activation_not_user_confirmed")

    return (
        ModeGate(OperatingMode.BACKTEST, not backtest_reasons, tuple(backtest_reasons)),
        ModeGate(OperatingMode.DEMO, not demo_reasons, tuple(dict.fromkeys(demo_reasons))),
        ModeGate(OperatingMode.LIVE, not live_reasons, tuple(dict.fromkeys(live_reasons))),
    )
