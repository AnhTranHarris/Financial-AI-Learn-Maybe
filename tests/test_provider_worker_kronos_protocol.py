from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
import json
import unittest
from unittest.mock import patch

from dusty import provider_worker_kronos as worker


class KronosWorkerProtocolTests(unittest.TestCase):
    def test_persistent_worker_quarantines_third_party_stdout_from_protocol_channel(self):
        def fake_load_runtime():
            print("third-party startup chatter ✓")
            return object(), object(), object(), object(), worker.RUNTIME_VERSION

        def fake_validate_request(payload):
            self.assertEqual(payload, {"request": 1})
            return {"request": 1}

        def fake_run_loaded(*args, **kwargs):
            print("third-party inference chatter ✓")
            return {"event": "forecast_result", "ok": True}

        stdout = io.StringIO()
        stderr = io.StringIO()
        stdin = io.StringIO('{"request":1}\n')

        with (
            patch.object(worker, "_load_runtime", side_effect=fake_load_runtime),
            patch.object(worker, "_validate_request", side_effect=fake_validate_request),
            patch.object(worker, "_run_loaded", side_effect=fake_run_loaded),
            patch.object(worker.sys, "stdin", stdin),
            redirect_stdout(stdout),
            redirect_stderr(stderr),
        ):
            result = worker._run_persistent()

        self.assertEqual(result, 0)
        protocol_lines = [line for line in stdout.getvalue().splitlines() if line.strip()]
        self.assertEqual(len(protocol_lines), 2)

        ready = json.loads(protocol_lines[0])
        self.assertEqual(ready["event"], "ready")
        self.assertEqual(ready["provider_id"], worker.PROVIDER_ID)
        self.assertEqual(ready["provider_version"], worker.RUNTIME_VERSION)

        forecast = json.loads(protocol_lines[1])
        self.assertEqual(forecast, {"event": "forecast_result", "ok": True})

        diagnostics = stderr.getvalue()
        self.assertIn("third-party startup chatter", diagnostics)
        self.assertIn("third-party inference chatter", diagnostics)
        self.assertNotIn("third-party", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
