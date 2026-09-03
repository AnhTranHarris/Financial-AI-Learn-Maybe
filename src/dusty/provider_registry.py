from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import os
from pathlib import Path
from typing import Iterable


class ProviderKind(str, Enum):
    FORECAST = "forecast"


class ProviderMode(str, Enum):
    OFF = "off"
    RESEARCH_ONLY = "research_only"


class ProviderHealth(str, Enum):
    MISSING = "missing"
    INCOMPLETE = "incomplete"
    INSTALLED = "installed"


def _exact_revision(value: str | None, label: str) -> None:
    if value is None:
        return
    if len(value) != 40 or any(
        character not in "0123456789abcdefABCDEF" for character in value
    ):
        raise ValueError(f"{label}_must_be_exact_git_sha")


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    display_name: str
    kind: ProviderKind
    model_id: str
    license_id: str
    directory_name: str
    model_revision: str | None = None
    runtime_version: str | None = None
    source_revision: str | None = None
    tokenizer_id: str | None = None
    tokenizer_revision: str | None = None
    capabilities: tuple[str, ...] = ("forecast", "research_evidence")
    mode: ProviderMode = ProviderMode.RESEARCH_ONLY
    broker_write_authority: bool = False
    promotion_authority: bool = False

    def __post_init__(self) -> None:
        if self.kind is not ProviderKind.FORECAST:
            raise ValueError("provider_registry_v1_supports_forecast_contractors_only")
        if self.mode is not ProviderMode.RESEARCH_ONLY:
            raise ValueError("forecast_contractors_must_be_research_only")
        if self.broker_write_authority or self.promotion_authority:
            raise ValueError("forecast_contractors_cannot_receive_trading_or_promotion_authority")
        _exact_revision(self.model_revision, "provider_model_revision")
        _exact_revision(self.source_revision, "provider_source_revision")
        _exact_revision(self.tokenizer_revision, "provider_tokenizer_revision")
        if self.runtime_version is not None and not self.runtime_version.strip():
            raise ValueError("provider_runtime_version_cannot_be_blank")
        if (self.tokenizer_id is None) != (self.tokenizer_revision is None):
            raise ValueError("provider_tokenizer_identity_must_be_complete")


@dataclass(frozen=True)
class ProviderSnapshot:
    spec: ProviderSpec
    root: Path
    python_executable: Path
    health: ProviderHealth
    detail: str

    @property
    def selectable(self) -> bool:
        return self.health is ProviderHealth.INSTALLED

    def as_dict(self) -> dict[str, object]:
        return {
            "provider_id": self.spec.provider_id,
            "display_name": self.spec.display_name,
            "kind": self.spec.kind.value,
            "model_id": self.spec.model_id,
            "model_revision": self.spec.model_revision,
            "runtime_version": self.spec.runtime_version,
            "source_revision": self.spec.source_revision,
            "tokenizer_id": self.spec.tokenizer_id,
            "tokenizer_revision": self.spec.tokenizer_revision,
            "license_id": self.spec.license_id,
            "capabilities": list(self.spec.capabilities),
            "mode": self.spec.mode.value,
            "broker_write_authority": self.spec.broker_write_authority,
            "promotion_authority": self.spec.promotion_authority,
            "root": str(self.root),
            "python_executable": str(self.python_executable),
            "health": self.health.value,
            "detail": self.detail,
            "selectable": self.selectable,
        }


