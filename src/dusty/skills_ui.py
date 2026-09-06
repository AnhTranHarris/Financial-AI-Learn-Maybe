from __future__ import annotations

"""M196.5 launcher that projects the strategy estate into the existing PC UI.

The Tk UI and LocalResearchRuntime remain unchanged. This wrapper can use an
explicit hash-pinned ``--strategy-library`` snapshot or, by default, the local
persistent Strategy Estate. It pins the exact reconstruction snapshot in the
environment inherited by spawned workers and generates a temporary metadata
catalog for display/selection. The catalog itself remains incapable of
execution; ``reviewed_strategies.resolve_research_package`` must independently
resolve the same hash-pinned package.
"""

import argparse
from hashlib import sha256
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Iterable

from . import basic_ui
from .strategy_catalog import StrategyCatalogEntry
from .strategy_estate import default_strategy_estate_path
from .strategy_library_snapshot import (
    LIBRARY_PATH_ENV,
    LIBRARY_SHA256_ENV,
    load_reconstruction_library,
)
from .strategy_taxonomy import catalog_entry_for_reconstruction
from .trading_skills import strategy_catalog_projection



def _catalog_row(entry: StrategyCatalogEntry) -> dict[str, object]:
    return {
        "strategy_id": entry.strategy_id,
        "title": entry.title,
        "strategy_hash": entry.strategy_hash,
        "stage": entry.stage.value,
        "allowed_symbols": list(entry.allowed_symbols),
        "allowed_category_prefixes": list(entry.allowed_category_prefixes),
        "universal_symbol_compatibility": entry.universal_symbol_compatibility,
        "source_url": entry.source_url,
        "timeframe": entry.timeframe,
    }



def projected_catalog_rows(entries: Iterable[StrategyCatalogEntry]) -> list[dict[str, object]]:
    return [_catalog_row(entry) for entry in entries]



def _has_catalog_argument(argv: list[str]) -> bool:
    return any(value == "--catalog" or value.startswith("--catalog=") for value in argv)



def _quant_catalog(reconstructions: tuple[object, ...]) -> tuple[StrategyCatalogEntry, ...]:
    quant = {
        row.candidate_spec.strategy_id: catalog_entry_for_reconstruction(row)
        for row in reconstructions
    }
    projected = strategy_catalog_projection(reconstructions=reconstructions)
    return tuple(quant.get(entry.strategy_id, entry) for entry in projected)



def main(argv: list[str] | None = None) -> int:
    raw = list(argv) if argv is not None else None
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--strategy-library", type=Path)
    parser.add_argument("--strategy-estate", type=Path)
    parser.add_argument("--no-strategy-estate", action="store_true")
    known, remaining = parser.parse_known_args(raw)

    if known.strategy_library is not None and known.strategy_estate is not None:
        parser.error("--strategy-library and --strategy-estate are mutually exclusive")
    if known.no_strategy_estate and (known.strategy_library is not None or known.strategy_estate is not None):
        parser.error("--no-strategy-estate cannot be combined with a strategy snapshot")

    library: Path | None = known.strategy_library or known.strategy_estate
    if library is None and not known.no_strategy_estate:
        estate = default_strategy_estate_path()
        if estate.is_file():
            library = estate

    if library is None:
        # Wrapper-only flags must not leak into the legacy UI parser.  With no
        # special flag present, ``remaining`` is exactly the caller's original
        # basic-UI argument list, preserving the pre-estate behavior.
        return basic_ui.main(remaining)
    if _has_catalog_argument(remaining):
        parser.error("strategy snapshots and --catalog are mutually exclusive")

    library = library.resolve()
    content = library.read_bytes()
    digest = sha256(content).hexdigest()
    reconstructions = load_reconstruction_library(library, digest)
    catalog = _quant_catalog(reconstructions)

    previous_path = os.environ.get(LIBRARY_PATH_ENV)
    previous_digest = os.environ.get(LIBRARY_SHA256_ENV)
    try:
        os.environ[LIBRARY_PATH_ENV] = str(library)
        os.environ[LIBRARY_SHA256_ENV] = digest
        with TemporaryDirectory(prefix="dusty-strategy-catalog-") as temporary:
            catalog_path = Path(temporary) / "catalog.json"
            catalog_path.write_text(
                json.dumps(projected_catalog_rows(catalog), sort_keys=True, indent=2) + "\n",
                encoding="utf-8",
            )
            return basic_ui.main([*remaining, "--catalog", str(catalog_path)])
    finally:
        if previous_path is None:
            os.environ.pop(LIBRARY_PATH_ENV, None)
        else:
            os.environ[LIBRARY_PATH_ENV] = previous_path
        if previous_digest is None:
            os.environ.pop(LIBRARY_SHA256_ENV, None)
        else:
            os.environ[LIBRARY_SHA256_ENV] = previous_digest


if __name__ == "__main__":
    raise SystemExit(main())
