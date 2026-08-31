from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from dusty.library import ArtifactClass, ArtifactRecord, SourceRecord, SQLiteLearningLibrary
from dusty.resource import JobPriority, ResourceBudget, ResourceSnapshot, ResourceState, admit_job


NOW = datetime(2026, 8, 31, 4, 0, tzinfo=timezone.utc)


class ResourceGovernorTests(unittest.TestCase):
    def setUp(self):
        self.budget = ResourceBudget(min_free_disk_bytes=10_000)

    def test_m24_resource_pressure_throttles_low_priority_work_first(self):
        healthy = ResourceSnapshot(1_000_000, 500_000, 100_000, cpu_percent=20)
        pressured = ResourceSnapshot(1_000_000, 100_000, 100_000, cpu_percent=20)
        exhausted = ResourceSnapshot(1_000_000, 500_000, 1_000, cpu_percent=20)
        self.assertIs(admit_job(JobPriority.TRAINING, healthy, self.budget).state, ResourceState.GREEN)
        self.assertFalse(admit_job(JobPriority.BACKTEST, pressured, self.budget).admitted)
        self.assertTrue(admit_job(JobPriority.JOURNAL, exhausted, self.budget).admitted)
        self.assertFalse(admit_job(JobPriority.EVIDENCE, exhausted, self.budget).admitted)


class LearningLibraryTests(unittest.TestCase):
    def test_m24_library_is_persistent_disk_first_and_reclaim_aware(self):
        with self.assertRaises(ValueError):
            SQLiteLearningLibrary(":memory:")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "dusty-library.db"
            library = SQLiteLearningLibrary(path)
            source = SourceRecord("src-1", "quant-research", "https://example.test/s", NOW, "abc", "public")
            library.register_source(source)
            library.remember_artifact(
                ArtifactRecord.of(
                    "knowledge-1",
                    "normalized_strategy",
                    ArtifactClass.IRREPLACEABLE,
                    200,
                    NOW,
                    source_id="src-1",
                )
            )
            library.remember_artifact(
                ArtifactRecord.of(
                    "feature-cache-1",
                    "feature_cache",
                    ArtifactClass.RECONSTRUCTIBLE,
                    5000,
                    NOW,
                )
            )
            self.assertEqual([item.artifact_id for item in library.iter_artifacts(batch_size=1)], ["feature-cache-1", "knowledge-1"])
            self.assertEqual(library.reclaim_candidates(), ("feature-cache-1",))
            self.assertEqual(library.bytes_by_class()[ArtifactClass.IRREPLACEABLE], 200)
            self.assertTrue(library.integrity_ok())
            library.close()

            reopened = SQLiteLearningLibrary(path)
            self.assertEqual(len(tuple(reopened.iter_artifacts())), 2)
            reopened.close()


if __name__ == "__main__":
    unittest.main()
