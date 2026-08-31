from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable

from .demo_session import DemoSession, SessionIdentity
from .execution_lifecycle import ExecutionState, SQLiteExecutionLedger
from .order_intent import BrokerPreflight


@dataclass(frozen=True, slots=True)
class DemoExecutionResult:
    intent_hash: str
    state: ExecutionState
    retcode: int
    order_ticket: int
    deal_ticket: int
    comment: str


class DemoMT5ExecutionAdapter:
    """The only Dusty module allowed to call MT5 order_send; live-money authority is permanently false."""

    def __init__(
        self,
        module: Any,
        session: DemoSession,
        connected_identity_reader: Callable[[], SessionIdentity],
        ledger: SQLiteExecutionLedger,
    ) -> None:
        self._mt5 = module
        self._session = session
        self._identity_reader = connected_identity_reader
        self._ledger = ledger

    @property
    def live_write_authorized(self) -> bool:
        return False

    def send(self, preflight: BrokerPreflight, *, at: datetime) -> DemoExecutionResult:
        intent = preflight.intent
        if not preflight.passed:
            raise PermissionError("broker preflight did not pass")
        if at.tzinfo is None or at.utcoffset() is None:
            raise ValueError("execution timestamp must be timezone-aware")
        if at > intent.expires_at:
            raise PermissionError("intent expired before execution")
        if intent.session_fingerprint != self._session.identity.fingerprint:
            raise PermissionError("intent belongs to a different session")
        if not self._mt5.initialize(self._session.identity.terminal_path):
            raise RuntimeError("MT5 initialize failed before execution")
        try:
            verification = self._session.verify(self._identity_reader())
            if not verification.valid or not self._session.broker_write_authorized:
                raise PermissionError("demo session is not write-authorized")
            self._ledger.authorize(intent.intent_hash, intent.client_tag, at=at)
            self._ledger.reserve_send(intent.intent_hash, at=at)
            result = self._mt5.order_send(preflight.request_dict())
        finally:
            self._mt5.shutdown()
        if result is None:
            raise RuntimeError("MT5 order_send returned no result; intent remains SENT_UNKNOWN")

        retcode = int(getattr(result, "retcode", -1))
        order_ticket = int(getattr(result, "order", 0) or 0)
        deal_ticket = int(getattr(result, "deal", 0) or 0)
        comment = str(getattr(result, "comment", ""))
        done = int(getattr(self._mt5, "TRADE_RETCODE_DONE", 10009))
        placed = int(getattr(self._mt5, "TRADE_RETCODE_PLACED", 10008))
        partial = int(getattr(self._mt5, "TRADE_RETCODE_DONE_PARTIAL", 10010))
        if retcode == partial:
            state = ExecutionState.PARTIAL
        elif retcode == done:
            state = ExecutionState.FILLED if deal_ticket else ExecutionState.ACCEPTED
        elif retcode == placed:
            state = ExecutionState.ACCEPTED
        else:
            state = ExecutionState.REJECTED
        self._ledger.transition(
            intent.intent_hash,
            state,
            at=at,
            order_ticket=order_ticket,
            deal_ticket=deal_ticket,
            note=f"retcode:{retcode}:{comment}",
        )
        return DemoExecutionResult(intent.intent_hash, state, retcode, order_ticket, deal_ticket, comment)