FORECAST_PROVIDER_SPECS: tuple[ProviderSpec, ...] = (
    ProviderSpec(
        provider_id="chronos2",
        display_name="Amazon Chronos-2",
        kind=ProviderKind.FORECAST,
        model_id="amazon/chronos-2",
        model_revision="29ec3766d36d6f73f0696f85560a422f50e8498c",
        runtime_version="2.3.1",
        license_id="Apache-2.0",
        directory_name="Chronos2",
    ),
    ProviderSpec(
        provider_id="kronos-small",
        display_name="Kronos-small",
        kind=ProviderKind.FORECAST,
        model_id="NeoQuasar/Kronos-small",
        model_revision="901c26c1332695a2a8f243eb2f37243a37bea320",
        runtime_version="source@67b630e67f6a18c9e9be918d9b4337c960db1e9a",
        source_revision="67b630e67f6a18c9e9be918d9b4337c960db1e9a",
        tokenizer_id="NeoQuasar/Kronos-Tokenizer-base",
        tokenizer_revision="0e0117387f39004a9016484a186a908917e22426",
        license_id="MIT",
        directory_name="Kronos",
        capabilities=("forecast", "research_evidence", "ohlc_kline"),
    ),
    ProviderSpec(
        provider_id="timesfm-2.5",
        display_name="TimesFM 2.5",
        kind=ProviderKind.FORECAST,
        model_id="google/timesfm-2.5-200m-transformers",
        model_revision="5a9806b9b291fad9233b5249d88263f1846304d3",
        runtime_version="transformers==5.16.1",
        license_id="Apache-2.0",
        directory_name="TimesFM25",
    ),
)


def default_provider_root() -> Path:
    override = os.environ.get("DUSTY_PROVIDER_ROOT", "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / "DustyProviders"


class ProviderRegistry:
    """Read-only discovery for optional, isolated research contractors.

    Discovery deliberately does not import provider packages or start model
    processes. A provider becomes merely INSTALLED when its isolated Python
    executable is present. Runtime model health belongs to an adapter layer
    and must fail independently of Dusty's deterministic core.
    """

    def __init__(
        self,
        root: Path | None = None,
        specs: Iterable[ProviderSpec] = FORECAST_PROVIDER_SPECS,
    ) -> None:
        self.root = (root or default_provider_root()).expanduser()
        self._specs = tuple(specs)
        ids = [spec.provider_id for spec in self._specs]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_provider_id")

    @staticmethod
    def _python_path(provider_root: Path) -> Path:
        return provider_root / ".venv" / "Scripts" / "python.exe"

    def discover(self) -> tuple[ProviderSnapshot, ...]:
        snapshots: list[ProviderSnapshot] = []
        for spec in self._specs:
            provider_root = self.root / spec.directory_name
            python_executable = self._python_path(provider_root)
            if not provider_root.exists():
                health = ProviderHealth.MISSING
                detail = "provider_directory_missing"
            elif not python_executable.is_file():
                health = ProviderHealth.INCOMPLETE
                detail = "isolated_python_missing"
            else:
                health = ProviderHealth.INSTALLED
                detail = "isolated_python_present_runtime_not_started"
            snapshots.append(
                ProviderSnapshot(
                    spec=spec,
                    root=provider_root,
                    python_executable=python_executable,
                    health=health,
                    detail=detail,
                )
            )
        return tuple(snapshots)

    def snapshot(self, provider_id: str) -> ProviderSnapshot:
        for snapshot in self.discover():
            if snapshot.spec.provider_id == provider_id:
                return snapshot
        raise KeyError(provider_id)

    def validate_forecast_slots(
        self,
        provider_ids: Iterable[str | None],
        *,
        slot_count: int = 3,
    ) -> tuple[str | None, ...]:
        selected = tuple(provider_ids)
        if len(selected) != slot_count:
            raise ValueError(f"exactly_{slot_count}_forecast_slots_required")
        active = tuple(provider_id for provider_id in selected if provider_id is not None)
        if len(active) != len(set(active)):
            raise ValueError("duplicate_forecast_provider_selection")
        available = {snapshot.spec.provider_id: snapshot for snapshot in self.discover()}
        for provider_id in active:
            snapshot = available.get(provider_id)
            if snapshot is None:
                raise ValueError(f"unknown_forecast_provider:{provider_id}")
            if not snapshot.selectable:
                raise ValueError(f"forecast_provider_not_installed:{provider_id}")
        return selected
