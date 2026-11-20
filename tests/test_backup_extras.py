"""Older versions of changed files, the charging and time-window rules, and the calendar export.

Run: python -m unittest discover -s tests
"""
import datetime as dt
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from android_backup import __main__ as ab

TODAY = f"{dt.date.today():%Y-%m-%d}"


def fake_pull(content=b"new", error=None):
    """Stands in for adb pull: writes the file the way pull() does, or fails."""
    def pull(a, remote, dest, size=0):
        if error:
            return error
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        part.write_bytes(content)
        part.replace(dest)
        return None
    return pull


class Versions(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.top = Path(self.dir.name)
        self.file = self.top / "files" / "DCIM" / "a.jpg"
        self.file.parent.mkdir(parents=True)
        self.file.write_bytes(b"old copy")
        self.kept = self.top / "versions" / TODAY / "files" / "DCIM" / "a.jpg"

    def sync(self, pull=None, files=((3, 99, "DCIM/a.jpg"),), **settings):
        with mock.patch.object(ab, "storage_roots", return_value=[("/sdcard/", "files")]), \
                mock.patch.object(ab, "list_files", return_value=list(files)), \
                mock.patch.object(ab, "pull", side_effect=pull or fake_pull()), \
                mock.patch.object(ab, "update_state"), mock.patch.object(ab, "online", return_value=True), \
                mock.patch.multiple(ab, **{"VERSION_DAYS": 30.0, "VERSION_MAX": 500e6, **settings}):
            with open(os.devnull, "wb") as lf:
                return ab.sync_files("S", "S", self.top, lf)

    def test_a_changed_file_keeps_its_previous_copy(self):
        self.sync()
        self.assertEqual(self.file.read_bytes(), b"new")
        self.assertEqual(self.kept.read_bytes(), b"old copy")

    def test_a_new_file_has_no_version(self):
        self.sync(files=[(3, 99, "DCIM/b.jpg")])
        self.assertFalse((self.top / "versions").exists())

    def test_a_failed_pull_leaves_no_version_behind(self):
        self.sync(pull=fake_pull(error="adb: error"))
        self.assertEqual(self.file.read_bytes(), b"old copy")
        self.assertFalse(self.kept.exists())

    def test_the_first_copy_of_the_day_is_the_one_kept(self):
        self.sync()
        self.sync(pull=fake_pull(b"newer!"), files=[(6, 100, "DCIM/a.jpg")])
        self.assertEqual(self.kept.read_bytes(), b"old copy")
        self.assertEqual(self.file.read_bytes(), b"newer!")

    def test_off_and_too_big(self):
        self.sync(VERSION_DAYS=0.0)
        self.assertFalse(self.kept.exists())
        self.file.write_bytes(b"old copy")
        self.sync(VERSION_MAX=4.0)  # the old copy is 8 bytes
        self.assertFalse(self.kept.exists())

    def test_a_share_that_cannot_hard_link_gets_a_copy(self):
        with mock.patch.object(ab.os, "link", side_effect=OSError("not supported")):
            self.sync()
        self.assertEqual(self.kept.read_bytes(), b"old copy")

    def test_old_versions_are_pruned_and_recent_ones_stay(self):
        old = self.top / "versions" / f"{dt.date.today() - dt.timedelta(days=40):%Y-%m-%d}" / "files" / "x"
        recent = self.top / "versions" / f"{dt.date.today() - dt.timedelta(days=5):%Y-%m-%d}" / "files" / "x"
        for f in (old, recent):
            f.parent.mkdir(parents=True)
            f.write_bytes(b"x")
        self.sync()
        self.assertFalse(old.exists())
        self.assertTrue(recent.exists())
        self.assertTrue(self.kept.exists())


class Schedule(unittest.TestCase):
    def due(self, charging_out="  AC powered: false\n  USB powered: false\n", window="", only_charging=True, at="12:00"):
        now = time.time()
        clock = mock.Mock(wraps=dt.datetime)
        clock.now.return_value = dt.datetime.combine(dt.date.today(), dt.time.fromisoformat(at))
        shell = mock.Mock(return_value=subprocess.CompletedProcess([], 0, charging_out, ""))
        with mock.patch.object(ab, "load_state", return_value={"S": {"last_success": now - 90000, "last_attempt": now - 90000}}), \
                mock.patch.object(ab, "shell", shell), mock.patch.object(ab.dt, "datetime", clock), \
                mock.patch.multiple(ab, ONLY_WHEN_CHARGING=only_charging, WINDOW=ab.window(window), _charging={}):
            return ab.due("S", "192.168.1.50:5555"), shell

    def test_waits_for_the_charger(self):
        self.assertFalse(self.due()[0])
        self.assertTrue(self.due("  AC powered: true\n")[0])
        self.assertTrue(self.due("  Wireless powered: true\n")[0])

    def test_charging_is_not_asked_when_the_rule_is_off(self):
        due, shell = self.due(only_charging=False)
        self.assertTrue(due)
        shell.assert_not_called()

    def test_a_phone_that_does_not_answer_counts_as_not_charging(self):
        with mock.patch.object(ab, "shell", side_effect=subprocess.TimeoutExpired("adb", 30)), \
                mock.patch.multiple(ab, _charging={}):
            self.assertFalse(ab.charging("S", "S"))

    def test_the_phone_is_asked_at_most_once_a_minute(self):
        shell = mock.Mock(return_value=subprocess.CompletedProcess([], 0, "AC powered: true", ""))
        with mock.patch.object(ab, "shell", shell), mock.patch.multiple(ab, _charging={}):
            self.assertTrue(ab.charging("S", "S") and ab.charging("S", "S"))
        self.assertEqual(shell.call_count, 1)

    def test_window(self):
        self.assertFalse(self.due(window="01:00-06:00", only_charging=False, at="12:00")[0])
        self.assertTrue(self.due(window="01:00-06:00", only_charging=False, at="03:30")[0])

    def test_window_past_midnight(self):
        for at, expected in (("23:15", True), ("02:00", True), ("12:00", False)):
            self.assertEqual(self.due(window="22:00-06:00", only_charging=False, at=at)[0], expected)

    def test_a_bad_window_means_any_time(self):
        with mock.patch.object(ab, "log") as log:
            self.assertIsNone(ab.window("tonight"))
        log.assert_called_once()
        self.assertIsNone(ab.window(""))

    def test_rules_line_for_the_status_page(self):
        with mock.patch.multiple(ab, ONLY_WHEN_CHARGING=True, WINDOW=ab.window("01:00-06:00")):
            self.assertEqual(ab.schedule_rules(), "only while charging, between 01:00 and 06:00")
        with mock.patch.multiple(ab, ONLY_WHEN_CHARGING=False, WINDOW=None):
            self.assertEqual(ab.schedule_rules(), "")


ROWS = ("Row: 0 _id=7, dtstart=1700000000000, dtend=1700003600000, allDay=0, deleted=0, duration=NULL, rrule=NULL, "
        "eventLocation=Room 2; north, title=Stand-up, weekly, description=Line one\nLine two\n"
        "Row: 1 _id=8, dtstart=1700006400000, dtend=NULL, allDay=1, deleted=0, duration=P1D, rrule=FREQ=YEARLY, "
        "eventLocation=NULL, title=Birthday, description=NULL\n"
        "Row: 2 _id=9, dtstart=1700000000000, dtend=NULL, allDay=0, deleted=1, duration=NULL, rrule=NULL, "
        "eventLocation=NULL, title=Cancelled, description=NULL\n"
        "Row: 3 _id=10, dtstart=1700000000000, dtend=1700003600000, allDay=0, deleted=0, duration=NULL, rrule=NULL, "
        "eventLocation=NULL, title=" + "Long " * 30 + ", description=NULL\n")


class Calendar(unittest.TestCase):
    def export(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(ab, "shell", return_value=subprocess.CompletedProcess([], 0, ROWS, "")):
            count = ab.export_calendar("S", Path(d) / "calendar.ics")
            return count, (Path(d) / "calendar.ics").read_bytes().decode()

    def test_events_are_written_and_deleted_ones_left_out(self):
        count, ics = self.export()
        self.assertEqual(count, 3)
        self.assertTrue(ics.startswith("BEGIN:VCALENDAR\r\nVERSION:2.0\r\n"))
        self.assertTrue(ics.endswith("END:VCALENDAR\r\n"))
        self.assertEqual(ics.count("BEGIN:VEVENT"), 3)
        self.assertNotIn("Cancelled", ics)

    def test_times_text_and_repeats(self):
        _, ics = self.export()
        self.assertIn("DTSTART:20231114T221320Z\r\nDTEND:20231114T231320Z\r\n", ics)
        self.assertIn("SUMMARY:Stand-up\\, weekly\r\n", ics)
        self.assertIn("LOCATION:Room 2\\; north\r\n", ics)
        self.assertIn("DESCRIPTION:Line one\\nLine two\r\n", ics)
        self.assertIn("DTSTART;VALUE=DATE:20231115\r\nDURATION:P1D\r\nRRULE:FREQ=YEARLY\r\nSUMMARY:Birthday\r\n", ics)

    def test_long_lines_are_folded(self):
        _, ics = self.export()
        self.assertTrue(all(len(line) <= 75 for line in ics.split("\r\n")))
        self.assertIn("\r\n ", ics)


if __name__ == "__main__":
    unittest.main()
