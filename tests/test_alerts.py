"""Alerts: each event goes out once, a Discord-only install is unchanged, a failing target
never breaks a backup, and no secret reaches the log.

Apprise is faked, so these run with the standard library alone. Every URL is a placeholder.

Run: python -m unittest discover -s tests
"""
import contextlib
import io
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from android_backup import __main__ as ab
from android_backup import alerts

SECRET = "PLACEHOLDER-SECRET"
NTFY = f"ntfys://tk_{SECRET}@ntfy.example.com/backups"
HASS = f"hassio://ha.example.com/{SECRET}"
DISCORD = f"https://discord.com/api/webhooks/1234567890/{SECRET}"
HOUR = 3600


class FakeServer:
    def __init__(self, url, tags=()):
        self.raw, self.tags = url, set(tags)
        self.host = url.partition("://")[2].rpartition("@")[2].partition("/")[0].partition("?")[0]

    def url(self, privacy=False):
        return self.raw.partition("://")[0] + "://****" if privacy else self.raw


class FakeApprise:
    sent = []  # (url, title, body, level) for every delivery
    broken = ()  # URL prefixes that fail
    explode = ()  # URL prefixes that raise, with the URL in the message

    def __init__(self):
        self.servers = []

    @staticmethod
    def instantiate(url, tag=None):
        return None if url.startswith("bogus://") else FakeServer(url)

    def add(self, server):
        self.servers.append(server)

    def notify(self, title, body, notify_type):
        for s in self.servers:
            if s.raw.startswith(tuple(self.explode)):
                raise RuntimeError(f"could not reach {s.raw}")
            if s.raw.startswith(tuple(self.broken)):
                return False
            FakeApprise.sent.append((s.raw, title, body, notify_type))
        return True


class FakeConfig:
    def add(self, path):
        self.path = path

    def services(self):
        return [FakeServer(f"json://file.example.com/{SECRET}", tags={"stale"})]


