from __future__ import annotations

from collections import namedtuple
from datetime import datetime, timedelta, timezone
import unittest

from dusty.m165_broker_forensics import capture_broker_forensics


UTC = timezone.utc
NOW = datetime(2026, 9, 8, 22, 43, tzinfo=UTC)
ORDER = 36923813
Order = namedtuple("Order", "ticket position_id time_setup_msc time_done_msc type state volume_initial volume_current price_open price_current sl symbol comment")
Deal = namedtuple("Deal", "ticket order position_id time_msc type entry reason volume price commission fee swap symbol comment")
Position = namedtuple("Position", "ticket identifier time_msc type volume price_open price_current sl profit symbol comment")


class FakeMT5:
    def __init__(self, *, order=(), deals=(), position=()) -> None:
        self.order_rows = tuple(order)
        self.deal_rows = tuple(deals)
        self.position_rows = tuple(position)
        self.calls: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    def last_error(self):
        return (1, "Success")

    def history_orders_get(self, *args, **kwargs):
        self.calls.append(("history_orders_get", args, kwargs))
        if "ticket" in kwargs:
            return tuple(row for row in self.order_rows if row.ticket == kwargs["ticket"])
        if "position" in kwargs:
            return tuple(row for row in self.order_rows if row.position_id == kwargs["position"])
        return self.order_rows

    def history_deals_get(self, *args, **kwargs):
        self.calls.append(("history_deals_get", args, kwargs))
        if "ticket" in kwargs:
            return tuple(row for row in self.deal_rows if row.order == kwargs["ticket"])
        if "position" in kwargs:
            return tuple(row for row in self.deal_rows if row.position_id == kwargs["position"])
        return self.deal_rows

    def orders_get(self, *args, **kwargs):
        self.calls.append(("orders_get", args, kwargs))
        if "ticket" in kwargs:
            return ()
        return ()

    def positions_get(self, *args, **kwargs):
        self.calls.append(("positions_get", args, kwargs))
        if "ticket" in kwargs:
            return tuple(row for row in self.position_rows if row.ticket == kwargs["ticket"] or row.identifier == kwargs["ticket"])
        return self.position_rows


class M165BrokerForensicsTests(unittest.TestCase):
    def test_empty_broker_state_stays_ambiguous_and_has_no_authority(self) -> None:
        module = FakeMT5()
        snapshot = capture_broker_forensics(
            module,
            order_ticket=ORDER,
            symbol="EURUSD",
            sent_at=NOW - timedelta(minutes=3),
            captured_at=NOW,
        )
        self.assertEqual(snapshot.payload["assessment"], "no_broker_evidence_found_ambiguous_send")
        self.assertFalse(snapshot.broker_write_authority)
        self.assertFalse(snapshot.retry_authority)
        self.assertFalse(snapshot.live_write_authority)
        self.assertTrue(any(name == "history_orders_get" and kwargs.get("ticket") == ORDER for name, _, kwargs in module.calls))
        self.assertTrue(any(name == "history_deals_get" and kwargs.get("ticket") == ORDER for name, _, kwargs in module.calls))

    def test_exact_order_position_id_is_followed_to_deals(self) -> None:
        position_id = 9001
        order = Order(ORDER, position_id, 1, 2, 0, 4, 0.01, 0.0, 1.16, 1.16, 1.15, "EURUSD", "DD")
        deal = Deal(7001, ORDER, position_id, 3, 0, 0, 0, 0.01, 1.16, 0.0, 0.0, 0.0, "EURUSD", "DD")
        module = FakeMT5(order=(order,), deals=(deal,))
        snapshot = capture_broker_forensics(
            module,
            order_ticket=ORDER,
            symbol="EURUSD",
            sent_at=NOW - timedelta(minutes=3),
            captured_at=NOW,
        )
        self.assertEqual(snapshot.payload["assessment"], "broker_execution_evidence_found")
        self.assertIn(f"history_deals_by_position_{position_id}", snapshot.payload["queries"])

    def test_order_without_deal_is_not_misreported_as_fill(self) -> None:
        order = Order(ORDER, 0, 1, 2, 0, 4, 0.01, 0.0, 1.16, 1.16, 1.15, "EURUSD", "DD")
        snapshot = capture_broker_forensics(
            FakeMT5(order=(order,)),
            order_ticket=ORDER,
            symbol="EURUSD",
            sent_at=NOW - timedelta(minutes=3),
            captured_at=NOW,
        )
        self.assertEqual(snapshot.payload["assessment"], "broker_order_evidence_found_without_deal")

    def test_invalid_ticket_or_time_fails_closed(self) -> None:
        with self.assertRaises(ValueError):
            capture_broker_forensics(FakeMT5(), order_ticket=0, symbol="EURUSD", sent_at=NOW, captured_at=NOW)
        with self.assertRaises(ValueError):
            capture_broker_forensics(FakeMT5(), order_ticket=ORDER, symbol="EURUSD", sent_at=NOW, captured_at=NOW - timedelta(seconds=1))


if __name__ == "__main__":
    unittest.main()
