"""The status page and device nicknames.

A nickname is a display name only: it must never change the backup folder, and it is free
text from a page with no login, so it must be escaped everywhere it is shown.

Run: python -m unittest discover -s tests
"""
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from android_backup import __main__ as ab
from android_backup import alerts, page

NOW = time.time()
STATE = {
    "S1": {"name": "Fold-S1", "nickname": "Brett's <b>Fold</b>", "last_rc": 0, "last_success": NOW - 3 * 3600,
           "size_bytes": 81_200_000_000, "last_duration": 754, "transport": "wifi", "last_message": "12 files <script>"},
    "S2": {"name": "Tab-S2", "last_rc": 1, "needs_unlock": True, "last_success": NOW - 60 * 3600},
    "S3": {"name": "Busy-S3", "last_rc": 0, "running": True, "progress": 37, "last_success": NOW - 60 * 3600},
}


class PageCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        root = Path(self.dir.name)
        seen = {"ready": {}, "unauthorized": ["192.168.1.9:5555"], "connected": {"S1": "wifi", "S9": "usb"}, "online": set()}
        for cm in (mock.patch.multiple(ab, CONFIG=root, STATE_FILE=root / "state.json", _seen=seen),
                   mock.patch.multiple(alerts, URLS="", CONFIG_FILE="", WEBHOOK="", _targets=None, STALE_HOURS=48.0,
                                       NOTIFY_EVENTS="", NOTIFY_SUCCESS=False, SETTINGS_FILE=root / "settings.json",
                                       _settings=None)):
            cm.start()
            self.addCleanup(cm.stop)
        for serial, fields in STATE.items():
            ab.update_state(serial, **fields)


class StatusPage(PageCase):
    def setUp(self):
        super().setUp()
        self.html = ab.status_page()

    def test_nickname_is_the_title_and_the_real_name_stays_visible(self):
        self.assertIn("<h2>Brett&#x27;s &lt;b&gt;Fold&lt;/b&gt;</h2>", self.html)
        self.assertIn("Fold-S1 &middot; S1", self.html)

    def test_free_text_is_escaped(self):
        self.assertNotIn("<b>Fold</b>", self.html)
        self.assertNotIn("12 files <script>", self.html)

    def test_each_state_has_its_own_pill(self):
        for text in ('class="pill ok">Backed up', 'class="pill needs_unlock">Unlock phone',
                     'class="pill running">Running 37%', 'class="pill never">No backup yet'):
            self.assertIn(text, self.html)

    def test_stale_badge_only_on_an_old_backup_that_is_not_running(self):
        self.assertEqual(self.html.count('class="pill stale"'), 1)

    def test_running_backup_shows_a_progress_bar(self):
        self.assertEqual(self.html.count("class=fill style='width:37%'"), 1)

    def test_a_connected_device_with_no_backup_yet_is_listed(self):
        self.assertIn("<h2>S9</h2>", self.html)

    def test_waiting_for_allow_banner_and_test_button(self):
        self.assertIn("192.168.1.9:5555", self.html)
        self.assertIn("Send test alert", self.html)
        self.assertIn("No alert targets set", self.html)

    def test_no_devices(self):
        empty = page.render([], [], version="x", interval=24, backups="/backups", alert_summary="", last_test="",
                            stale_hours=48)
        self.assertIn("No devices yet", empty)

    def test_relative_times(self):
        self.assertEqual([page.ago(t, 100_000) for t in (None, 99_990, 99_000, 100_000 - 5 * 3600, 100_000 - 72 * 3600)],
                         ["never", "just now", "17 min ago", "5 h ago", "3 days ago"])
        self.assertEqual([page.took(s) for s in (None, 42, 754)], ["-", "42 s", "12 min 34 s"])


