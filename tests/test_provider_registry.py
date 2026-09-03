from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dusty.provider_registry import (
    FORECAST_PROVIDER_SPECS,
    ProviderHealth,
    ProviderRegistry,
    default_provider_root,
)


class ProviderRegistryTests(unittest.TestCase):
    def test_default_root_uses_explicit_environment_override(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {"DUSTY_PROVIDER_ROOT": temporary}):
                self.assertEqual(default_provider_root(), Path(temporary))

    def test_discovery_is_read_only_and_requires_isolated_python(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "Chronos2" / ".venv" / "Scripts").mkdir(parents=True)
            (root / "Chronos2" / ".venv" / "Scripts" / "python.exe").touch()
            (root / "Kronos").mkdir()

            snapshots = ProviderRegistry(root).discover()
            by_id = {snapshot.spec.provider_id: snapshot for snapshot in snapshots}

            self.assertIs(by_id["chronos2"].health, ProviderHealth.INSTALLED)
            self.assertTrue(by_id["chronos2"].selectable)
            self.assertIs(by_id["kronos-small"].health, ProviderHealth.INCOMPLETE)
            self.assertFalse(by_id["kronos-small"].selectable)
            self.assertIs(by_id["timesfm-2.5"].health, ProviderHealth.MISSING)
            self.assertFalse(by_id["timesfm-2.5"].selectable)

    def test_specs_never_grant_broker_or_promotion_authority(self):
        for spec in FORECAST_PROVIDER_SPECS:
            with self.subTest(provider_id=spec.provider_id):
                self.assertFalse(spec.broker_write_authority)
                self.assertFalse(spec.promotion_authority)
                self.assertEqual(spec.mode.value, "research_only")
                self.assertIn("research_evidence", spec.capabilities)

    def test_three_unique_installed_forecast_slots_validate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for spec in FORECAST_PROVIDER_SPECS:
                scripts = root / spec.directory_name / ".venv" / "Scripts"
                scripts.mkdir(parents=True)
                (scripts / "python.exe").touch()

            registry = ProviderRegistry(root)
            selected = registry.validate_forecast_slots(
                ("chronos2", "kronos-small", "timesfm-2.5")
            )
            self.assertEqual(selected, ("chronos2", "kronos-small", "timesfm-2.5"))

    def test_none_slots_are_allowed_but_duplicates_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scripts = root / "Chronos2" / ".venv" / "Scripts"
            scripts.mkdir(parents=True)
            (scripts / "python.exe").touch()
            registry = ProviderRegistry(root)

            self.assertEqual(
                registry.validate_forecast_slots(("chronos2", None, None)),
                ("chronos2", None, None),
            )
            with self.assertRaisesRegex(ValueError, "duplicate_forecast_provider_selection"):
                registry.validate_forecast_slots(("chronos2", "chronos2", None))

    def test_missing_or_unknown_provider_cannot_be_selected(self):
        with tempfile.TemporaryDirectory() as temporary:
            registry = ProviderRegistry(Path(temporary))
            with self.assertRaisesRegex(ValueError, "forecast_provider_not_installed:chronos2"):
                registry.validate_forecast_slots(("chronos2", None, None))
            with self.assertRaisesRegex(ValueError, "unknown_forecast_provider:not-real"):
                registry.validate_forecast_slots(("not-real", None, None))

    def test_snapshot_serialization_keeps_safety_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = ProviderRegistry(Path(temporary)).discover()[0].as_dict()
            self.assertEqual(snapshot["kind"], "forecast")
            self.assertEqual(snapshot["mode"], "research_only")
            self.assertFalse(snapshot["broker_write_authority"])
            self.assertFalse(snapshot["promotion_authority"])
            self.assertFalse(snapshot["selectable"])


if __name__ == "__main__":
    unittest.main()
