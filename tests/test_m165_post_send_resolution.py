from __future__ import annotations

import unittest

from dusty.m165_post_send_resolution import PostSendStatus, resolve_post_send_forensics


class M165PostSendResolutionTests(unittest.TestCase):
    def test_completed_protective_close_is_recognized_without_retry_authority(self) -> None:
        payload = {
            "queries": {
                "history_orders_by_ticket": {"rows": [{"ticket": 10, "position_id": 10}]},
                "active_orders_by_ticket": {"rows": []},
                "open_positions_by_ticket": {"rows": []},
                "open_position_10": {"rows": []},
                "history_deals_by_position_10": {
                    "rows": [
                        {"ticket": 20, "position_id": 10, "entry": 0},
                        {"ticket": 21, "position_id": 10, "entry": 1},
                    ]
                },
            }
        }
        result = resolve_post_send_forensics(payload)
        self.assertEqual(result.status, PostSendStatus.COMPLETED_PROTECTIVE_CLOSE)
        self.assertEqual(result.position_id, 10)
        self.assertEqual(result.entry_deal_ticket, 20)
        self.assertEqual(result.exit_deal_ticket, 21)
        self.assertFalse(result.retry_authority)
        self.assertFalse(result.broker_write_authority)
        self.assertFalse(result.live_write_authority)

    def test_open_position_requires_existing_governed_close_path(self) -> None:
        payload = {
            "queries": {
                "history_orders_by_ticket": {"rows": [{"ticket": 10, "position_id": 10}]},
                "active_orders_by_ticket": {"rows": []},
                "open_positions_by_ticket": {"rows": [{"ticket": 10}]},
                "open_position_10": {"rows": [{"ticket": 10}]},
                "history_deals_by_position_10": {"rows": [{"ticket": 20, "position_id": 10, "entry": 0}]},
            }
        }
        result = resolve_post_send_forensics(payload)
        self.assertEqual(result.status, PostSendStatus.POSITION_OPEN)
        self.assertEqual(result.entry_deal_ticket, 20)
        self.assertEqual(result.exit_deal_ticket, 0)

    def test_order_only_never_becomes_fill(self) -> None:
        payload = {
            "queries": {
                "history_orders_by_ticket": {"rows": [{"ticket": 10, "position_id": 10}]},
                "active_orders_by_ticket": {"rows": []},
                "open_positions_by_ticket": {"rows": []},
                "open_position_10": {"rows": []},
                "history_deals_by_position_10": {"rows": []},
            }
        }
        result = resolve_post_send_forensics(payload)
        self.assertEqual(result.status, PostSendStatus.ORDER_ONLY)

    def test_empty_state_remains_ambiguous(self) -> None:
        result = resolve_post_send_forensics({"queries": {}})
        self.assertEqual(result.status, PostSendStatus.AMBIGUOUS)

    def test_multiple_position_identities_fail_closed(self) -> None:
        payload = {
            "queries": {
                "history_orders_by_ticket": {"rows": [{"position_id": 10}, {"position_id": 11}]},
            }
        }
        with self.assertRaisesRegex(ValueError, "multiple position identities"):
            resolve_post_send_forensics(payload)


if __name__ == "__main__":
    unittest.main()
