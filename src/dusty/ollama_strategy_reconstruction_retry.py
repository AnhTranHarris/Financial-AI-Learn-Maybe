from __future__ import annotations

"""Bounded truncation escalation for M196.5 local Ollama reconstruction.

The certified 384-token reconstruction path remains the default. Only a normal
Ollama chat response whose ``done_reason`` is exactly ``length`` may receive one
second attempt with a larger generation ceiling. Transport failures, schema
failures, model-identity failures, and all other errors remain fail-closed in the
existing ``OllamaStrategyReconstructor``.
"""

from copy import deepcopy

from .ollama_quant_reviewer import Transport, _urllib_transport
from .ollama_strategy_reconstruction import NUM_PREDICT, OllamaStrategyReconstructor


TRUNCATION_RETRY_NUM_PREDICT = 640


class TruncationRetryTransport:
    """Transport decorator that retries one truncated reconstruction chat only."""

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    def __call__(
        self,
        method: str,
        url: str,
        payload: dict[str, object] | None,
        timeout: float,
    ) -> dict[str, object]:
        response = self._transport(method, url, payload, timeout)
        if not self._eligible(method, url, payload, response):
            return response

        assert payload is not None
        retry_payload = deepcopy(payload)
        options = retry_payload.get("options")
        assert isinstance(options, dict)
        options["num_predict"] = TRUNCATION_RETRY_NUM_PREDICT
        return self._transport(method, url, retry_payload, timeout)

    @staticmethod
    def _eligible(
        method: str,
        url: str,
        payload: dict[str, object] | None,
        response: dict[str, object],
    ) -> bool:
        if method != "POST" or not url.endswith("/api/chat") or payload is None:
            return False
        if response.get("done_reason") != "length":
            return False
        options = payload.get("options")
        return isinstance(options, dict) and options.get("num_predict") == NUM_PREDICT


class BoundedRetryOllamaStrategyReconstructor(OllamaStrategyReconstructor):
    """Existing strict reconstructor plus one 384 -> 640 truncation retry."""

    def __init__(
        self,
        *,
        base_url: str = "http://127.0.0.1:11434",
        timeout_seconds: float = 240.0,
        transport: Transport = _urllib_transport,
    ) -> None:
        super().__init__(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            transport=TruncationRetryTransport(transport),
        )
