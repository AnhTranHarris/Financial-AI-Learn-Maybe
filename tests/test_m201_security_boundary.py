import unittest
from dataclasses import replace

from dusty.security_boundary import (
    SecurityBoundaryEvidence,
    SecurityBoundaryPolicy,
    SecurityBoundaryStatus,
    certify_security_boundary,
)


def _sha(ch: str) -> str:
    return ch * 64


def _evidence() -> SecurityBoundaryEvidence:
    return SecurityBoundaryEvidence(
        source_commit="a" * 40,
        terminal_executable_path=r"C:\Program Files\Coinexx MT5 Terminal\terminal64.exe",
        terminal_identity_fingerprint=_sha("b"),
        account_identity_fingerprint=_sha("c"),
        credential_fields_persisted=(),
        raw_account_login_persisted=False,
        model_endpoint="http://127.0.0.1:11434",
        broker_write_surfaces=(
            "src/dusty/demo_execution.py",
            "src/dusty/demo_execution_bridge.py",
        ),
        broad_process_termination_used=False,
        shell_execution_used=False,
        state_write_roots=(
            r"C:\Users\user\AppData\Local\DustyDragon\validation",
            r"C:\Users\user\AppData\Local\DustyDragon\strategy-estate",
        ),
        mql5_dll_imports_present=False,
        mql5_webrequest_present=False,
        generated_mt5_config_contains_credentials=False,
        live_write_surface_present=False,
        source_tree_integrity_ok=True,
    )


class M201SecurityBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.policy = SecurityBoundaryPolicy()

    def test_compliant_boundary_certifies(self):
        result = certify_security_boundary(self.policy, _evidence())
        self.assertIs(result.status, SecurityBoundaryStatus.CERTIFIED)
        self.assertEqual(result.blockers, ())

    def test_broker_credentials_are_forbidden(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), credential_fields_persisted=("password",)),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)
        self.assertIn("broker_credentials_persisted", result.blockers)

    def test_raw_account_login_cannot_be_persisted(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), raw_account_login_persisted=True),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)

    def test_generated_mt5_config_cannot_contain_credentials(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), generated_mt5_config_contains_credentials=True),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)

    def test_remote_model_endpoint_is_rejected(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), model_endpoint="https://example.com:11434"),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)
        self.assertIn("model_endpoint_not_localhost", result.blockers)

    def test_ipv6_loopback_model_endpoint_is_allowed(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), model_endpoint="http://[::1]:11434"),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.CERTIFIED)

    def test_unapproved_broker_write_surface_is_rejected(self):
        result = certify_security_boundary(
            self.policy,
            replace(
                _evidence(),
                broker_write_surfaces=(
                    "src/dusty/demo_execution.py",
                    "src/dusty/secret_live_sender.py",
                ),
            ),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)
        self.assertTrue(any(row.startswith("unapproved_broker_write_surface:") for row in result.blockers))

    def test_live_write_surface_before_final_authorization_is_rejected(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), live_write_surface_present=True),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)

    def test_broad_process_kill_is_rejected(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), broad_process_termination_used=True),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)

    def test_shell_execution_is_rejected(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), shell_execution_used=True),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)

    def test_state_write_outside_dusty_root_is_rejected(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), state_write_roots=(r"C:\Windows\Temp",)),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)
        self.assertTrue(any(row.startswith("state_write_outside_allowed_root:") for row in result.blockers))

    def test_mql5_dll_import_is_rejected_by_default(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), mql5_dll_imports_present=True),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)

    def test_mql5_webrequest_is_rejected_by_default(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), mql5_webrequest_present=True),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)

    def test_non_exact_terminal_path_is_pending_not_certified(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), terminal_executable_path=r"C:\MT5\launcher.exe"),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.PENDING)
        self.assertIn("exact_terminal_executable_not_proven", result.blockers)

    def test_source_integrity_failure_rejects(self):
        result = certify_security_boundary(
            self.policy,
            replace(_evidence(), source_tree_integrity_ok=False),
        )
        self.assertIs(result.status, SecurityBoundaryStatus.REJECTED)

    def test_certification_has_no_authority(self):
        result = certify_security_boundary(self.policy, _evidence())
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)
        self.assertFalse(result.promotion_authority)
        self.assertFalse(result.credential_access_authority)
        self.assertFalse(result.process_kill_authority)
        self.assertFalse(result.risk_override_authority)
        self.assertFalse(result.guardian_override_authority)

    def test_fingerprints_are_deterministic(self):
        left = certify_security_boundary(self.policy, _evidence())
        right = certify_security_boundary(self.policy, _evidence())
        self.assertEqual(left.fingerprint, right.fingerprint)
        self.assertEqual(left.evidence_fingerprint, right.evidence_fingerprint)


if __name__ == "__main__":
    unittest.main()
