"""/api/ha: one flat entry per phone, shaped for a Home Assistant REST sensor.

Run: python -m unittest discover -s tests
"""
import unittest
from unittest import mock

from android_backup import __main__ as ab

STATE = {
    "S1": {"name": "Fold-S1", "last_rc": 0, "last_success": 1_700_000_000, "last_attempt": 1_700_000_000,
           "size_bytes": 5_000_000_000, "last_duration": 754, "transport": "wifi", "last_message": "12 files"},
    "S2": {"name": "Tab-S2", "last_rc": 1, "needs_unlock": True, "last_success": 1_600_000_000, "transport": "usb"},
    "S3": {"name": "Old-S3", "last_rc": 1, "needs_unlock": False},
    "S4": {"name": "Busy-S4", "last_rc": 1, "running": True},
    "S5": {"name": "New-S5"},
}


class HaStatus(unittest.TestCase):
    def setUp(self):
        seen = {"ready": {}, "unauthorized": [], "connected": {"S2": "wifi"}, "online": set()}
        with mock.patch.object(ab, "load_state", return_value=STATE), mock.patch.object(ab, "_seen", seen):
            self.entries = {e["serial"]: e for e in ab.ha_status()}

    def test_one_entry_per_phone(self):
        self.assertEqual(sorted(self.entries), ["S1", "S2", "S3", "S4", "S5"])

    def test_result_words(self):
        self.assertEqual({k: e["result"] for k, e in self.entries.items()},
                         {"S1": "ok", "S2": "needs_unlock", "S3": "failed", "S4": "running", "S5": "never"})

    def test_timestamps_are_iso_8601_with_a_zone(self):
        self.assertEqual(self.entries["S1"]["last_backup"], "2023-11-14T22:13:20+00:00")
        self.assertIsNone(self.entries["S5"]["last_backup"])

    def test_size_duration_and_message(self):
        e = self.entries["S1"]
        self.assertEqual((e["size_bytes"], e["duration_seconds"], e["message"]), (5_000_000_000, 754, "12 files"))
        self.assertEqual(self.entries["S5"]["size_bytes"], 0)

    def test_transport_is_the_live_link_when_connected(self):
        self.assertEqual(self.entries["S1"]["transport"], "Wi-Fi")
        self.assertFalse(self.entries["S1"]["connected"])
        self.assertEqual(self.entries["S2"]["transport"], "Wi-Fi")  # state says usb; it is on Wi-Fi now
        self.assertTrue(self.entries["S2"]["connected"])
        self.assertIsNone(self.entries["S5"]["transport"])


if __name__ == "__main__":
    unittest.main()
