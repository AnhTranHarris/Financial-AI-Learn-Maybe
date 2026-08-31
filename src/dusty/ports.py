from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Protocol

from .core import EvidenceItem, EvidenceSnapshot, HealthState


class ReasoningCore(Protocol):
    def reason(self, *args: Any, **kwargs: Any) -> Any: ...


class EvidenceProvider(Protocol):
    name: str

    def collect(self, symbol: str, at: datetime) -> Iterable[EvidenceItem]: ...


class ModelProvider(Protocol):
    name: str

    def forecast(self, symbol: str, at: datetime) -> dict[str, Any]: ...


class JournalStore(Protocol):
    def append(self, record: Any) -> None: ...
    def records(self, person_id: str | None = None) -> list[Any]: ...


class MT5Worker(Protocol):
    @property
    def health(self) -> HealthState: ...


class ResearchWorker(Protocol):
    @property
    def health(self) -> HealthState: ...


class OperatorAPI(Protocol):
    def status(self) -> dict[str, Any]: ...


class LLMProvider(Protocol):
    name: str

    def complete(self, prompt: str) -> str: ...


class DustyCore(Protocol):
    def snapshot(self) -> EvidenceSnapshot: ...
