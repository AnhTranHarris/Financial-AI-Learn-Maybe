from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from dusty.champion_registry import ChampionLifecycleState, FrozenChampionRecord
from dusty.single_desk_demo_certification import SingleDeskDemoCertification, SingleDeskDemoStatus
from dusty.trading_skills import SkillEvidenceKind, SkillEvidenceRef, build_trading_skill


NOW = datetime(2026, 9, 5, 20, 0, tzinfo=timezone.utc)
H = lambda ch: ch * 64


def champion() -> FrozenChampionRecord:
    return FrozenChampionRecord(
        "general-eurusd",
        "generation-demo-boundary",
        "trend",
        H("1"),
        H("2"),
        (H("3"),),
        "596d996e156b530697158eadc596bf475f64ad68",
        H("4"),
        H("5"),
        None,
        None,
        NOW - timedelta(days=10),
    )


class M1965DemoSkillBoundaryTests(unittest.TestCase):
    def test_certified_enum_without_real_runtime_cannot_create_demo_skill(self) -> None:
        champ = champion()
        synthetic = SingleDeskDemoCertification(
            SingleDeskDemoStatus.CERTIFIED,
            champ.fingerprint,
            H("6"),
            H("7"),
            (),
            (),
            (),
            (),
            H("8"),
            False,
        )
        refs = (
            SkillEvidenceRef(SkillEvidenceKind.ROBUSTNESS, champ.robustness_fingerprint),
            SkillEvidenceRef(SkillEvidenceKind.OTHER, champ.selection_evidence_fingerprint),
            SkillEvidenceRef(SkillEvidenceKind.DEMO, synthetic.certification_fingerprint),
        )
        with self.assertRaisesRegex(ValueError, "requires real M194 Demo runtime evidence"):
            build_trading_skill(
                champ,
                lifecycle_state=ChampionLifecycleState.ACTIVE,
                symbols=("EURUSD",),
                timeframes=("M15",),
                evidence=refs,
                demo_certification=synthetic,
                demo_runtime=None,
                created_at=NOW,
            )

    def test_skill_projection_has_no_path_to_live_eligible(self) -> None:
        champ = champion()
        refs = (
            SkillEvidenceRef(SkillEvidenceKind.ROBUSTNESS, champ.robustness_fingerprint),
            SkillEvidenceRef(SkillEvidenceKind.OTHER, champ.selection_evidence_fingerprint),
        )
        skill = build_trading_skill(
            champ,
            lifecycle_state=ChampionLifecycleState.ACTIVE,
            symbols=("EURUSD",),
            timeframes=("M15",),
            evidence=refs,
            demo_certification=None,
            created_at=NOW,
        )
        self.assertNotEqual(skill.stage.value, "live_eligible")
        self.assertFalse(skill.live_write_authority)
        self.assertFalse(skill.broker_write_authority)


if __name__ == "__main__":
    unittest.main()
