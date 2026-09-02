from __future__ import annotations

import argparse
import sys
import unittest


GROUPS = {
    "reasoning_research": (
        "test_backtest_hardening.py",
        "test_backtest_statistics.py",
        "test_cognition.py",
        "test_core.py",
        "test_experience.py",
        "test_forecasting.py",
        "test_m86_m88.py",
        "test_m89_m92.py",
        "test_research.py",
        "test_statistical_hardening.py",
        "test_validation_mt5.py",
    ),
    "curriculum_event": (
        "test_acquisition.py",
        "test_curriculum.py",
        "test_curriculum_streaming.py",
        "test_event_sources.py",
        "test_information_value.py",
        "test_providers_scenarios.py",
        "test_scenario_research.py",
        "test_self_development.py",
    ),
    "governance_storage": (
        "test_connected_research.py",
        "test_research_comparison.py",
        "test_capital_governance.py",
        "test_governance_invariants.py",
        "test_journal_learning.py",
        "test_m68_m69.py",
        "test_resource_library.py",
    ),
    "mt5_certification": (
        "test_certification.py",
        "test_indicator_parity_contract.py",
        "test_m67_tester_contract.py",
        "test_mt5_symbol_snapshot.py",
        "test_mt5_training.py",
        "test_m76_m79.py",
        "test_m80_m83.py",
        "test_m84_m85.py",
        "test_m93_m94.py",
        "test_m95.py",
        "test_operations_certification.py",
        "test_pre_demo_certification.py",
        "test_strategy_ir_hardening.py",
        "test_tester_parity.py",
    ),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("group", choices=tuple(GROUPS))
    args = parser.parse_args()
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for pattern in GROUPS[args.group]:
        suite.addTests(loader.discover("tests", pattern=pattern))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
