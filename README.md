# android-backup

Backs up your Android phones and tablets to your server over ADB. No root and no app on the
device. Plug a device in and it's backed up, or give it the device's Wi-Fi address.

Any authorised device that's connected gets one backup every `BACKUP_INTERVAL_HOURS`
(default 24), so a drawer full of old phones only needs plugging in one at a time.

This is a fork of [louij2/android-backup](https://github.com/louij2/android-backup). It adds
[Apprise](https://github.com/caronc/apprise) alerts (ntfy, Home Assistant, Discord and more), alerts
for the things that go wrong with a phone on Wi-Fi, and a Home Assistant sensor endpoint. See
[CHANGELOG.md](CHANGELOG.md).

> **The status page has no login.** Anyone who can reach port 9872 can see your devices and start
> a backup. Keep it on your LAN: don't publish the port through a reverse proxy or to the internet.

## What you get

`<backups>/<model>-<serial>/`

| Folder | Contents | Restore with |
|---|---|---|
| `files/` | Everything in shared storage: photos, downloads, documents, `Android/media`, `Android/data` | Copy back |
| `sd-<id>/` | The same for an SD card, if there is one | Copy back |
| `apks/<package>/<versionCode>/` | Every user-installed app, including split APKs | `adb install-multiple` |
| `snapshots/<date>/contacts.vcf` | Contacts, without photos | Any contacts app |
| `snapshots/<date>/sms.xml`, `calls.xml` | SMS text messages and call log | [SMS Backup & Restore](https://www.synctech.com.au/sms-backup-restore/) format |
| `snapshots/<date>/calendar.ics` | Calendar events from every calendar on the device | Any calendar app |
| `snapshots/<date>/` | Package list, settings, `getprop` | Reference |
| `versions/<date>/files/...` | The previous copy of each file that changed on the phone that day | Copy back |

- **`files/` and `sd-<id>/` never delete.** A file deleted on the phone stays in the backup, so the folder
  only grows. A file changed on the phone overwrites the old copy, and the old copy moves to
  `versions/<date>/` for `VERSION_DAYS` (default 30), so a photo damaged or edited by mistake on
  the phone doesn't replace the good one. Files bigger than `VERSION_MAX_MB` are not versioned.
- The last `KEEP_SNAPSHOTS` days of snapshots are kept (default 30).
- A file is copied again if its size or modification time has changed. Unplugging mid-run is
  safe, and the next run picks up where it left off.

## What it can't do

**Private app data can't be backed up without root.** That means logins, app databases and
chat histories kept inside apps. `adb backup` doesn't help: Android 12 and later exclude almost
every app from it. After a restore, apps come back but start logged out. Apps that keep their
own exports in shared storage are covered by `files/`. This can be most of a device: a tablet
with 209 GB used had 178 GB of private app data, whatever the apps keep privately.

MMS messages and contact photos aren't included. The backups aren't encrypted, so keep them on
a private share. `snapshots/` holds your messages and contacts in plain text.

## Run

```sh
docker run -d --name android-backup --restart unless-stopped -p 9872:9872 \
  --device-cgroup-rule='c 189:* rmw' -v /dev/bus/usb:/dev/bus/usb \
  -v /srv/android-backups:/backups \
  -v /srv/android-backup/config:/config \
  -e TZ=Europe/London \
  ghcr.io/iotemylabs/android-backup:latest
```

The device-cgroup rule and the `/dev/bus/usb` mount let devices be plugged in at any time
without `--privileged`.

## Add a device over USB

The status page has the same steps under **Add a device**, with a line that shows when a phone is
waiting for Allow or connected, a checklist to download, and a **Switch to Wi-Fi** button.

1. On the device, open Settings > About and tap **Build number** seven times.
2. In Developer options, turn on **USB debugging**.
3. Also turn on **Disable adb authorization timeout**, if it's there. Otherwise Android forgets
   the server after 7 days without a connection and asks again.
4. Plug it in and tap **Allow** with "Always allow" ticked. The status page shows when a device
   is waiting for this.

Check it worked with `docker exec android-backup android-backup adb devices -l`.

### Samsung notes (written for a Galaxy Z Fold 5)

Nothing in android-backup is Samsung-only, but Samsung adds one setting that can get in the way:

- **Auto Blocker** (Settings > Security and privacy > Auto Blocker) can block commands sent over a
  USB cable. If the phone never shows the Allow prompt, or `adb devices` doesn't list it, look
  there. Turning that protection off means a computer the phone is plugged into can send it
  commands again, once you have allowed that computer's key. That is what ADB needs, and it is
  also what the setting exists to stop, so decide whether you are comfortable with it.
- **Disable adb authorization timeout** is in Developer options, as in step 3.
- A folding phone backs up the same as any other: there is one shared storage for both screens.

## Add a device over Wi-Fi

Authorise it over USB first, then:

```sh
docker exec android-backup android-backup adb -s <serial> tcpip 5555
```

The **Switch to Wi-Fi** button under Add a device on the status page runs the same command for a
device that is plugged in, and shows the address to use.

Add `<device ip>:5555` to `ADB_TARGETS`. Android forgets `tcpip 5555` when it reboots, so repeat
that command after a reboot. Anyone on the network can then reach the device's ADB port, but they
still can't use it unless the device has authorised their key.

A `wifi_lost` alert tells you when this has happened: see [Alerts](#alerts). Give the phone a fixed
address (a DHCP reservation on your router), or `ADB_TARGETS` will point at the wrong place when
its address changes.

## Mobile data (light mode)

A device reached over the network that isn't on Wi-Fi, such as a tablet on Tailscale over LTE,
gets a light backup: new files up to `MOBILE_DATA_LIMIT_MB` (smallest first) and the contacts,
SMS and settings snapshot. App updates and bigger files wait. As soon as the device is back on
Wi-Fi or USB it gets a full backup, without waiting for the next interval.

App updates are most of the data: a phone with 51 apps averaged about 0.5 GB a day of updates,
while its changed files came to 30-50 MB.

## Settings

| Variable | Default | |
|---|---|---|
| `BACKUP_INTERVAL_HOURS` | `24` | Minimum time between good backups of a device |
| `RETRY_MINUTES` | `60` | Wait after a failed attempt |
| `ADB_TARGETS` | | Comma list of `host:port` to connect to |
| `SERIALS` | | Comma list; only back up these devices |
| `EXCLUDE` | caches, thumbnails, `Android/obb/` | Regex matched against paths relative to `/sdcard` or the SD card |
| `MOBILE_DATA_LIMIT_MB` | `500` | Most new-file data per backup on mobile data |
| `KEEP_SNAPSHOTS` | `30` | Dated snapshots to keep per device |
| `VERSION_DAYS` | `30` | Days to keep the previous copy of a changed file. `0` overwrites and keeps nothing |
| `VERSION_MAX_MB` | `500` | A changed file bigger than this is overwritten without keeping its previous copy |
| `ONLY_WHEN_CHARGING` | `false` | A scheduled backup waits until the device is on a charger |
| `BACKUP_WINDOW` | | Scheduled backups only start between these times, e.g. `01:00-06:00` or `22:00-06:00` |
| `APPRISE_URLS` | | Where alerts go: Apprise URLs separated by spaces or commas. See [Alerts](#alerts) |
| `APPRISE_CONFIG` | | Path to an Apprise config file, e.g. `/config/apprise.yml` |
| `NOTIFY_EVENTS` | see [Alerts](#alerts) | Comma list of the events to send |
| `NOTIFY_SUCCESS` | `false` | Also send an alert for each successful backup |
| `STALE_HOURS` | `48` | Alert once when a device has had no good backup for this long. `0` turns it off |
| `WIFI_LOST_HOURS` | `6` | Alert once when an `ADB_TARGETS` address has been unreachable for this long. `0` turns it off |
| `DISCORD_WEBHOOK_URL` | | Legacy, prefer `APPRISE_URLS`. Still works: failed backups are posted to it |
| `PUID` / `PGID` | `99` / `100` | Owner of the finished backup |

## Alerts

Alerts are sent through [Apprise](https://github.com/caronc/apprise), so anything it supports
works. Put one or more Apprise URLs in `APPRISE_URLS`, separated by spaces or commas:

```sh
# ntfy on your own server, with an access token
ntfys://YOUR_NTFY_TOKEN@ntfy.example.com/backups

# Home Assistant: a persistent notification in the HA sidebar
hassio://homeassistant.local/YOUR_LONG_LIVED_TOKEN

# Home Assistant: a push to the Companion app on one phone
hassio://homeassistant.local/YOUR_LONG_LIVED_TOKEN/notify.mobile_app_YOUR_PHONE

# Discord
discord://WEBHOOK_ID/WEBHOOK_TOKEN
```

Use `hassios://` when Home Assistant is behind HTTPS. Add both Home Assistant URLs to get both
kinds of notification. An Apprise URL holds a token, so treat it like a password. android-backup
logs only its scheme and host.

| Event | Sent | Sent by default |
|---|---|---|
| `failed` | Every failed backup. Not when the device was unplugged mid-run: that resumes next time | yes |
| `needs_unlock` | Once, when a phone has rebooted and hasn't been unlocked, so its storage looks empty | yes |
| `unauthorized` | Once, when a phone is waiting for you to tap Allow | yes |
| `wifi_lost` | Once, when an `ADB_TARGETS` address hasn't answered for `WIFI_LOST_HOURS` | yes |
| `stale` | Once, when a device has had no good backup for `STALE_HOURS` | yes |
| `success` | Every good backup | only with `NOTIFY_SUCCESS=true` |
| `started` | Every time a device connects and a backup starts | no |

The "once" alerts are remembered in `/config/alerts.json`, so a restart doesn't repeat them. Each
is sent again only after the problem has gone away and come back.

- **Choose the events** under "Alert settings" on the status page, or with `NOTIFY_EVENTS`, for
  example `started,failed,stale`. A choice saved on the page is kept in `/config/settings.json`
  and from then on wins over `NOTIFY_EVENTS` and `NOTIFY_SUCCESS`.
- **Send an event to one place only** by putting event names in front of a URL:
  `stale,failed=hassio://homeassistant.local/YOUR_LONG_LIVED_TOKEN`. A URL with nothing in front
  gets every event.
- **ntfy priority** follows the alert: high for a failure, default for a warning, low for a
  success. A `priority=` in the URL overrides it.
- **A config file** works too: set `APPRISE_CONFIG=/config/apprise.yml`. Entries with no tag get
  every event, and entries tagged with an event name get that event.
- **Test it** with the "Send test alert" button on the status page. It sends one message to every
  target and shows which ones took it.

**Upgrading from Discord only.** An install with just `DISCORD_WEBHOOK_URL` (and `NOTIFY_SUCCESS`)
keeps working with no changes and gets the same alerts as before: failed backups, and good ones
if you asked for them. The new events start when you set `APPRISE_URLS`, `APPRISE_CONFIG` or
`NOTIFY_EVENTS`.

## Home Assistant

`GET /api/ha` returns one entry per phone:

```json
[{"serial": "PHONE_SERIAL", "name": "SM-F946U-PHONE_SERIAL", "result": "ok", "running": false,
  "connected": true, "transport": "Wi-Fi", "last_backup": "2026-11-20T08:15:02+00:00",
  "last_attempt": "2026-11-20T08:02:28+00:00", "size_bytes": 81234567890,
  "duration_seconds": 754, "message": "41210 files (12 new, 0.21 GB); ..."}]
```

`result` is one of `ok`, `failed`, `needs_unlock`, `running` or `never`. Each entry also has
`nickname` (the name given on the status page) and `progress` (0 to 100 while a backup runs).

[homeassistant/android-backup.yaml](homeassistant/android-backup.yaml) turns that into sensors
for one phone (last backup, last result, size, duration, progress, connected, running) with no
MQTT broker. One automation keeps a single persistent notification in the Home Assistant sidebar
updated with the percentage while a backup runs, then replaces it with the result. It also has
an automation that sends a Companion app notification when the last backup is more
than 48 hours old. That check runs on Home Assistant's own clock, so it still fires when
android-backup or the whole server is down, which is the one case android-backup's own `stale`
alert can't cover. Replace `SERVER_IP`, `PHONE_SERIAL` and `mobile_app_YOUR_PHONE` in the file.

`ONLY_WHEN_CHARGING` and `BACKUP_WINDOW` apply to scheduled backups. "Back up now" always runs.

## Status and metrics

- `http://server:9872/`: status page with a "Back up now" button per device
- `/metrics`: Prometheus, including `android_backup_last_success_timestamp_seconds`,
  `android_backup_last_ok`, `android_backup_files_failed` and `android_backup_safe_to_unplug`
- `POST /api/backup?serial=<serial>`: start a backup; leave out `serial` for every connected device
- The status page refreshes itself, shows a progress bar while a backup runs, and lets you give
  each device a nickname with **Rename**. A nickname is a display name for the page, alerts and
  Home Assistant only: the backup folder stays `<model>-<serial>`.
- `GET /api/ha`: one entry per device for [Home Assistant](#home-assistant)
- `POST /api/nickname?serial=<serial>` (form field `nickname`) and `POST /api/settings` (one
  form field per event to send): what the page's Rename and Alert settings forms use
- `POST /api/notify-test`: send a test alert to every target

None of these has a login. Keep port 9872 on your LAN.

## Command line

```sh
docker exec android-backup android-backup backup [serial...]
docker exec android-backup android-backup status
docker exec android-backup android-backup adb devices -l
```

## License

MIT
