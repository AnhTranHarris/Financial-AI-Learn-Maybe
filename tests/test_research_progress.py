"""Deterministic regression coverage for Windows progress-file sharing conflicts."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from dusty.local_research import (
    _CAMPAIGN_PROGRESS_LIMIT, _atomic_json, _campaign_progress_paths,
    _publish_campaign_progress, _read_campaign_progress, _seal_campaign_queue,
)


def progress(done=0):
    return {
        "queue": [{"state": "COMPLETED" if i < done else "PENDING"} for i in range(30)],
        "case_sha256": {}, "request_sha256": "a" * 64, "promotion_eligible": False,
    }


def spawned_publisher(directory, advance, published):
    """Handshake holds every previous snapshot open during the next publication."""
    for done in range(1, 31):
        if not advance.wait(20):
            raise RuntimeError("test_reader_handshake_timed_out")
        advance.clear()
        _publish_campaign_progress(Path(directory), progress(done))
        published.set()


class ImmutableProgressTests(unittest.TestCase):
    def test_all_campaign_updates_use_new_destinations_and_retain_old_bytes(self):
        with tempfile.TemporaryDirectory() as root, ExitStack() as readers:
            directory = Path(root)
            replace = os.replace

            def publish(source, destination):
                self.assertFalse(destination.exists(), "must never replace a readable snapshot")
                return replace(source, destination)

            held = []
            with patch("dusty.local_research.os.replace", side_effect=publish):
                for sequence in range(61):
                    payload = progress(sequence // 2)
                    path = _publish_campaign_progress(directory, payload)
                    self.assertEqual(path.name, f"queue-{sequence:03d}.json")
                    held.append((readers.enter_context(path.open("rb")), payload))
                    self.assertEqual(_read_campaign_progress(directory), payload)
            for stream, payload in held:
                self.assertEqual(json.loads(stream.read()), payload)

    def test_reader_sees_previous_snapshot_while_next_file_is_unpublished(self):
        with tempfile.TemporaryDirectory() as root, ThreadPoolExecutor(max_workers=1) as pool:
            directory = Path(root)
            _publish_campaign_progress(directory, progress())
            ready, release = threading.Event(), threading.Event()
            replace = os.replace

            def delayed_publish(source, destination):
                ready.set()
                if not release.wait(10):
                    raise RuntimeError("test_publication_handshake_timed_out")
                return replace(source, destination)

            with patch("dusty.local_research.os.replace", side_effect=delayed_publish):
                pending = pool.submit(_publish_campaign_progress, directory, progress(1))
                try:
                    self.assertTrue(ready.wait(10))
                    self.assertEqual(_read_campaign_progress(directory), progress())
                    self.assertEqual(len(_campaign_progress_paths(directory)), 1)
                finally:
                    release.set()
                pending.result(timeout=10)
            self.assertEqual(_read_campaign_progress(directory), progress(1))

    def test_spawned_publisher_succeeds_with_every_prior_reader_held_open(self):
        with tempfile.TemporaryDirectory() as root, ExitStack() as readers:
            directory = Path(root)
            first = _publish_campaign_progress(directory, progress())
            held = [(readers.enter_context(first.open("rb")), progress())]
            context = multiprocessing.get_context("spawn")
            advance, published = context.Event(), context.Event()
            worker = context.Process(target=spawned_publisher, args=(root, advance, published))
            worker.start()
            try:
                for done in range(1, 31):
                    advance.set()
                    self.assertTrue(published.wait(20), "worker failed to publish while reader was open")
                    published.clear()
                    self.assertEqual(_read_campaign_progress(directory), progress(done))
                    latest = _campaign_progress_paths(directory)[-1]
                    held.append((readers.enter_context(latest.open("rb")), progress(done)))
                worker.join(timeout=20)
                self.assertEqual(worker.exitcode, 0)
                for stream, payload in held:
                    self.assertEqual(json.loads(stream.read()), payload)
            finally:
                if worker.is_alive():
                    worker.terminate()
                worker.join(timeout=20)
                worker.close()

    @unittest.skipUnless(os.name == "nt", "requires actual Windows file-sharing semantics")
    def test_windows_open_reader_blocks_old_replace_but_not_new_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            first = _publish_campaign_progress(directory, progress())
            with first.open("rb") as held:
                # Negative control: reproduce the former failure deterministically.
                with self.assertRaises(PermissionError) as blocked:
                    _atomic_json(first, progress(1))
                self.assertIn(blocked.exception.winerror, (5, 32))
                second = _publish_campaign_progress(directory, progress(1))
                self.assertNotEqual(first, second)
                self.assertEqual(json.loads(held.read()), progress())
                self.assertEqual(_read_campaign_progress(directory), progress(1))

    def test_missing_or_only_temporary_progress_is_not_readable(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            with self.assertRaises(FileNotFoundError):
                _read_campaign_progress(directory)
            _atomic_json(directory / "queue-000.json.interrupted.tmp", progress(30))
            _atomic_json(directory / "queue.json", progress(30))  # legacy files are not migrated
            with self.assertRaises(FileNotFoundError):
                _read_campaign_progress(directory)
            self.assertEqual(_publish_campaign_progress(directory, progress()).name, "queue-000.json")

    def test_invalid_latest_snapshot_does_not_fall_back_to_older_state(self):
        for invalid in (b"{", b"[]", b"null"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as root:
                directory = Path(root)
                _publish_campaign_progress(directory, progress())
                (directory / "queue-001.json").write_bytes(invalid)
                with self.assertRaises(ValueError):
                    _read_campaign_progress(directory)

    def test_failed_publication_propagates_without_retry_or_changing_prior_snapshot(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            first = _publish_campaign_progress(directory, progress())
            original = first.read_bytes()
            with patch("dusty.local_research.os.replace", side_effect=PermissionError("denied")) as replace:
                with self.assertRaises(PermissionError):
                    _publish_campaign_progress(directory, progress(1))
                replace.assert_called_once()
            self.assertEqual(first.read_bytes(), original)
            self.assertEqual(_read_campaign_progress(directory), progress())
            self.assertFalse((directory / "queue-001.json").exists())

    def test_journal_limit_cannot_overwrite_previous_evidence(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            for _ in range(_CAMPAIGN_PROGRESS_LIMIT):
                _publish_campaign_progress(directory, progress())
            before = {p.name: p.read_bytes() for p in _campaign_progress_paths(directory)}
            with self.assertRaisesRegex(ValueError, "limit_exceeded"):
                _publish_campaign_progress(directory, progress(30))
            self.assertEqual(before, {p.name: p.read_bytes() for p in _campaign_progress_paths(directory)})

    def test_sealing_retains_readable_snapshots_case_hashes_and_is_idempotent(self):
        for state in ("FAILED", "CANCELLED", "TIMED_OUT"):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as root:
                directory = Path(root)
                payload = progress(1)
                payload["queue"][1]["state"] = "RUNNING"
                payload["case_sha256"] = {"case-000.json": "b" * 64}
                first = _publish_campaign_progress(directory, payload)
                with first.open("rb") as held:
                    _seal_campaign_queue(directory, state)
                    sealed = _read_campaign_progress(directory)
                    self.assertEqual([r["state"] for r in sealed["queue"]],
                                     ["COMPLETED", state] + ["NOT_RUN"] * 28)
                    self.assertEqual(sealed["case_sha256"], payload["case_sha256"])
                    self.assertEqual(sealed["request_sha256"], payload["request_sha256"])
                    self.assertEqual(json.loads(held.read()), payload)
                    _seal_campaign_queue(directory, state)
                    self.assertEqual(len(_campaign_progress_paths(directory)), 2)


if __name__ == "__main__":
    unittest.main()
