from __future__ import annotations

from datetime import datetime, timedelta, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dusty.artifact_vault import (
    ArtifactIntegrityError,
    ArtifactKind,
    ResearchArtifactVault,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


class M164ResearchArtifactVaultTests(unittest.TestCase):
    def test_store_reopen_and_read_preserves_append_only_evidence(self) -> None:
        with TemporaryDirectory() as temp:
            now = datetime(2026, 9, 5, 3, 40, tzinfo=timezone.utc)
            vault = ResearchArtifactVault(temp)
            record = vault.store_bytes(
                b'{"native":true}',
                kind=ArtifactKind.EVALUATION,
                content_type="application/json",
                producer_fingerprint=_fp("m161-executor"),
                subject_fingerprint=_fp("strategy"),
                source_fingerprints=(_fp("manifest"), _fp("dataset")),
                now=now,
            )
            self.assertEqual(vault.read_bytes(record.record_fingerprint), b'{"native":true}')
            self.assertFalse(vault.broker_write_authorized)
            self.assertFalse(vault.promotion_authorized)
            self.assertFalse(hasattr(vault, "delete"))
            vault.close()

            reopened = ResearchArtifactVault(temp)
            loaded = reopened.get_record(record.record_fingerprint)
            self.assertEqual(loaded, record)
            self.assertEqual(reopened.list_subject(_fp("strategy")), (record,))
            self.assertTrue(reopened.integrity_check()[0])
            reopened.close()

    def test_same_blob_can_have_distinct_append_only_provenance_records(self) -> None:
        with TemporaryDirectory() as temp:
            vault = ResearchArtifactVault(temp)
            now = datetime.now(timezone.utc)
            first = vault.store_bytes(
                b"same bytes",
                kind=ArtifactKind.LOG,
                content_type="text/plain",
                producer_fingerprint=_fp("producer-a"),
                subject_fingerprint=_fp("subject"),
                now=now,
            )
            second = vault.store_bytes(
                b"same bytes",
                kind=ArtifactKind.LOG,
                content_type="text/plain",
                producer_fingerprint=_fp("producer-b"),
                subject_fingerprint=_fp("subject"),
                now=now + timedelta(seconds=1),
            )
            self.assertEqual(first.blob_sha256, second.blob_sha256)
            self.assertNotEqual(first.record_fingerprint, second.record_fingerprint)
            self.assertEqual(len(list((Path(temp) / "blobs").rglob("*.bin"))), 1)
            self.assertEqual(len(vault.list_subject(_fp("subject"))), 2)
            vault.close()

    def test_source_provenance_is_canonical_as_an_unordered_evidence_set(self) -> None:
        with TemporaryDirectory() as temp:
            vault = ResearchArtifactVault(temp)
            now = datetime.now(timezone.utc)
            kwargs = dict(
                data=b"artifact",
                kind=ArtifactKind.MANIFEST,
                content_type="text/plain",
                producer_fingerprint=_fp("producer"),
                subject_fingerprint=_fp("subject"),
                now=now,
            )
            first = vault.store_bytes(
                source_fingerprints=(_fp("b"), _fp("a"), _fp("a")),
                **kwargs,
            )
            second = vault.store_bytes(
                source_fingerprints=(_fp("a"), _fp("b")),
                **kwargs,
            )
            self.assertEqual(first.record_fingerprint, second.record_fingerprint)
            self.assertEqual(first.source_fingerprints, tuple(sorted((_fp("a"), _fp("b")))))
            vault.close()

    def test_tampered_blob_is_never_silently_dropped_or_reclassified_as_missing_cache(self) -> None:
        with TemporaryDirectory() as temp:
            vault = ResearchArtifactVault(temp)
            record = vault.store_bytes(
                b"evidence",
                kind=ArtifactKind.DEALS,
                content_type="text/csv",
                producer_fingerprint=_fp("mt5"),
                subject_fingerprint=_fp("strategy"),
                now=datetime.now(timezone.utc),
            )
            blob = Path(temp) / "blobs" / record.blob_sha256[:2] / record.blob_sha256[2:4] / f"{record.blob_sha256}.bin"
            blob.write_bytes(b"tampered")
            with self.assertRaises(ArtifactIntegrityError):
                vault.read_bytes(record.record_fingerprint)
            ok, errors = vault.integrity_check()
            self.assertFalse(ok)
            self.assertTrue(any(record.record_fingerprint in row for row in errors))
            self.assertIsNotNone(vault.get_record(record.record_fingerprint))
            vault.close()

    def test_missing_record_is_distinct_from_corrupt_existing_record(self) -> None:
        with TemporaryDirectory() as temp:
            vault = ResearchArtifactVault(temp)
            self.assertIsNone(vault.get_record(_fp("missing")))
            with self.assertRaises(KeyError):
                vault.read_bytes(_fp("missing"))
            vault.close()

    def test_vault_rejects_unhashed_provenance(self) -> None:
        with TemporaryDirectory() as temp:
            vault = ResearchArtifactVault(temp)
            with self.assertRaises(ValueError):
                vault.store_bytes(
                    b"x",
                    kind=ArtifactKind.OTHER,
                    content_type="application/octet-stream",
                    producer_fingerprint="not-a-hash",
                    subject_fingerprint=_fp("subject"),
                    now=datetime.now(timezone.utc),
                )
            vault.close()


if __name__ == "__main__":
    unittest.main()
