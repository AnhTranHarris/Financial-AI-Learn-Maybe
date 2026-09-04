from __future__ import annotations

import contextlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "diagnose_vibe_ollama_v2.py"
SPEC = importlib.util.spec_from_file_location("diagnose_vibe_ollama_v2", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class VibeOllamaDiagnosticV2Tests(unittest.TestCase):
    def _stage(self, name: str, status: str):
        return MODULE.StageResult(name=name, status=status, elapsed_seconds=0.1)

    def _good_prefix(self):
        return [
            self._stage("ollama", "pass"),
            self._stage("model", "pass"),
            self._stage("native_chat", "pass"),
            self._stage("native_tool_call", "pass"),
        ]

    def test_classifies_native_model_failure_before_compatibility(self):
        stages = [
            self._stage("ollama", "pass"),
            self._stage("model", "pass"),
            self._stage("native_chat", "fail"),
        ]
        self.assertEqual(MODULE._classification(stages), "MODEL_NATIVE_CHAT_BLOCKED")

    def test_classifies_openai_chat_compatibility_failure(self):
        stages = self._good_prefix() + [self._stage("openai_chat", "fail")]
        self.assertEqual(MODULE._classification(stages), "OLLAMA_OPENAI_CHAT_COMPAT_BLOCKED")

    def test_classifies_openai_tool_compatibility_failure(self):
        stages = self._good_prefix() + [
            self._stage("openai_chat", "pass"),
            self._stage("openai_tool_call", "fail"),
        ]
        self.assertEqual(MODULE._classification(stages), "OLLAMA_OPENAI_TOOL_COMPAT_BLOCKED")

    def test_classifies_vibe_agent_loop_only_after_all_provider_paths_pass(self):
        stages = self._good_prefix() + [
            self._stage("openai_chat", "pass"),
            self._stage("openai_tool_call", "pass"),
            self._stage("vibe_doctor", "pass"),
            self._stage("vibe_agent", "fail"),
        ]
        self.assertEqual(MODULE._classification(stages), "VIBE_AGENT_LOOP_BLOCKED")

    def test_emit_is_ascii_safe_for_windows_console(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            MODULE._emit({"detail": "box │ line"})
        raw = buffer.getvalue().strip()
        raw.encode("ascii")
        self.assertEqual(json.loads(raw)["detail"], "box │ line")

    def test_tool_name_extracts_function(self):
        message = {"tool_calls": [{"function": {"name": "dusty_echo", "arguments": "{}"}}]}
        self.assertEqual(MODULE._tool_name(message), "dusty_echo")


if __name__ == "__main__":
    unittest.main()
