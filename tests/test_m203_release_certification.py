import unittest
from dataclasses import replace

from dusty.release_certification import (
    ReleaseArtifact,
    ReleaseCertificationStatus,
    ReleaseManifest,
    RollbackEvidence,
    certify_release_rollback,
)


def _sha(ch: str) -> str:
    return ch * 64


def _predecessor() -> str:
    return _sha("d")


def _release() -> ReleaseManifest:
    return ReleaseManifest(
        release_id="dusty-v1-rc1",
        source_commit="a" * 40,
        source_tree_sha256=_sha("b"),
        python_abi="cp311-win_amd64",
        state_schema_version=1,
        artifacts=(
            ReleaseArtifact("dist/dusty.whl", _sha("c"), 1234),
            ReleaseArtifact("manifest/runtime.json", _sha("e"), 321),
        ),
        predecessor_release_fingerprint=_predecessor(),
    )


def _evidence(release=None) -> RollbackEvidence:
    release = release or _release()
    return RollbackEvidence(
        candidate_release_fingerprint=release.fingerprint,
        predecessor_release_fingerprint=_predecessor(),
        predecessor_artifacts_available=True,
        predecessor_artifacts_integrity_ok=True,
        state_backup_available=True,
        rollback_schema_supported=True,
        clean_process_stop_proven=True,
        exact_predecessor_restart_proven=True,
        post_rollback_state_integrity_ok=True,
    )


class M203ReleaseCertificationTests(unittest.TestCase):
    def test_complete_release_and_rollback_certifies(self):
        result = certify_release_rollback(_release(), _evidence())
        self.assertIs(result.status, ReleaseCertificationStatus.CERTIFIED)
        self.assertEqual(result.blockers, ())

    def test_candidate_identity_mismatch_rejects(self):
        evidence = replace(_evidence(), candidate_release_fingerprint=_sha("f"))
        result = certify_release_rollback(_release(), evidence)
        self.assertIs(result.status, ReleaseCertificationStatus.REJECTED)

    def test_predecessor_identity_mismatch_rejects(self):
        evidence = replace(_evidence(), predecessor_release_fingerprint=_sha("f"))
        result = certify_release_rollback(_release(), evidence)
        self.assertIs(result.status, ReleaseCertificationStatus.REJECTED)

    def test_missing_predecessor_binding_is_pending(self):
        release = replace(_release(), predecessor_release_fingerprint=None)
        evidence = replace(_evidence(release), candidate_release_fingerprint=release.fingerprint)
        result = certify_release_rollback(release, evidence)
        self.assertIs(result.status, ReleaseCertificationStatus.PENDING)
        self.assertIn("predecessor_release_not_bound", result.blockers)

    def test_missing_rollback_proofs_are_pending(self):
        fields = (
            "predecessor_artifacts_available",
            "state_backup_available",
            "rollback_schema_supported",
            "clean_process_stop_proven",
            "exact_predecessor_restart_proven",
        )
        for field in fields:
            result = certify_release_rollback(
                _release(), replace(_evidence(), **{field: False})
            )
            self.assertIs(result.status, ReleaseCertificationStatus.PENDING, field)

    def test_corrupt_predecessor_artifacts_reject(self):
        result = certify_release_rollback(
            _release(), replace(_evidence(), predecessor_artifacts_integrity_ok=False)
        )
        self.assertIs(result.status, ReleaseCertificationStatus.REJECTED)

    def test_post_rollback_state_corruption_rejects(self):
        result = certify_release_rollback(
            _release(), replace(_evidence(), post_rollback_state_integrity_ok=False)
        )
        self.assertIs(result.status, ReleaseCertificationStatus.REJECTED)

    def test_artifact_order_does_not_change_release_identity(self):
        left = _release()
        right = replace(left, artifacts=tuple(reversed(left.artifacts)))
        self.assertEqual(left.fingerprint, right.fingerprint)

    def test_duplicate_artifact_paths_fail_closed(self):
        artifact = ReleaseArtifact("dist/dusty.whl", _sha("c"), 1234)
        with self.assertRaises(ValueError):
            replace(_release(), artifacts=(artifact, artifact))

    def test_parent_path_escape_is_rejected(self):
        with self.assertRaises(ValueError):
            ReleaseArtifact("../secret.txt", _sha("a"), 1)

    def test_release_manifest_requires_exact_source_identity(self):
        with self.assertRaises(ValueError):
            replace(_release(), source_commit="short")

    def test_certification_has_no_operational_authority(self):
        result = certify_release_rollback(_release(), _evidence())
        self.assertFalse(result.install_authority)
        self.assertFalse(result.rollback_authority)
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.credential_access_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_fingerprint_is_deterministic(self):
        left = certify_release_rollback(_release(), _evidence())
        right = certify_release_rollback(_release(), _evidence())
        self.assertEqual(left.fingerprint, right.fingerprint)


if __name__ == "__main__":
    unittest.main()
