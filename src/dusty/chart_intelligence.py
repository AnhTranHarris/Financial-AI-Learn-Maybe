from __future__ import annotations

import json
import math
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from typing import Mapping

from .analytical_tools import ToolOrigin


class ChartObjectKind(StrEnum):
    HORIZONTAL_LINE = "horizontal_line"
    VERTICAL_LINE = "vertical_line"
    TREND_LINE = "trend_line"
    TREND_BY_ANGLE = "trend_by_angle"
    EQUIDISTANT_CHANNEL = "equidistant_channel"
    REGRESSION_CHANNEL = "regression_channel"
    STANDARD_DEVIATION_CHANNEL = "standard_deviation_channel"
    FIBONACCI_RETRACEMENT = "fibonacci_retracement"
    FIBONACCI_EXPANSION = "fibonacci_expansion"
    FIBONACCI_TIME_ZONES = "fibonacci_time_zones"
    FIBONACCI_ARCS = "fibonacci_arcs"
    FIBONACCI_FAN = "fibonacci_fan"
    GANN_LINE = "gann_line"
    GANN_GRID = "gann_grid"
    GANN_FAN = "gann_fan"
    ANDREWS_PITCHFORK = "andrews_pitchfork"
    ELLIOTT_WAVE = "elliott_wave"
    CYCLE_LINES = "cycle_lines"
    RECTANGLE_ZONE = "rectangle_zone"
    ARROW = "arrow"
    TEXT = "text"
    INTERFACE_CONTROL = "interface_control"


_ANALYTICAL_KINDS = frozenset(ChartObjectKind) - {
    ChartObjectKind.TEXT,
    ChartObjectKind.INTERFACE_CONTROL,
}


@dataclass(frozen=True, slots=True)
class ChartAnchor:
    at: datetime
    price: float

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("chart anchor time must be timezone-aware")
        if not math.isfinite(self.price) or self.price <= 0:
            raise ValueError("chart anchor price must be finite and positive")


