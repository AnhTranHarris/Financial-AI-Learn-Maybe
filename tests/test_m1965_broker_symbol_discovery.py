from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
import unittest

from dusty.broker_symbol_discovery import BrokerAwareStrategyDiscoveryService
from dusty.strategy_discovery import (
    DiscoveryMode,
    DiscoveryStatus,
    StrategyDiscoveryConfig,
)
from dusty.strategy_estate import StrategyEstateUpdate
from dusty.strategy_estate_builder import (
    EstatePopulationResult,
    EstatePopulationRow,
    EstatePopulationStatus,
)
from dusty.vibe_research_contract import (
    EXPECTED_VIBE_VERSION,
    PROTOCOL,
    PROVIDER_ID,
    VibeResearchEvidence,
    VibeResearchResult,
    VibeResearchStatus,
)


H = lambda ch: ch * 64
NOW = datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)


def available(tool: str, payload: dict[str, object], marker: str = "a") -> VibeResearchResult:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return VibeResearchResult(
        VibeResearchStatus.AVAILABLE,
        evidence=VibeResearchEvidence(
            PROTOCOL,
            PROVIDER_ID,
            tool,
            EXPECTED_VIBE_VERSION,
            H(marker),
            H("b"),
            H("c"),
            text,
        ),
    )


def catalog_result() -> VibeResearchResult:
    items = [
        {
            "id": "alpha:momentum",
            "nickname": "Momentum continuation",
            "columns_required": ["close", "rsi"],
            "theme": ["momentum"],
            "frequency": ["M15"],
            "formula_latex": "RSI > 55",
        },
        {
            "id": "alpha:breakout",
            "nickname": "Volatility breakout",
            "columns_required": ["close", "atr"],
            "theme": ["breakout"],
            "frequency": ["M15"],
            "formula_latex": "ATR expansion",
        },
        {
            "id": "alpha:reversal",
            "nickname": "Mean reversal",
            "columns_required": ["close", "rsi"],
            "theme": ["mean reversion"],
            "frequency": ["M30"],
            "formula_latex": "RSI < 30",
        },
    ]
    return available(
        "list_strategies",
        {"status": "ok", "result": {"total": len(items), "items": items}},
    )


def web_result(marker: str = "d") -> VibeResearchResult:
    return available(
        "web_search",
        {
            "status": "ok",
            "result": {
                "items": [
                    {
                        "title": "Research lead",
                        "url": "https://example.com/research",
                        "snippet": "strategy research lead",
                    }
                ]
            },
        },
        marker,
    )


class FakeContractor:
    def __init__(self):
        self.calls: list[tuple[str, dict[str, object]]] = []

    def invoke(self, tool: str, arguments: dict[str, object]) -> VibeResearchResult:
        self.calls.append((tool, dict(arguments)))
        if tool == "list_strategies":
            return catalog_result()
        if tool == "web_search":
            return web_result()
        raise AssertionError(f"unexpected tool: {tool}")


class FakeBuilder:
    def __init__(self):
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def populate(self, proposals, **kwargs):
        rows_in = tuple(proposals)
        self.calls.append((rows_in, dict(kwargs)))
        rows = tuple(
            EstatePopulationRow(
                row.proposal_id,
                EstatePopulationStatus.ADDED,
                reconstruction_fingerprint=H("e"),
            )
            for row in rows_in
        )
        update = (
            StrategyEstateUpdate(
                Path(kwargs["estate_path"]),
                H("f"),
                len(rows),
                len(rows),
            )
            if rows
            else None
        )
        return EstatePopulationResult(rows, update)


class FakeApplication:
    def __init__(self, symbols):
        self.symbols = tuple(symbols)

    def view(self):
        return SimpleNamespace(symbols=self.symbols)


def option(
    symbol: str,
    *,
    custom: bool = False,
    trade_mode: int = 4,
    tick_size: float = 0.0001,
    volume_min: float = 0.01,
):
    return SimpleNamespace(
        symbol=symbol,
        custom=custom,
        trade_mode=trade_mode,
        tick_size=tick_size,
        volume_min=volume_min,
    )


