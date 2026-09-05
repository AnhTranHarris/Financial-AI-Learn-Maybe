from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from dusty.replay_cache import (
    DeterministicReplayCache,
    ReplayCacheKey,
    ReplayLookupStatus,
)


def _fp(label: str) -> str:
    return sha256(label.encode("utf-8")).hexdigest()


def _key(*, software: str = "a" * 40, engine: str = "engine") -> ReplayCacheKey:
    return ReplayCacheKey(
        execution_fingerprint=_fp("execution"),
        dataset_fingerprint=_fp("dataset"),
        engine_fingerprint=_fp(engine),
        software_commit=software,
        input_fingerprints=(_fp("bars"), _fp("manifest")),
        deterministic_seed=163,
    )


class M163DeterministicReplayCacheTests(unittest.TestCase):
    def test_exact_identity_round_trip_rehashes_bytes(self) -> None:
        with TemporaryDirectory() as temp:
            cache = DeterministicReplayCache(temp)
            key = _key()
            now = datetime(2026, 9, 5, 3, 30, tzinfo=timezone.utc)
            record = cache.put(key, b'{"status":"pass"}', content_type="application/json", now=now)
            lookup = cache.get(key)
            self.assertEqual(lookup.status, ReplayLookupStatus.HIT)
            self.assertEqual(lookup.data, b'{"status":"pass"}')
            self.assertEqual(lookup.record, record)
            self.assertTrue(cache.integrity_check()[0])
            self.assertFalse(cache.broker_write_authorized)
            self.assertFalse(cache.promotion_authorized)
            cache.close()

    def test_software_or_engine_drift_is_a_cache_miss(self) -> None:
        with TemporaryDirectory() as temp:
            cache = DeterministicReplayCache(temp)
            cache.put(
                _key(),
                b"same output",
                content_type="application/octet-stream",
                now=datetime.now(timezone.utc),
            )
            self.assertEqual(cache.get(_key(software="b" * 40)).status, ReplayLookupStatus.MISS)
            self.assertEqual(cache.get(_key(engine="engine-v2")).status, ReplayLookupStatus.MISS)
            cache.close()

    def test_same_replay_key_cannot_silently_change_output(self) -> None:
        with TemporaryDirectory() as temp:
            cache = DeterministicReplayCache(temp)
            key = _key()
            now = datetime.now(timezone.utc)
            cache.put(key, b"first", content_type="application/octet-stream", now=now)
            with self.assertRaises(RuntimeError):
                cache.put(key, b"different", content_type="application/octet-stream", now=now)
            self.assertEqual(cache.get(key).data, b"first")
            cache.close()

    def test_corrupt_blob_is_never_returned_and_is_invalidated(self) -> None:
        with TemporaryDirectory() as temp:
            cache = DeterministicReplayCache(temp)
            key = _key()
            record = cache.put(
                key,
                b"trusted-cache-bytes",
                content_type="application/octet-stream",
                now=datetime.now(timezone.utc),
            )
            blob_path = Path(temp) / "blobs" / record.blob_sha256[:2] / record.blob_sha256[2:4] / f"{record.blob_sha256}.bin"
            blob_path.write_bytes(b"tampered")
            corrupt = cache.get(key)
            self.assertEqual(corrupt.status, ReplayLookupStatus.CORRUPT)
            self.assertIsNone(corrupt.data)
            self.assertEqual(cache.get(key).status, ReplayLookupStatus.MISS)
            cache.close()

    def test_content_addressing_deduplicates_identical_bytes_across_exact_keys(self) -> None:
        with TemporaryDirectory() as temp:
            cache = DeterministicReplayCache(temp)
            now = datetime.now(timezone.utc)
            first = cache.put(_key(), b"shared", content_type="text/plain", now=now)
            second_key = ReplayCacheKey(
                execution_fingerprint=_fp("execution-2"),
                dataset_fingerprint=_fp("dataset"),
                engine_fingerprint=_fp("engine"),
                software_commit="a" * 40,
                input_fingerprints=(_fp("bars"),),
                deterministic_seed=163,
            )
            second = cache.put(second_key, b"shared", content_type="text/plain", now=now)
            self.assertEqual(first.blob_sha256, second.blob_sha256)
            blobs = list((Path(temp) / "blobs").rglob("*.bin"))
            self.assertEqual(len(blobs), 1)
            cache.close()

    def test_input_order_is_part_of_execution_identity(self) -> None:
        first = _key()
        second = ReplayCacheKey(
            execution_fingerprint=first.execution_fingerprint,
            dataset_fingerprint=first.dataset_fingerprint,
            engine_fingerprint=first.engine_fingerprint,
            software_commit=first.software_commit,
            input_fingerprints=tuple(reversed(first.input_fingerprints)),
            deterministic_seed=first.deterministic_seed,
        )
        self.assertNotEqual(first.fingerprint, second.fingerprint)

    def test_cache_key_rejects_non_sha_and_non_commit_identities(self) -> None:
        with self.assertRaises(ValueError):
            ReplayCacheKey("bad", _fp("d"), _fp("e"), "a" * 40, ())
        with self.assertRaises(ValueError):
            ReplayCacheKey(_fp("x"), _fp("d"), _fp("e"), "short", ())


if __name__ == "__main__":
    unittest.main()
