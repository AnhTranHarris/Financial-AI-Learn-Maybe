from __future__ import annotations

import os
import sys
import unittest

from dusty.provider_process import IsolatedJsonLineWorker, ProviderWorkerState


class ProviderProcessTests(unittest.TestCase):
    def test_process_is_reused_across_two_transactions_and_stops_cleanly(self):
        child = (
            "import sys\n"
            "print('READY', flush=True)\n"
            "for line in sys.stdin:\n"
            "    print('ACK:' + line.strip(), flush=True)\n"
        )
        worker = IsolatedJsonLineWorker(
            (sys.executable, "-u", "-c", child),
            environment=os.environ,
            startup_timeout_seconds=5,
            request_timeout_seconds=5,
        )
        state, ready = worker.start()
        self.assertIs(state, ProviderWorkerState.READY)
        self.assertEqual(ready, "READY")
        pid = worker.pid
        state, first = worker.transact("one")
        self.assertIs(state, ProviderWorkerState.READY)
        self.assertEqual(first, "ACK:one")
        self.assertEqual(worker.pid, pid)
        state, second = worker.transact("two")
        self.assertIs(state, ProviderWorkerState.READY)
        self.assertEqual(second, "ACK:two")
        self.assertEqual(worker.pid, pid)
        self.assertIs(worker.stop(), ProviderWorkerState.STOPPED)
        self.assertIs(worker.state, ProviderWorkerState.STOPPED)

    def test_utf8_stderr_does_not_kill_reader_on_windows_locale(self):
        child = (
            "import sys\n"
            "sys.stderr.buffer.write(b'progress:\\xe5\\x8d\\x8d\\n')\n"
            "sys.stderr.buffer.flush()\n"
            "print('READY', flush=True)\n"
            "for line in sys.stdin:\n"
            "    print('ACK:' + line.strip(), flush=True)\n"
        )
        worker = IsolatedJsonLineWorker(
            (sys.executable, "-u", "-c", child),
            environment=os.environ,
            startup_timeout_seconds=5,
            request_timeout_seconds=5,
        )
        try:
            state, ready = worker.start()
            self.assertIs(state, ProviderWorkerState.READY)
            self.assertEqual(ready, "READY")
            state, response = worker.transact("probe")
            self.assertIs(state, ProviderWorkerState.READY)
            self.assertEqual(response, "ACK:probe")
            self.assertIn("progress:卍", worker.stderr_excerpt)
        finally:
            self.assertIs(worker.stop(), ProviderWorkerState.STOPPED)

    def test_resource_failure_is_classified_without_affecting_parent(self):
        child = (
            "import sys\n"
            "print('MemoryError: out of memory', file=sys.stderr, flush=True)\n"
        )
        worker = IsolatedJsonLineWorker(
            (sys.executable, "-u", "-c", child),
            environment=os.environ,
            startup_timeout_seconds=5,
            request_timeout_seconds=5,
        )
        state, ready = worker.start()
        self.assertIs(state, ProviderWorkerState.RESOURCE_BLOCKED)
        self.assertIsNone(ready)
        self.assertIn("out of memory", worker.stderr_excerpt.lower())
        self.assertIs(worker.stop(), ProviderWorkerState.STOPPED)

    def test_child_exit_after_ready_fails_closed(self):
        child = (
            "import sys\n"
            "print('READY', flush=True)\n"
            "sys.stdin.readline()\n"
        )
        worker = IsolatedJsonLineWorker(
            (sys.executable, "-u", "-c", child),
            environment=os.environ,
            startup_timeout_seconds=5,
            request_timeout_seconds=5,
        )
        self.assertIs(worker.start()[0], ProviderWorkerState.READY)
        state, response = worker.transact("request")
        self.assertIs(state, ProviderWorkerState.FAILED)
        self.assertIsNone(response)
        self.assertIs(worker.stop(), ProviderWorkerState.STOPPED)

    def test_timeout_and_command_contracts_are_bounded(self):
        with self.assertRaisesRegex(ValueError, "provider_startup_timeout"):
            IsolatedJsonLineWorker(
                (sys.executable,),
                environment=os.environ,
                startup_timeout_seconds=4,
                request_timeout_seconds=5,
            )
        with self.assertRaisesRegex(ValueError, "provider_request_timeout"):
            IsolatedJsonLineWorker(
                (sys.executable,),
                environment=os.environ,
                startup_timeout_seconds=5,
                request_timeout_seconds=601,
            )
        with self.assertRaisesRegex(ValueError, "provider_worker_command_invalid"):
            IsolatedJsonLineWorker(
                tuple(),
                environment=os.environ,
                startup_timeout_seconds=5,
                request_timeout_seconds=5,
            )


if __name__ == "__main__":
    unittest.main()
