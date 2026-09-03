from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dusty.research_cycle import ResearchCycle, ResearchStage


class ResearchCycleTests(unittest.TestCase):
    def request(self) -> dict[str, object]:
        return {
            "code_commit": "abc123",
            "symbol": "EURUSD",
            "window": {"start": "2026-08-01T00:00:00+00:00", "end": "2026-08-08T00:00:00+00:00"},
        }

    def test_completed_identical_cycle_is_a_verified_cache_hit(self) -> None:
        calls: list[str] = []

        def acquire(_: dict[str, object]) -> dict[str, object]:
            calls.append("acquire")
            return {"bars": 500}

        def evaluate(prior: dict[str, object]) -> dict[str, object]:
            calls.append("evaluate")
            self.assertEqual(prior["acquire"], {"bars": 500})
            return {"net_pnl": -10.0}

        stages = (
            ResearchStage("acquire", "1", acquire),
            ResearchStage("evaluate", "1", evaluate),
        )
        with TemporaryDirectory() as temporary:
            cycle = ResearchCycle(Path(temporary))
            first = cycle.run(self.request(), stages)
            self.assertFalse(first.cache_hit)
            self.assertEqual(calls, ["acquire", "evaluate"])

            calls.clear()
            second = cycle.run(self.request(), stages)
            self.assertTrue(second.cache_hit)
            self.assertEqual(second.cycle_fingerprint, first.cycle_fingerprint)
            self.assertEqual(calls, [])
            self.assertEqual(second.output_map()["evaluate"], {"net_pnl": -10.0})

    def test_interrupted_cycle_resumes_from_first_missing_stage(self) -> None:
        calls: list[str] = []
        fail_once = {"value": True}

        def acquire(_: dict[str, object]) -> dict[str, object]:
            calls.append("acquire")
            return {"bars": 500}

        def evaluate(prior: dict[str, object]) -> dict[str, object]:
            calls.append("evaluate")
            self.assertEqual(prior["acquire"], {"bars": 500})
            if fail_once["value"]:
                fail_once["value"] = False
                raise RuntimeError("synthetic interruption")
            return {"trades": 12}

        stages = (
            ResearchStage("acquire", "1", acquire),
            ResearchStage("evaluate", "1", evaluate),
        )
        with TemporaryDirectory() as temporary:
            cycle = ResearchCycle(Path(temporary))
            with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                cycle.run(self.request(), stages)
            self.assertEqual(calls, ["acquire", "evaluate"])

            calls.clear()
            result = cycle.run(self.request(), stages)
            self.assertEqual(result.reused_stages, ("acquire",))
            self.assertEqual(calls, ["evaluate"])
            self.assertEqual(result.output_map()["evaluate"], {"trades": 12})

    def test_corrupt_checkpoint_fails_closed_instead_of_recomputing(self) -> None:
        calls = 0

        def acquire(_: dict[str, object]) -> dict[str, object]:
            nonlocal calls
            calls += 1
            return {"bars": 500}

        with TemporaryDirectory() as temporary:
            cycle = ResearchCycle(Path(temporary))
            stage = ResearchStage("acquire", "1", acquire)
            result = cycle.run(self.request(), (stage,))
            checkpoint = result.run_directory / "00-acquire.json"
            envelope = json.loads(checkpoint.read_text(encoding="utf-8"))
            envelope["payload"] = {"bars": 999}
            checkpoint.write_text(json.dumps(envelope), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "payload_corrupt"):
                cycle.run(self.request(), (stage,))
            self.assertEqual(calls, 1)

    def test_stage_version_is_part_of_experiment_identity(self) -> None:
        with TemporaryDirectory() as temporary:
            cycle = ResearchCycle(Path(temporary))
            request = self.request()
            one = cycle.run(request, (ResearchStage("evaluate", "1", lambda _: {"v": 1}),))
            two = cycle.run(request, (ResearchStage("evaluate", "2", lambda _: {"v": 2}),))
            self.assertNotEqual(one.cycle_fingerprint, two.cycle_fingerprint)
            self.assertNotEqual(one.run_directory, two.run_directory)

    def test_code_commit_and_unique_simple_stage_names_are_required(self) -> None:
        with TemporaryDirectory() as temporary:
            cycle = ResearchCycle(Path(temporary))
            with self.assertRaisesRegex(ValueError, "requires_code_commit"):
                cycle.run({}, (ResearchStage("evaluate", "1", lambda _: {}),))
            with self.assertRaisesRegex(ValueError, "unique"):
                cycle.run(
                    self.request(),
                    (
                        ResearchStage("evaluate", "1", lambda _: {}),
                        ResearchStage("evaluate", "1", lambda _: {}),
                    ),
                )
            with self.assertRaisesRegex(ValueError, "name_must_be_simple"):
                ResearchStage("bad/name", "1", lambda _: {})


if __name__ == "__main__":
    unittest.main()