class AddADevice(PageCase):
    def test_guide_says_a_phone_is_waiting_for_allow_and_opens_itself(self):
        html = ab.status_page()
        self.assertIn('<details class="prefs guide" open>', html)
        self.assertIn("192.168.1.9:5555 is waiting: tap Allow", html)

    def test_guide_offers_wifi_for_a_usb_device_only(self):
        with mock.patch.dict(ab._seen, {"unauthorized": []}):
            html = ab.status_page()
        self.assertIn("S9 is connected over USB", html)
        self.assertIn('action="/api/wifi?serial=S9"', html)
        self.assertNotIn('action="/api/wifi?serial=S1"', html)  # already on Wi-Fi
        self.assertIn('<details class="prefs guide">', html)

    def test_checklist_download_has_every_step(self):
        text = page.guide_text()
        self.assertEqual(text.count("[ ] "), len(page.GUIDE))
        self.assertIn("Always allow", text)

    def wifi(self, serial, ip_out="    inet 192.168.1.50/24 brd 192.168.1.255 scope global wlan0", rc=0, targets=()):
        calls = []

        def adb(*args, timeout=120):
            calls.append(args)
            out = ip_out if "shell" in args else ""
            return ab.subprocess.CompletedProcess(args, rc if "tcpip" in args else 0, out, "")

        ready = {"S9": "S9", "S1": "192.168.1.50:5555"}
        with mock.patch.object(ab, "adb", adb), mock.patch.dict(ab._seen, {"ready": ready}), \
                mock.patch.object(ab, "TARGETS", list(targets)):
            return ab.enable_wifi(serial), calls

    def test_switch_to_wifi_runs_tcpip_and_gives_the_address(self):
        note, calls = self.wifi("S9")
        self.assertIn(("-s", "S9", "tcpip", "5555"), calls)
        self.assertEqual(note, "Done. Add 192.168.1.50:5555 to ADB_TARGETS in the container settings, then it can be unplugged.")

    def test_switch_to_wifi_knows_the_address_is_already_set(self):
        note, _ = self.wifi("S9", targets=["192.168.1.50:5555"])
        self.assertIn("already in ADB_TARGETS", note)

    def test_switch_to_wifi_needs_usb_and_wifi_and_no_running_backup(self):
        self.assertEqual(self.wifi("S1"), ("Plug the device in over USB first.", []))
        self.assertEqual(self.wifi("NOPE")[1], [])
        note, calls = self.wifi("S9", ip_out="")
        self.assertIn("isn't on Wi-Fi", note)
        self.assertNotIn(("-s", "S9", "tcpip", "5555"), calls)
        with ab._lock:
            self.assertEqual(self.wifi("S9"), ("A backup is running. Try again when it has finished.", []))
        self.assertIn("refused", self.wifi("S9", rc=1)[0])


class Nicknames(PageCase):
    def post(self, path, fields):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ab.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}{path}",
                                         data=urllib.parse.urlencode(fields).encode(), method="POST")
        try:
            return urllib.request.build_opener(NoRedirect).open(request, timeout=10).status
        except urllib.error.HTTPError as e:
            return e.code

    def test_saved_trimmed_and_capped(self):
        self.assertEqual(self.post("/api/nickname?serial=S2", {"nickname": "  Kitchen   tablet " + "x" * 60}), 303)
        saved = ab.load_state()["S2"]["nickname"]
        self.assertTrue(saved.startswith("Kitchen tablet x"))
        self.assertEqual(len(saved), 40)

    def test_the_backup_folder_name_is_untouched(self):
        self.post("/api/nickname?serial=S2", {"nickname": "Kitchen tablet"})
        self.assertEqual(ab.load_state()["S2"]["name"], "Tab-S2")

    def test_blank_clears_it(self):
        self.post("/api/nickname?serial=S1", {"nickname": ""})
        self.assertEqual(ab.load_state()["S1"]["nickname"], "")
        self.assertIn("<h2>Fold-S1</h2>", ab.status_page())

    def test_unknown_device_is_refused_and_not_created(self):
        self.assertEqual(self.post("/api/nickname?serial=NOPE", {"nickname": "x"}), 404)
        self.assertNotIn("NOPE", ab.load_state())

    def test_connected_device_with_no_backup_yet_can_be_named(self):
        self.assertEqual(self.post("/api/nickname?serial=S9", {"nickname": "New phone"}), 303)
        self.assertIn("<h2>New phone</h2>", ab.status_page())

    def test_home_assistant_gets_the_nickname(self):
        entry = {e["serial"]: e for e in ab.ha_status()}["S1"]
        self.assertEqual((entry["nickname"], entry["name"]), ("Brett's <b>Fold</b>", "Fold-S1"))

    def test_alerts_use_the_nickname(self):
        with mock.patch.object(alerts, "once") as once:
            alerts.check_stale({"S2": {"name": "Tab-S2", "nickname": "Kitchen tablet", "last_success": NOW - 60 * 3600}})
        self.assertEqual(once.call_args.args[3], "⏰ Android backup is stale: Kitchen tablet")
        root = Path(self.dir.name)
        with mock.patch.multiple(ab, BACKUPS=root / "backups", LOG_DIR=root / "logs", device_label=lambda a, s: "Fold-S1",
                                 on_mobile_data=lambda a: False, sync_files=mock.Mock(side_effect=RuntimeError("boom")),
                                 fix_ownership=lambda top: None, backup_size=lambda top: 0), \
                mock.patch.object(alerts, "backup_result") as result:
            ab.backup_one("S1", "S1")
        self.assertEqual(result.call_args.args[1], "Brett's <b>Fold</b>")
        self.assertTrue((root / "backups" / "Fold-S1").is_dir())


if __name__ == "__main__":
    unittest.main()