class M1965BrokerSymbolDiscoveryTests(unittest.TestCase):
    def config(self, root: Path) -> StrategyDiscoveryConfig:
        return StrategyDiscoveryConfig(
            root / "VibeTrading",
            root / "work",
            root / "estate.json",
            root / "state.json",
            root / "reports",
            catalog_limit=100,
            max_reconstructions=6,
        )

    def service(self, root: Path, contractor: FakeContractor, builder: FakeBuilder):
        return BrokerAwareStrategyDiscoveryService(
            self.config(root),
            contractor_factory=lambda *_: contractor,
            builder=builder,
            digest_resolver=lambda _tag: H("d"),
        )

    def test_broker_inventory_filters_custom_disabled_and_invalid_symbols(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            service = self.service(root, FakeContractor(), FakeBuilder())
            service.bind_application(
                FakeApplication(
                    (
                        option("EURUSD"),
                        option("XAUUSD", custom=True),
                        option("NASUSD", trade_mode=0),
                        option("GBPUSD", tick_size=0.0),
                        option("USDJPY", volume_min=0.0),
                        option("AUDUSD"),
                    )
                )
            )
            self.assertEqual(service.broker_symbol_universe(), ("EURUSD", "AUDUSD"))

    def test_new_strategy_scan_rotates_broker_symbols_and_uses_one_exact_ollama_target(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            contractor = FakeContractor()
            builder = FakeBuilder()
            service = self.service(root, contractor, builder)
            service.bind_application(
                FakeApplication(
                    tuple(option(symbol) for symbol in ("EURUSD", "XAUUSD", "NASUSD", "GBPUSD"))
                )
            )

            first = service.discover(DiscoveryMode.NEW_STRATEGIES, now=NOW)
            self.assertEqual(first.status, DiscoveryStatus.COMPLETED)
            summary = service.last_symbol_summary
            self.assertIsNotNone(summary)
            assert summary is not None
            self.assertEqual(summary.symbols_scanned, ("EURUSD", "XAUUSD", "NASUSD"))
            self.assertEqual(summary.reconstruction_target, "EURUSD")
            self.assertEqual(summary.web_queries_completed, 3)
            self.assertEqual(builder.calls[0][1]["allowed_symbols"], ("EURUSD",))
            self.assertLessEqual(len(builder.calls[0][0]), 2)

            second = service.discover(DiscoveryMode.NEW_STRATEGIES, now=NOW)
            summary = service.last_symbol_summary
            assert summary is not None
            self.assertEqual(summary.symbols_scanned, ("GBPUSD", "EURUSD", "XAUUSD"))
            self.assertEqual(summary.reconstruction_target, "GBPUSD")
            self.assertEqual(builder.calls[1][1]["allowed_symbols"], ("GBPUSD",))

            symbol_queries = [
                args["query"]
                for tool, args in contractor.calls
                if tool == "web_search" and args["query"].split()[0] in {"EURUSD", "XAUUSD", "NASUSD", "GBPUSD"}
            ]
            self.assertEqual(len(symbol_queries), 6)

    def test_both_mode_caps_total_web_searches_and_keeps_website_leads_outside_ollama(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            contractor = FakeContractor()
            builder = FakeBuilder()
            service = self.service(root, contractor, builder)
            service.bind_application(
                FakeApplication(tuple(option(symbol) for symbol in ("EURUSD", "XAUUSD", "NASUSD", "GBPUSD")))
            )

            result = service.discover(DiscoveryMode.BOTH, now=NOW)
            self.assertEqual(result.status, DiscoveryStatus.COMPLETED)
            summary = service.last_symbol_summary
            assert summary is not None
            self.assertEqual(summary.symbols_scanned, ("EURUSD", "XAUUSD"))
            self.assertEqual(summary.reconstruction_target, "EURUSD")
            self.assertEqual(summary.web_queries_completed, 2)
            all_web = [args for tool, args in contractor.calls if tool == "web_search"]
            # Two exact-symbol scouts + the existing three cross-symbol lead queries.
            self.assertEqual(len(all_web), 5)
            self.assertEqual(builder.calls[0][1]["allowed_symbols"], ("EURUSD",))
            self.assertTrue(summary.report_path and summary.report_path.is_file())
            report = json.loads(summary.report_path.read_text(encoding="utf-8"))
            self.assertIn("not send arbitrary web snippets directly to Ollama", report["policy"])
            self.assertEqual(report["query_budget"]["parallel_requests"], 1)
            self.assertEqual(report["query_budget"]["ollama_reconstructions"], 2)
            self.assertFalse(report["authority"]["broker_write"])

    def test_no_connected_terminal_falls_back_without_mutating_mt5(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp)
            service = self.service(root, FakeContractor(), FakeBuilder())
            self.assertEqual(service.broker_symbol_universe(), self.config(root).allowed_symbols)
            self.assertFalse(service.broker_write_authority)
            self.assertFalse(service.live_write_authority)


if __name__ == "__main__":
    unittest.main()