class AlertsCase(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        FakeApprise.sent, FakeApprise.broken, FakeApprise.explode = [], (), ()
        fake = types.SimpleNamespace(Apprise=FakeApprise, AppriseConfig=FakeConfig, NotifyType=lambda level: level)
        self.patch(mock.patch.dict(sys.modules, {"apprise": fake}))
        self.set(URLS="", CONFIG_FILE="", WEBHOOK="", NOTIFY_SUCCESS=False, NOTIFY_EVENTS="",
                 STALE_HOURS=48.0, WIFI_LOST_HOURS=6.0, SENT_FILE=Path(self.dir.name) / "alerts.json", last_test="",
                 SETTINGS_FILE=Path(self.dir.name) / "settings.json", _settings=None)
        self.now = 1_000_000.0
        self.patch(mock.patch.object(alerts.time, "time", lambda: self.now))
        self.out = io.StringIO()
        self.patch(contextlib.redirect_stdout(self.out))

    def patch(self, cm):
        cm.__enter__()
        self.addCleanup(cm.__exit__, None, None, None)

    def set(self, **settings):
        """Change settings the way a restart with new variables would."""
        for name, value in settings.items():
            self.patch(mock.patch.object(alerts, name, value))
        self.patch(mock.patch.object(alerts, "_targets", None))

    def titles(self, url_prefix=""):
        return [title for url, title, _, _ in FakeApprise.sent if url.startswith(url_prefix)]


class SplitUrls(unittest.TestCase):
    def test_spaces_commas_and_tags(self):
        self.assertEqual(alerts.split_urls(f"{NTFY}?tags=a,b, {HASS}\n stale,failed={DISCORD} "),
                         [(set(), f"{NTFY}?tags=a,b"), (set(), HASS), ({"stale", "failed"}, DISCORD)])

    def test_empty(self):
        self.assertEqual(alerts.split_urls("  "), [])


class Routing(AlertsCase):
    def test_untagged_gets_everything_tagged_only_its_own(self):
        self.set(URLS=f"{NTFY} stale={HASS}")
        alerts.notify("failed", "Fold", "t", "b")
        alerts.notify("stale", "Fold", "t", "b")
        self.assertEqual(len(self.titles("ntfys://")), 2)
        self.assertEqual(len(self.titles("hassio://")), 1)

    def test_ntfy_priority_follows_the_level(self):
        self.set(URLS=NTFY, NOTIFY_SUCCESS=True)
        alerts.notify("failed", "Fold", "t", "b")
        alerts.notify("stale", "Fold", "t", "b")
        alerts.notify("success", "Fold", "t", "b")
        self.assertEqual([url.rpartition("priority=")[2] for url, *_ in FakeApprise.sent], ["high", "default", "low"])

    def test_test_alert_arrives_like_a_real_one(self):
        self.set(URLS=NTFY)
        alerts.test()
        self.assertTrue(FakeApprise.sent[0][0].endswith("priority=default"))

    def test_a_priority_in_the_url_wins(self):
        self.set(URLS=f"{NTFY}?priority=min")
        alerts.notify("failed", "Fold", "t", "b")
        self.assertEqual(FakeApprise.sent[0][0], f"{NTFY}?priority=min")

    def test_event_switched_off(self):
        self.set(URLS=NTFY, NOTIFY_EVENTS="failed")
        self.assertIsNone(alerts.notify("stale", "Fold", "t", "b"))
        self.assertEqual(FakeApprise.sent, [])

    def test_config_file_targets(self):
        config = Path(self.dir.name) / "apprise.yml"
        config.write_text("placeholder")
        self.set(CONFIG_FILE=str(config))
        alerts.notify("failed", "Fold", "t", "b")
        alerts.notify("stale", "Fold", "t", "b")
        self.assertEqual(len(FakeApprise.sent), 1)

    def test_no_targets_is_quiet(self):
        self.assertIsNone(alerts.notify("failed", "Fold", "t", "b"))
        self.assertEqual(self.out.getvalue(), "")


class EachEventOnce(AlertsCase):
    def setUp(self):
        super().setUp()
        self.set(URLS=NTFY)

    def restart(self):
        self.patch(mock.patch.object(alerts, "_targets", None))

    def test_needs_unlock_once_until_a_good_backup(self):
        for _ in range(3):
            alerts.backup_result("S1", "Fold-S1", False, False, True, "shared storage is empty")
        self.restart()
        alerts.backup_result("S1", "Fold-S1", False, False, True, "shared storage is empty")
        self.assertEqual(len(FakeApprise.sent), 1)
        self.assertEqual(FakeApprise.sent[0][2], "Unlock your phone once to let the backup run.")
        alerts.backup_result("S1", "Fold-S1", True, False, False, "12 files")
        alerts.backup_result("S1", "Fold-S1", False, False, True, "shared storage is empty")
        self.assertEqual(len(FakeApprise.sent), 2)

    def test_failed_every_time_but_not_when_unplugged(self):
        alerts.backup_result("S1", "Fold-S1", False, False, False, "adb: error")
        alerts.backup_result("S1", "Fold-S1", False, False, False, "adb: error")
        alerts.backup_result("S1", "Fold-S1", False, True, False, "device went away during the file copy")
        self.assertEqual(len(FakeApprise.sent), 2)
        self.assertEqual(FakeApprise.sent[0][3], "failure")

    def test_success_only_when_asked(self):
        alerts.backup_result("S1", "Fold-S1", True, False, False, "12 files")
        self.assertEqual(FakeApprise.sent, [])
        self.set(NOTIFY_SUCCESS=True)
        alerts.backup_result("S1", "Fold-S1", True, False, False, "12 files")
        self.assertEqual(self.titles(), ["✅ Android backup OK: Fold-S1"])

    def test_unauthorized_once_per_address_until_allowed(self):
        for _ in range(3):
            alerts.check_devices({}, ["S1"], set(), [])
        self.restart()
        alerts.check_devices({}, ["S1"], set(), [])
        self.assertEqual(len(FakeApprise.sent), 1)
        alerts.check_devices({"S1": "S1"}, [], {"S1"}, [])  # Allow tapped
        alerts.check_devices({}, ["S1"], set(), [])  # key revoked later
        self.assertEqual(len(FakeApprise.sent), 2)

    def test_wifi_lost_after_the_threshold_then_once(self):
        target = "192.168.1.50:5555"
        alerts.check_devices({"S1": target}, [], {target}, [target])
        self.now += 5 * HOUR
        alerts.check_devices({}, [], set(), [target])
        self.assertEqual(FakeApprise.sent, [])
        self.now += 2 * HOUR
        for _ in range(3):
            alerts.check_devices({}, [], set(), [target])
        self.restart()
        alerts.check_devices({}, [], set(), [target])
        self.assertEqual(len(FakeApprise.sent), 1)
        self.assertIn("adb tcpip 5555", FakeApprise.sent[0][2])
        alerts.check_devices({"S1": target}, [], {target}, [target])  # back
        self.now += 7 * HOUR
        alerts.check_devices({}, [], set(), [target])
        self.assertEqual(len(FakeApprise.sent), 2)

    def test_wifi_target_never_seen_starts_its_clock_at_first_check(self):
        target = "192.168.1.50:5555"
        alerts.check_devices({}, [], set(), [target])
        self.assertEqual(FakeApprise.sent, [])
        self.now += 6 * HOUR
        alerts.check_devices({}, [], set(), [target])
        self.assertEqual(len(FakeApprise.sent), 1)

    def test_wifi_target_counts_while_usb_is_the_best_address(self):
        target = "192.168.1.50:5555"
        alerts.check_devices({"S1": "S1"}, [], {"S1", target}, [target])
        self.now += 7 * HOUR
        alerts.check_devices({"S1": "S1"}, [], {"S1", target}, [target])
        self.assertEqual(FakeApprise.sent, [])

    def test_stale_once_per_stretch(self):
        state = {"S1": {"name": "Fold-S1", "last_success": self.now - 49 * HOUR},
                 "S2": {"name": "Tab-S2", "last_success": self.now - HOUR},
                 "S3": {"name": "New-S3"}}
        for _ in range(3):
            alerts.check_stale(state)
        self.restart()
        alerts.check_stale(state)
        self.assertEqual(self.titles(), ["⏰ Android backup is stale: Fold-S1"])
        alerts.backup_result("S1", "Fold-S1", True, False, False, "12 files")
        alerts.check_stale(state)  # still the old timestamp: a new stretch
        self.assertEqual(len(FakeApprise.sent), 2)

    def test_stale_waits_for_a_running_backup_and_can_be_switched_off(self):
        state = {"S1": {"name": "Fold-S1", "last_success": self.now - 49 * HOUR, "running": True}}
        alerts.check_stale(state)
        self.set(STALE_HOURS=0.0)
        alerts.check_stale({"S1": {"name": "Fold-S1", "last_success": self.now - 49 * HOUR}})
        self.assertEqual(FakeApprise.sent, [])

    def test_an_alert_nothing_took_is_retried_later_not_at_once(self):
        FakeApprise.broken = ("ntfys://",)
        alerts.check_devices({}, ["S1"], set(), [])
        FakeApprise.broken = ()
        alerts.check_devices({}, ["S1"], set(), [])
        self.assertEqual(FakeApprise.sent, [])
        self.now += alerts.RETRY_SECONDS
        alerts.check_devices({}, ["S1"], set(), [])
        self.assertEqual(len(FakeApprise.sent), 1)


class DiscordOnly(AlertsCase):
    """An install with only DISCORD_WEBHOOK_URL and NOTIFY_SUCCESS behaves as upstream did."""

    def setUp(self):
        super().setUp()
        self.set(WEBHOOK=DISCORD)

    def test_failure_is_sent_success_is_not(self):
        alerts.backup_result("S1", "Fold-S1", False, False, False, "adb: error")
        alerts.backup_result("S1", "Fold-S1", True, False, False, "12 files")
        self.assertEqual(FakeApprise.sent, [(DISCORD, "⚠️ Android backup FAILED: Fold-S1", "adb: error", "failure")])

    def test_success_with_notify_success(self):
        self.set(NOTIFY_SUCCESS=True)
        alerts.backup_result("S1", "Fold-S1", True, False, False, "12 files")
        self.assertEqual(FakeApprise.sent, [(DISCORD, "✅ Android backup OK: Fold-S1", "12 files", "success")])

    def test_locked_phone_is_still_a_plain_failure_every_time(self):
        message = "shared storage is empty or unreadable (unlock the phone once after a reboot)"
        alerts.backup_result("S1", "Fold-S1", False, False, True, message)
        alerts.backup_result("S1", "Fold-S1", False, False, True, message)
        self.assertEqual([(t, b) for _, t, b, _ in FakeApprise.sent], [("⚠️ Android backup FAILED: Fold-S1", message)] * 2)

    def test_no_new_kinds_of_alert(self):
        alerts.check_devices({}, ["S1"], set(), ["192.168.1.50:5555"])
        self.now += 100 * HOUR
        alerts.check_devices({}, ["S1"], set(), ["192.168.1.50:5555"])
        alerts.check_stale({"S1": {"last_success": self.now - 100 * HOUR}})
        self.assertEqual(FakeApprise.sent, [])

    def test_unplugged_mid_run_is_not_a_failure(self):
        alerts.backup_result("S1", "Fold-S1", False, True, False, "device went away during the file copy")
        self.assertEqual(FakeApprise.sent, [])

    def test_same_webhook_in_both_settings_is_sent_once(self):
        self.set(URLS=f"{NTFY} {DISCORD}")
        alerts.notify("failed", "Fold", "t", "b")
        self.assertEqual(len(self.titles("https://discord.com")), 1)
        self.assertEqual(len(FakeApprise.sent), 2)


class NeverBreaksABackup(AlertsCase):
    def test_a_target_that_raises_is_logged_and_the_rest_still_get_it(self):
        self.set(URLS=f"{NTFY} {HASS}")
        FakeApprise.explode = ("ntfys://",)
        self.assertTrue(alerts.notify("failed", "Fold", "t", "b"))
        self.assertEqual(len(self.titles("hassio://")), 1)

    def test_backup_result_swallows_everything(self):
        self.set(URLS=NTFY)
        with mock.patch.object(alerts, "notify", side_effect=RuntimeError(f"boom {NTFY}")):
            alerts.backup_result("S1", "Fold-S1", False, False, False, "adb: error")
        self.assertNotIn(SECRET, self.out.getvalue())

    def test_apprise_not_installed(self):
        self.set(URLS=NTFY)
        with mock.patch.dict(sys.modules, {"apprise": None}):
            self.assertIsNone(alerts.notify("failed", "Fold", "t", "b"))

    def test_backup_one_survives_a_failing_target(self):
        self.set(URLS=NTFY)
        FakeApprise.explode = ("ntfys://",)
        root = Path(self.dir.name)
        with mock.patch.multiple(ab, BACKUPS=root / "backups", LOG_DIR=root / "logs", STATE_FILE=root / "state.json",
                                 CONFIG=root, device_label=lambda a, s: "Fold-S1", on_mobile_data=lambda a: False,
                                 sync_files=mock.Mock(side_effect=ab.NeedsUnlock("shared storage is empty")),
                                 fix_ownership=lambda top: None, backup_size=lambda top: 0):
            self.assertFalse(ab.backup_one("S1", "S1"))
            self.assertTrue(ab.load_state()["S1"]["needs_unlock"])


class NoSecretsInLogs(AlertsCase):
    def test_nothing_secret_is_ever_printed(self):
        config = Path(self.dir.name) / "apprise.yml"
        config.write_text("placeholder")
        self.set(URLS=f"{NTFY} stale={HASS} bogus://{SECRET}@example.com", WEBHOOK=DISCORD, CONFIG_FILE=str(config),
                 NOTIFY_SUCCESS=True)
        alerts.describe()
        FakeApprise.explode, FakeApprise.broken = ("ntfys://",), ("hassio://", "https://discord.com")
        alerts.notify("failed", "Fold", "t", "b")
        alerts.check_stale({"S1": {"last_success": self.now - 49 * HOUR}})
        alerts.test()
        printed = self.out.getvalue()
        self.assertIn("ntfys://ntfy.example.com", printed)
        self.assertIn("did not reach", printed)
        self.assertNotIn(SECRET, printed)
        self.assertNotIn("1234567890", printed)  # a Discord webhook ID is not a hostname
        self.assertNotIn(SECRET, alerts.last_test)

    def test_only_a_real_hostname_is_shown(self):
        self.assertEqual(alerts.where(FakeServer("ntfy://secret-topic")), "ntfy://...")
        self.assertEqual(alerts.where(FakeServer(HASS)), "hassio://ha.example.com")


class StartedAndSettings(AlertsCase):
    def setUp(self):
        super().setUp()
        self.set(URLS=NTFY, NOTIFY_SUCCESS=True)

    def test_started_is_off_until_asked_for(self):
        alerts.backup_started("Fold", "wifi", False)
        self.assertEqual(FakeApprise.sent, [])
        self.set(NOTIFY_EVENTS="started,failed")
        alerts.backup_started("Fold", "wifi", False)
        self.assertEqual([(t, b) for _, t, b, _ in FakeApprise.sent],
                         [("📲 Android backup started: Fold", "Connected over Wi-Fi and backing up.")])

    def test_a_saved_choice_wins_over_the_variables_and_survives_a_restart(self):
        alerts.save_events({"started", "failed", "not-an-event"})
        self.assertEqual(alerts.events(), {"started", "failed"})
        self.set()  # restart
        self.assertEqual(alerts.events(), {"started", "failed"})
        alerts.backup_result("S1", "Fold", True, False, False, "12 files")  # success was unticked
        self.assertEqual(FakeApprise.sent, [])

    def test_nothing_saved_leaves_the_variables_in_charge(self):
        self.assertEqual(alerts.events(), set(alerts.DEFAULT_EVENTS) | {"success"})

    def test_everything_unticked_sends_nothing(self):
        alerts.save_events(set())
        self.assertEqual(alerts.events(), set())
        self.assertIsNone(alerts.notify("failed", "Fold", "t", "b"))

    def test_settings_form_on_the_status_page(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), ab.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/settings",
                                         data=b"started=on&failed=on", method="POST")
        with mock.patch.object(ab, "load_state", return_value={}):
            html = urllib.request.urlopen(request, timeout=10).read().decode()  # follows the redirect to the page
        self.assertEqual(alerts.events(), {"started", "failed"})
        self.assertIn("name=started checked", html)
        self.assertIn("name=success>", html)


class TestEndpoint(AlertsCase):
    def test_test_alert_reaches_every_target_whatever_its_tags(self):
        self.set(URLS=f"{NTFY} stale={HASS}", NOTIFY_EVENTS="failed")
        FakeApprise.broken = ("hassio://",)
        result = alerts.test()
        self.assertEqual(len(FakeApprise.sent), 1)
        self.assertIn("1 of 2 target(s)", result)
        self.assertIn("hassio://ha.example.com", result)

    def test_post_sends_and_redirects_to_the_status_page(self):
        self.set(URLS=NTFY)
        server = ThreadingHTTPServer(("127.0.0.1", 0), ab.Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None

        request = urllib.request.Request(f"http://127.0.0.1:{server.server_port}/api/notify-test", data=b"", method="POST")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            urllib.request.build_opener(NoRedirect).open(request, timeout=10)
        self.assertEqual(caught.exception.code, 303)
        self.assertEqual(self.titles(), ["🔔 Android backup: test alert"])
        with mock.patch.object(ab, "load_state", return_value={}):
            self.assertIn("1 of 1 target(s) took the test alert", ab.status_page())


if __name__ == "__main__":
    unittest.main()
