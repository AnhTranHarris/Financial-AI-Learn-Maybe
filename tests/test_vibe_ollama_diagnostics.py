from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


MODULE_PATH = Path(__file__).resolve().parents[1] / "tools" / "diagnose_vibe_ollama.py"
SPEC = importlib.util.spec_from_file_location("diagnose_vibe_ollama", MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class VibeOllamaDiagnosticTests(unittest.TestCase):
    def _stage(self, name: str, status: str):
        return MODULE.StageResult(name=name, status=status, elapsed_seconds=0.1)

    def test_classifies_tool_call_block_after_chat_passes(self):
        stages = [
            self._stage("ollama", "pass"),
            self._stage("model", "pass"),
            self._stage("chat", "pass"),
            self._stage("tool_call", "fail"),
            self._stage("vibe_doctor", "pass"),
            self._stage("vibe_agent", "fail"),
        ]
        self.assertEqual(MODULE._classify(stages), "MODEL_TOOL_CALL_BLOCKED")

    def test_classifies_vibe_agent_block_after_provider_passes(self):
        stages = [
            self._stage("ollama", "pass"),
            self._stage("model", "pass"),
            self._stage("chat", "pass"),
            self._stage("tool_call", "pass"),
            self._stage("vibe_doctor", "pass"),
            self._stage("vibe_agent", "fail"),
        ]
        self.assertEqual(MODULE._classify(stages), "VIBE_AGENT_LOOP_BLOCKED")

    def test_pass_requires_every_required_stage(self):
        stages = [
            self._stage("ollama", "pass"),
            self._stage("model", "pass"),
            self._stage("chat", "pass"),
            self._stage("tool_call", "pass"),
            self._stage("vibe_doctor", "pass"),
            self._stage("vibe_agent", "pass"),
        ]
        self.assertEqual(MODULE._classify(stages), "PASS")

    def test_extracts_openai_tool_call_name(self):
        response = {
            "choices": [
                {
                    "message": {
                        "tool_calls": [
                            {"function": {"name": "dusty_echo", "arguments": "{}"}}
                        ]
                    }
                }
            ]
        }
        self.assertEqual(MODULE._tool_call_name(response), "dusty_echo")


if __name__ == "__main__":
    unittest.main()