@dataclass(frozen=True, slots=True)
class ChartObjectSpec:
    object_id: str
    kind: ChartObjectKind
    symbol: str
    timeframe: str
    anchors: tuple[ChartAnchor, ...]
    known_at: datetime
    origin: ToolOrigin
    levels: tuple[float, ...] = ()
    label: str = ""

    def __post_init__(self) -> None:
        if not self.object_id.strip() or not self.symbol.strip() or not self.timeframe.strip():
            raise ValueError("chart object requires identity, symbol and timeframe")
        if self.known_at.tzinfo is None or self.known_at.utcoffset() is None:
            raise ValueError("chart object known_at must be timezone-aware")
        if any(anchor.at > self.known_at for anchor in self.anchors):
            raise ValueError("chart object cannot use future anchors")
        if any(not math.isfinite(level) for level in self.levels):
            raise ValueError("chart object levels must be finite")
        if self.kind is ChartObjectKind.HORIZONTAL_LINE and len(self.anchors) != 1:
            raise ValueError("horizontal line requires one anchor")
        if self.kind in {ChartObjectKind.TREND_LINE, ChartObjectKind.TREND_BY_ANGLE} and len(self.anchors) != 2:
            raise ValueError("trend line requires two anchors")
        if self.kind in {ChartObjectKind.FIBONACCI_RETRACEMENT, ChartObjectKind.FIBONACCI_EXPANSION} and len(self.anchors) < 2:
            raise ValueError("Fibonacci object requires at least two anchors")

    @property
    def analytical(self) -> bool:
        return self.kind in _ANALYTICAL_KINDS

    @property
    def fingerprint(self) -> str:
        payload = {
            "object_id": self.object_id,
            "kind": self.kind.value,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "anchors": [(row.at.isoformat(), row.price) for row in self.anchors],
            "known_at": self.known_at.isoformat(),
            "origin": self.origin.value,
            "levels": self.levels,
            "label": self.label,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def value_at(self, at: datetime) -> float:
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("chart query time must be timezone-aware")
        if self.kind is ChartObjectKind.HORIZONTAL_LINE:
            return self.anchors[0].price
        if self.kind not in {ChartObjectKind.TREND_LINE, ChartObjectKind.TREND_BY_ANGLE}:
            raise ValueError(f"{self.kind.value} does not define one price line")
        left, right = self.anchors
        seconds = (right.at - left.at).total_seconds()
        if seconds == 0:
            raise ValueError("trend line anchors cannot share a timestamp")
        slope = (right.price - left.price) / seconds
        return left.price + slope * (at - left.at).total_seconds()


class ValueUnit(StrEnum):
    PRICE = "price"
    POINTS = "points"
    OSCILLATOR = "oscillator"
    SCALAR = "scalar"
    BOOLEAN = "boolean"


class NodeOperation(StrEnum):
    INPUT = "input"
    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"
    CROSS_ABOVE = "cross_above"
    CROSS_BELOW = "cross_below"
    ALL = "all"
    ANY = "any"
    NOT = "not"


@dataclass(frozen=True, slots=True)
class AnalysisNode:
    node_id: str
    operation: NodeOperation
    unit: ValueUnit
    inputs: tuple[str, ...] = ()
    source_key: str = ""

    def __post_init__(self) -> None:
        if not self.node_id.strip():
            raise ValueError("analysis node requires identity")
        if self.operation is NodeOperation.INPUT:
            if not self.source_key.strip() or self.inputs:
                raise ValueError("input node requires source key and no dependencies")
        elif self.source_key:
            raise ValueError("computed node cannot declare a source key")
        required = {
            NodeOperation.NOT: 1,
            NodeOperation.ADD: 2,
            NodeOperation.SUBTRACT: 2,
            NodeOperation.MULTIPLY: 2,
            NodeOperation.DIVIDE: 2,
            NodeOperation.GREATER_THAN: 2,
            NodeOperation.LESS_THAN: 2,
            NodeOperation.CROSS_ABOVE: 2,
            NodeOperation.CROSS_BELOW: 2,
        }.get(self.operation)
        if required is not None and len(self.inputs) != required:
            raise ValueError(f"{self.operation.value} requires {required} inputs")
        if self.operation in {NodeOperation.ALL, NodeOperation.ANY} and len(self.inputs) < 1:
            raise ValueError("boolean aggregate requires inputs")


@dataclass(frozen=True, slots=True)
class AnalysisSnapshot:
    at: datetime
    values: tuple[tuple[str, float | bool], ...]
    known_at: tuple[tuple[str, datetime], ...] = ()

    @classmethod
    def of(
        cls,
        at: datetime,
        values: Mapping[str, float | bool],
        *,
        known_at: Mapping[str, datetime] | None = None,
    ) -> "AnalysisSnapshot":
        return cls(at, tuple(sorted(values.items())), tuple(sorted((known_at or {}).items())))

    def __post_init__(self) -> None:
        if self.at.tzinfo is None or self.at.utcoffset() is None:
            raise ValueError("analysis snapshot time must be timezone-aware")
        if len(dict(self.values)) != len(self.values):
            raise ValueError("analysis snapshot keys must be unique")
        for value in dict(self.values).values():
            if isinstance(value, float) and not math.isfinite(value):
                raise ValueError("analysis value must be finite")
        for key, known in self.known_at:
            if known.tzinfo is None or known.utcoffset() is None or known > self.at:
                raise ValueError(f"future or naive analysis evidence: {key}")


@dataclass(frozen=True, slots=True)
class MarketAnalysisGraph:
    nodes: tuple[AnalysisNode, ...]
    outputs: tuple[tuple[str, str], ...]
    tool_fingerprints: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        by_id = {node.node_id: node for node in self.nodes}
        if len(by_id) != len(self.nodes) or not self.outputs:
            raise ValueError("analysis graph requires unique nodes and outputs")
        resolved: set[str] = set()
        for node in self.nodes:
            if any(dependency not in resolved for dependency in node.inputs):
                raise ValueError("analysis graph must be topologically ordered and acyclic")
            resolved.add(node.node_id)
        if any(node_id not in by_id for _, node_id in self.outputs):
            raise ValueError("analysis graph output references unknown node")
        if len({name for name, _ in self.outputs}) != len(self.outputs):
            raise ValueError("analysis graph output names must be unique")
        if any(len(value) != 64 for value in self.tool_fingerprints):
            raise ValueError("analysis graph tool dependencies must be SHA-256 fingerprints")
        boolean_ops = {
            NodeOperation.GREATER_THAN,
            NodeOperation.LESS_THAN,
            NodeOperation.CROSS_ABOVE,
            NodeOperation.CROSS_BELOW,
            NodeOperation.ALL,
            NodeOperation.ANY,
            NodeOperation.NOT,
        }
        for node in self.nodes:
            if node.operation in boolean_ops and node.unit is not ValueUnit.BOOLEAN:
                raise ValueError("logical/comparison nodes must emit boolean values")
            dependency_units = tuple(by_id[key].unit for key in node.inputs)
            if node.operation in {NodeOperation.ALL, NodeOperation.ANY, NodeOperation.NOT} and any(
                unit is not ValueUnit.BOOLEAN for unit in dependency_units
            ):
                raise ValueError("boolean operation requires boolean dependencies")
            if node.operation in {
                NodeOperation.GREATER_THAN,
                NodeOperation.LESS_THAN,
                NodeOperation.CROSS_ABOVE,
                NodeOperation.CROSS_BELOW,
            }:
                if len(set(dependency_units)) != 1 or dependency_units[0] is ValueUnit.BOOLEAN:
                    raise ValueError("comparison/crossover requires matching numeric units")
            if node.operation in {NodeOperation.ADD, NodeOperation.SUBTRACT}:
                if len(set(dependency_units)) != 1 or dependency_units[0] is not node.unit:
                    raise ValueError("add/subtract requires matching input and output units")
            if node.operation is NodeOperation.MULTIPLY:
                if ValueUnit.BOOLEAN in dependency_units:
                    raise ValueError("multiply requires numeric dependencies")
                non_scalar = tuple(unit for unit in dependency_units if unit is not ValueUnit.SCALAR)
                expected = ValueUnit.SCALAR if not non_scalar else non_scalar[0]
                if len(non_scalar) > 1 or node.unit is not expected:
                    raise ValueError("multiply requires a scalar factor and dimensionally valid output")
            if node.operation is NodeOperation.DIVIDE:
                numerator, denominator = dependency_units
                if ValueUnit.BOOLEAN in dependency_units:
                    raise ValueError("divide requires numeric dependencies")
                if denominator is ValueUnit.SCALAR:
                    expected = numerator
                elif numerator is denominator:
                    expected = ValueUnit.SCALAR
                else:
                    raise ValueError("divide requires a scalar denominator or matching units")
                if node.unit is not expected:
                    raise ValueError("divide output unit is dimensionally invalid")

    @property
    def fingerprint(self) -> str:
        payload = {
            "nodes": [
                {
                    "node_id": node.node_id,
                    "operation": node.operation.value,
                    "unit": node.unit.value,
                    "inputs": node.inputs,
                    "source_key": node.source_key,
                }
                for node in self.nodes
            ],
            "outputs": self.outputs,
            "tools": self.tool_fingerprints,
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def evaluate(
        self,
        current: AnalysisSnapshot,
        *,
        previous: AnalysisSnapshot | None = None,
    ) -> dict[str, float | bool]:
        current_values = dict(current.values)
        previous_values = {} if previous is None else dict(previous.values)
        resolved: dict[str, float | bool] = {}
        previous_resolved: dict[str, float | bool] = {}
        for node in self.nodes:
            if node.operation is NodeOperation.INPUT:
                if node.source_key not in current_values:
                    raise ValueError(f"missing analysis input: {node.source_key}")
                resolved[node.node_id] = current_values[node.source_key]
                if node.source_key in previous_values:
                    previous_resolved[node.node_id] = previous_values[node.source_key]
                continue
            args = tuple(resolved[key] for key in node.inputs)
            if node.operation in {NodeOperation.CROSS_ABOVE, NodeOperation.CROSS_BELOW}:
                if previous is None or any(key not in previous_resolved for key in node.inputs):
                    resolved[node.node_id] = False
                else:
                    old = tuple(previous_resolved[key] for key in node.inputs)
                    resolved[node.node_id] = _cross(node.operation, old, args)
            else:
                resolved[node.node_id] = _calculate(node.operation, args)
                if previous is not None and all(key in previous_resolved for key in node.inputs):
                    old_args = tuple(previous_resolved[key] for key in node.inputs)
                    previous_resolved[node.node_id] = _calculate(node.operation, old_args)
        return {name: resolved[node_id] for name, node_id in self.outputs}


def _numbers(args: tuple[float | bool, ...]) -> tuple[float, ...]:
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in args):
        raise ValueError("numeric analysis operation received boolean")
    return tuple(float(value) for value in args)


def _booleans(args: tuple[float | bool, ...]) -> tuple[bool, ...]:
    if any(not isinstance(value, bool) for value in args):
        raise ValueError("boolean analysis operation received numeric value")
    return tuple(bool(value) for value in args)


def _calculate(operation: NodeOperation, args: tuple[float | bool, ...]) -> float | bool:
    if operation is NodeOperation.ADD:
        left, right = _numbers(args)
        return left + right
    if operation is NodeOperation.SUBTRACT:
        left, right = _numbers(args)
        return left - right
    if operation is NodeOperation.MULTIPLY:
        left, right = _numbers(args)
        return left * right
    if operation is NodeOperation.DIVIDE:
        left, right = _numbers(args)
        if right == 0:
            raise ValueError("analysis graph division by zero")
        return left / right
    if operation is NodeOperation.GREATER_THAN:
        left, right = _numbers(args)
        return left > right
    if operation is NodeOperation.LESS_THAN:
        left, right = _numbers(args)
        return left < right
    if operation is NodeOperation.ALL:
        return all(_booleans(args))
    if operation is NodeOperation.ANY:
        return any(_booleans(args))
    if operation is NodeOperation.NOT:
        return not _booleans(args)[0]
    raise ValueError(f"unsupported graph operation: {operation.value}")


def _cross(
    operation: NodeOperation,
    old: tuple[float | bool, ...],
    new: tuple[float | bool, ...],
) -> bool:
    old_left, old_right = _numbers(old)
    new_left, new_right = _numbers(new)
    if operation is NodeOperation.CROSS_ABOVE:
        return old_left <= old_right and new_left > new_right
    return old_left >= old_right and new_left < new_right
