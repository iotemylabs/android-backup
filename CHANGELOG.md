# Changelog

This fork tracks [louij2/android-backup](https://github.com/louij2/android-backup). Versions are
upstream's version plus `-apprise.N`.

## 1.0.5-apprise.1

Based on upstream 1.0.5.

### Added

- Alerts go through [Apprise](https://github.com/caronc/apprise), so ntfy, Home Assistant, Discord
  and its other services all work. New settings: `APPRISE_URLS`, `APPRISE_CONFIG`, `NOTIFY_EVENTS`,
  `STALE_HOURS`, `WIFI_LOST_HOURS`.
- New alerts, each sent once and remembered across restarts in `/config/alerts.json`:
  - `needs_unlock`: the phone rebooted and hasn't been unlocked yet;
  - `unauthorized`: a phone is waiting for Allow;
  - `wifi_lost`: an `ADB_TARGETS` address has been unreachable for `WIFI_LOST_HOURS`;
  - `stale`: no good backup for `STALE_HOURS`.
- ntfy alerts get a priority that follows the alert: high for a failure, default for a warning,
  low for a success. A `priority=` in the URL wins.
- Older versions: when a file changes on the phone, its previous copy is kept in
  `versions/<date>/` for `VERSION_DAYS` (default 30). Upstream overwrote it. `VERSION_DAYS=0`
  restores that. Files over `VERSION_MAX_MB` (default 500) are not versioned.
- `ONLY_WHEN_CHARGING` and `BACKUP_WINDOW`: scheduled backups can wait for a charger and for a
  time of day. Both are off by default.
- `snapshots/<date>/calendar.ics`: calendar events, next to contacts, SMS and calls.
- `started` alert, off by default: a device connected and its backup began.
- A new status page: a card per device with coloured states, a moving progress bar, a stale badge
  and live refresh. Still one self-contained page with no extra files or dependencies.
- Device nicknames, set with Rename on the status page. Shown on the page, in alerts and in
  `/api/ha`; the backup folder name never changes.
- "Alert settings" on the status page: tick the alerts to send. Saved in `/config/settings.json`,
  and from then on it wins over `NOTIFY_EVENTS` and `NOTIFY_SUCCESS`.
- "Add a device" on the status page: the setup steps, a live line that says when a phone is
  waiting for Allow or connected, a checklist to download (`/setup-guide.txt`), and a
  "Switch to Wi-Fi" button (`POST /api/wifi?serial=<serial>`) that runs `adb tcpip 5555` on a
  USB-connected device and shows the address for `ADB_TARGETS`.
- `POST /api/notify-test` and a "Send test alert" button on the status page.
- `GET /api/ha`: one flat entry per phone for a Home Assistant REST sensor.
- `homeassistant/android-backup.yaml`: sensors, a persistent notification that shows backup
  progress, and a stale-backup automation.
- `requirements.txt` (Apprise, pinned). It is the first dependency outside the standard library.

### Changed

- `DISCORD_WEBHOOK_URL` still works and is now delivered through Apprise. An install with only
  `DISCORD_WEBHOOK_URL` and `NOTIFY_SUCCESS` gets the same alerts as before, at the same times.
  The message now has a title line and a body where it used to be one line.
- `state.json` gains `needs_unlock` and `nickname` fields per device.
- The Unraid template and `deploy.sh` point at this fork's image, `ghcr.io/iotemylabs/android-backup`.
  Upstream's deploy script is kept as `contrib/deploy-tower.sh`.
- `tests/test_light_mode.py` uses `os.devnull`, so the tests also run on Windows.
