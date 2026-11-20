"""android-backup: backs up Android devices over ADB, with no root and no app on the phone.

Any authorised device that is plugged in, or reachable at an ADB_TARGETS address, is backed up
once every BACKUP_INTERVAL_HOURS. Each device gets <backup dir>/<model>-<serial>/:

  files/       mirror of shared storage (/sdcard). Files deleted on the phone are kept here.
  versions/    <date>/ the previous copy of each file that changed on the phone
  sd-<id>/     the same for an SD card, if there is one
  apks/        <package>/<versionCode>/*.apk for every user-installed app
  snapshots/   <date>/ contacts.vcf, sms.xml, calls.xml, calendar.ics, packages.txt, settings and getprop

Private app data (/data/data) can't be reached without root.

Commands:
  serve                 device watcher + status page (default)
  backup [SERIAL...]    back up connected devices now, in the foreground
  status                print the last result for each device
  adb ARGS...           run adb with this container's key (e.g. `adb devices -l`)
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from xml.sax.saxutils import quoteattr

from . import __version__, alerts, page

CONFIG = Path(os.environ.get("CONFIG_DIR", "/config"))
BACKUPS = Path(os.environ.get("BACKUP_DIR", "/backups"))
INTERVAL_HOURS = float(os.environ.get("BACKUP_INTERVAL_HOURS", "24"))
RETRY_MINUTES = float(os.environ.get("RETRY_MINUTES", "60"))
TARGETS = [t.strip() for t in os.environ.get("ADB_TARGETS", "").split(",") if t.strip()]
SERIALS = {s.strip() for s in os.environ.get("SERIALS", "").split(",") if s.strip()}
EXCLUDE = re.compile(os.environ.get(
    "EXCLUDE", r"^Android/obb/|^Android/data/[^/]+/cache/|(^|/)\.thumbnails/|(^|/)\.Trash|^\.trash-storage/"
    r"|^\.Spotlight-V100/|^\.fseventsd/|^LOST\.DIR/"))
KEEP_SNAPSHOTS = int(os.environ.get("KEEP_SNAPSHOTS", "30"))
MOBILE_LIMIT = float(os.environ.get("MOBILE_DATA_LIMIT_MB", "500")) * 1e6
PORT = int(os.environ.get("PORT", "9872"))
PUID = int(os.environ.get("PUID", "99"))  # Unraid's nobody:users, so the backups are usable over SMB
PGID = int(os.environ.get("PGID", "100"))
VERSION_DAYS = float(os.environ.get("VERSION_DAYS") or 30)  # 0 = overwrite changed files, as upstream does
VERSION_MAX = float(os.environ.get("VERSION_MAX_MB") or 500) * 1e6
ONLY_WHEN_CHARGING = os.environ.get("ONLY_WHEN_CHARGING", "false").lower() in ("1", "true", "yes")
BACKUP_WINDOW = os.environ.get("BACKUP_WINDOW", "").strip()  # e.g. 01:00-06:00; blank = any time
POLL_SECONDS = 30
CONNECT_EVERY = 300

STATE_FILE = CONFIG / "state.json"
LOG_DIR = CONFIG / "logs"
_lock = threading.Lock()  # one backup at a time
_state_lock = threading.Lock()
_seen = {"ready": {}, "unauthorized": [], "connected": {}, "online": set()}  # latest poll
_notes = {"wifi": ""}  # the last Switch to Wi-Fi result, for the status page


def log(msg):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


# --- state ---------------------------------------------------------------------------

def load_state():
    try:
        return json.loads(STATE_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def update_state(serial, **fields):
    with _state_lock:
        st = load_state()
        st.setdefault(serial, {}).update(fields)
        CONFIG.mkdir(parents=True, exist_ok=True)
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, sort_keys=True))
        tmp.replace(STATE_FILE)


# --- adb -----------------------------------------------------------------------------

def adb(*args, timeout=120):
    return subprocess.run(["adb", *args], capture_output=True, text=True, timeout=timeout, errors="surrogateescape")


def shell(adb_serial, command, timeout=300):
    """Run a device command without a pty, so stdout is clean and the exit code is real."""
    return adb("-s", adb_serial, "shell", "-T", command, timeout=timeout)


def online(adb_serial):
    try:
        return adb("-s", adb_serial, "get-state", timeout=20).stdout.strip() == "device"
    except subprocess.TimeoutExpired:
        return False


def connect_targets():
    for target in TARGETS:
        try:
            adb("connect", target, timeout=15)
        except subprocess.TimeoutExpired:
            pass


def list_devices():
    """(ready, unauthorized). ready maps the device's real serial to its best adb address, USB first."""
    try:
        lines = adb("devices", timeout=30).stdout.splitlines()[1:]
    except subprocess.TimeoutExpired:
        return {}, []
    ready, unauthorized, online = {}, [], set()
    for line in lines:
        parts = line.split()
        if len(parts) < 2:
            continue
        address, status = parts[0], parts[1]
        if status == "unauthorized":
            unauthorized.append(address)
        elif status == "device":
            online.add(address)  # every address, so a Wi-Fi target still counts while USB is the best one
            try:
                serial = shell(address, "getprop ro.serialno", timeout=20).stdout.strip()
            except subprocess.TimeoutExpired:
                continue
            is_network = transport_of(address) == "wifi"
            if not serial:  # a USB address is the serial; a network one would back the device up twice
                if is_network:
                    log(f"{address}: no serial number yet, skipping this poll")
                    continue
                serial = address
            if serial not in ready or not is_network:
                ready[serial] = address
    _seen["online"] = online
    return ({s: a for s, a in ready.items() if not SERIALS or s in SERIALS}, unauthorized)


def transport_of(address):
    return "wifi" if ":" in address or "._tcp" in address else "usb"


def on_mobile_data(a):
    """True when a device reached over the network isn't on Wi-Fi (Tailscale over mobile data).

    If the check itself fails, assume mobile data: a cut-down backup is the safe mistake.
    """
    if transport_of(a) == "usb":
        return False
    try:
        out = shell(a, "cmd wifi status 2>/dev/null; dumpsys wifi 2>/dev/null | grep -m1 mNetworkInfo", timeout=60).stdout
    except subprocess.TimeoutExpired:
        return True
    return not ("Wifi is connected" in out or "state: CONNECTED" in out)


def usb_nodes():
    try:
        return {f"{bus.name}/{dev.name}" for bus in Path("/dev/bus/usb").iterdir() for dev in bus.iterdir()}
    except OSError:
        return set()


def device_label(adb_serial, serial):
    model = shell(adb_serial, "getprop ro.product.model", timeout=20).stdout.strip() or "android"
    return re.sub(r"[^A-Za-z0-9._-]+", "_", model) + "-" + re.sub(r"[^A-Za-z0-9._-]+", "_", serial)


# --- shared storage + APKs -----------------------------------------------------------

class Disconnected(Exception):
    pass


class NeedsUnlock(RuntimeError):  # its own alert: the fix is a tap, not a look at the logs
    pass


STAT_RE = re.compile(r"^(\d+) (\d+) (.+)$")


def storage_roots(a):
    """[(device path, backup folder)]: shared storage, then any SD card."""
    roots = [("/sdcard/", "files")]
    for name in shell(a, "ls /storage", timeout=60).stdout.split():
        if name not in ("emulated", "self"):
            roots.append((f"/storage/{name}/", f"sd-{name}"))
    return roots


def list_files(a, root):
    """[(size, mtime, relative path)] under root; raises if the listing is empty or comes up short.

    Piped through xargs: `find -exec stat {} +` stops with "Argument list too long" on a big
    tree and silently returns a partial list.
    """
    found = shell(a, f"find {root} -type f 2>/dev/null | wc -l", timeout=1800).stdout.strip()
    r = shell(a, f"find {root} -type f -print0 2>/dev/null | xargs -0 stat -c '%s %Y %n' 2>/dev/null", timeout=1800)
    lines = r.stdout.splitlines()
    expected = int(found) if found.isdigit() else 0
    if len(lines) < expected - max(10, expected // 100):  # a few files can vanish between the two commands
        raise RuntimeError(f"incomplete listing of {root}: {len(lines)} of {expected} files")
    if root == "/sdcard/" and not lines:  # before the first unlock after a reboot, shared storage looks empty
        raise NeedsUnlock("shared storage is empty or unreadable (unlock the phone once after a reboot)")
    out = []
    for line in lines:
        m = STAT_RE.match(line)
        if m and m.group(3).startswith(root):
            rel = m.group(3)[len(root):]
            if not EXCLUDE.search(rel):
                out.append((int(m.group(1)), int(m.group(2)), rel))
    return out


def pull(a, remote, dest, size=0):
    """Pull into a .part file so an interrupted copy never looks finished. Returns the adb error or None."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    timeout = 300 + size / 1_000_000  # 1 MB/s floor, so a wedged adbd can't hang the whole run
    try:
        r = adb("-s", a, "pull", "-a", remote, str(part), timeout=timeout)
    except subprocess.TimeoutExpired:
        part.unlink(missing_ok=True)
        return "timed out"
    if r.returncode != 0:
        part.unlink(missing_ok=True)
        return (r.stderr or r.stdout).strip()[-200:]
    part.replace(dest)
    return None


def version_path(top, folder, rel, old_size):
    """Where today's previous copy of a changed file goes, or None when versions are off or it is too big."""
    if not VERSION_DAYS or old_size > VERSION_MAX:
        return None
    return top / "versions" / f"{dt.date.today():%Y-%m-%d}" / folder / rel


def keep_version(dest, keep):
    """Hold on to the copy that is about to be replaced, so a file damaged on the phone can't erase the good one.

    A hard link costs no space or time. A share that can't link gets a real copy.
    """
    if not keep or keep.exists() or not dest.exists():  # the first copy of the day is the one worth keeping
        return False
    keep.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(dest, keep)
    except OSError:
        shutil.copy2(dest, keep)
    return True


def prune_versions(top):
    cutoff = f"{dt.date.today() - dt.timedelta(days=VERSION_DAYS):%Y-%m-%d}"
    for old in (top / "versions").glob("*"):
        if VERSION_DAYS and old.name < cutoff:
            shutil.rmtree(old, ignore_errors=True)


def sync_files(a, serial, top, lf, limit=None):
    """Copy new and changed files. With a byte limit, files past it wait for the next run."""
    todo, n_files = [], 0
    for root, folder in storage_roots(a):
        files = list_files(a, root)
        n_files += len(files)
        for size, mtime, rel in files:
            keep = None
            try:
                st = (top / folder / rel).stat()
                if st.st_size == size and abs(st.st_mtime - mtime) <= 1:
                    continue
                keep = version_path(top, folder, rel, st.st_size)
            except OSError:
                pass
            todo.append((size, root, folder, rel, keep))
    deferred = 0
    if limit is not None:
        kept, budget = [], limit
        for t in sorted(todo, key=lambda t: t[0]):  # smallest first, so one big video doesn't hold back the rest
            if t[0] <= budget:
                kept.append(t)
                budget -= t[0]
            else:
                deferred += t[0]
        todo = kept
    total = sum(t[0] for t in todo) or 1
    done = copied = failed = 0
    log(f"{serial}: {n_files} files on the device, {len(todo)} to copy ({total / 1e9:.2f} GB)"
        + (f", {deferred / 1e9:.2f} GB waiting for Wi-Fi" if deferred else ""))
    last_pct = -1
    for size, root, folder, rel, keep in todo:
        kept = keep_version(top / folder / rel, keep)
        err = pull(a, root + rel, top / folder / rel, size)
        if err:
            if kept:
                keep.unlink(missing_ok=True)  # nothing was replaced, so it isn't an older version
            if not online(a):
                raise Disconnected("device went away during the file copy")
            failed += 1
            lf.write(f"FAILED {root}{rel}: {err}\n".encode(errors="surrogateescape"))
        else:
            copied += 1
        done += size
        pct = int(done * 100 / total)
        if pct != last_pct:
            last_pct = pct
            update_state(serial, progress=pct)
    prune_versions(top)
    return n_files, copied, failed, done, deferred


PKG_RE = re.compile(r"^package:(\S+) versionCode:(\d+)")


def sync_apks(a, top, lf):
    copied = failed = 0
    for line in shell(a, "pm list packages -3 --show-versioncode", timeout=120).stdout.splitlines():
        m = PKG_RE.match(line.strip())
        if not m:
            continue
        pkg, version = m.groups()
        dest = top / "apks" / pkg / version
        if (dest / ".complete").exists():
            continue
        ok = True
        for path in shell(a, f"pm path {pkg}", timeout=60).stdout.split():
            if path.startswith("package:"):
                remote = path.removeprefix("package:")
                err = pull(a, remote, dest / os.path.basename(remote))
                if err:
                    if not online(a):
                        raise Disconnected("device went away during the APK copy")
                    ok = False
                    lf.write(f"FAILED {pkg}: {err}\n".encode(errors="surrogateescape"))
        if ok and dest.exists():
            (dest / ".complete").touch()
            copied += 1
        else:
            failed += 1
    return copied, failed


# --- snapshots -----------------------------------------------------------------------

def query(a, uri, columns):
    """Rows of a content provider. Free-text columns must come last: a column ends where the next begins."""
    r = shell(a, f"content query --uri {uri} --projection {':'.join(columns)}", timeout=600)
    if "Row: " not in r.stdout:
        if r.returncode != 0 or "Exception" in r.stderr + r.stdout:
            raise RuntimeError((r.stderr or r.stdout).strip()[-200:])
        return []
    rows = []
    for chunk in re.split(r"(?m)^Row: \d+ ", r.stdout)[1:]:
        row, rest = {}, chunk.rstrip("\n")
        for i, col in enumerate(columns):
            rest = rest.removeprefix(f"{col}=")
            if i + 1 < len(columns):
                value, _, rest = rest.partition(f", {columns[i + 1]}=")
                rest = f"{columns[i + 1]}={rest}"
            else:
                value = rest
            row[col] = "null" if value == "NULL" else value
        rows.append(row)
    return rows


def clean(value):
    """XML 1.0 can't carry control characters or lone surrogates from undecodable bytes."""
    return quoteattr(re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]", "", value))


def write_xml(path, root, tag, rows, readable_date=True):
    """The XML format of the 'SMS Backup & Restore' app, so these can be restored with it."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"<?xml version='1.0' encoding='UTF-8' standalone='yes' ?>\n<{root} count=\"{len(rows)}\">\n")
        for row in rows:
            attrs = dict(row)
            if readable_date and attrs.get("date", "").isdigit():
                attrs["readable_date"] = dt.datetime.fromtimestamp(int(attrs["date"]) / 1000).strftime("%d %b %Y %H:%M:%S")
            f.write(f"  <{tag} " + " ".join(f"{k}={clean(v)}" for k, v in attrs.items()) + " />\n")
        f.write(f"</{root}>\n")


CONTACT_COLUMNS = ["contact_id", "mimetype", "data2", "data3", "data4", "data1"]
PHONE_TYPES = {"1": "HOME", "2": "CELL", "3": "WORK", "4": "WORK,FAX", "5": "HOME,FAX", "6": "PAGER"}
EMAIL_TYPES = {"1": "HOME", "2": "WORK"}


def vcard_text(value):
    value = "" if value == "null" else value
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def typed(prop, kinds, row):
    kind = kinds.get(row["data2"])
    return f"{prop};TYPE={kind}:{vcard_text(row['data1'])}" if kind else f"{prop}:{vcard_text(row['data1'])}"


def vcard_line(row):
    kind = row["mimetype"].rpartition("/")[2]
    if row["data1"] == "null" and kind != "organization":
        return None
    if kind == "name":
        return f"FN:{vcard_text(row['data1'])}\r\nN:{vcard_text(row['data3'])};{vcard_text(row['data2'])};;;"
    if kind == "phone_v2":
        return typed("TEL", PHONE_TYPES, row)
    if kind == "email_v2":
        return typed("EMAIL", EMAIL_TYPES, row)
    if kind == "organization":
        org = vcard_text(row["data1"])
        title = vcard_text(row["data4"])
        return "\r\n".join(([f"ORG:{org}"] if org else []) + ([f"TITLE:{title}"] if title else [])) or None
    simple = {"nickname": "NICKNAME", "note": "NOTE", "website": "URL"}
    if kind in simple:
        return f"{simple[kind]}:{vcard_text(row['data1'])}"
    if kind == "postal-address_v2":
        return f"ADR:;;{vcard_text(row['data1'])};;;;"
    return None  # photos, groups and app-specific rows don't carry over as text


def export_contacts(a, path):
    """vCards built from the raw contact rows: the provider's own vCard export needs asset-file access."""
    people = {}
    for row in query(a, "content://com.android.contacts/data", CONTACT_COLUMNS):
        line = vcard_line(row)
        if line:
            people.setdefault(row["contact_id"], []).append(line)
    with open(path, "w", encoding="utf-8", errors="replace", newline="") as f:
        for lines in people.values():
            if not any(l.startswith("FN:") for l in lines):
                lines.insert(0, "FN:\r\nN:;;;;")
            f.write("BEGIN:VCARD\r\nVERSION:3.0\r\n" + "\r\n".join(lines) + "\r\nEND:VCARD\r\n")
    return len(people)


EVENT_COLUMNS = ["_id", "dtstart", "dtend", "allDay", "deleted", "duration", "rrule", "eventLocation", "title",
                 "description"]


def ics_text(value):
    value = "" if value == "null" else value
    return value.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\r", "").replace("\n", "\\n")


def ics_time(ms, all_day):
    when = dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc)
    return f";VALUE=DATE:{when:%Y%m%d}" if all_day else f":{when:%Y%m%dT%H%M%SZ}"


def ics_event(row):
    """One VEVENT, or None for a deleted row or one with no start."""
    if row["deleted"] == "1" or not row["dtstart"].isdigit():
        return None
    all_day = row["allDay"] == "1"
    lines = ["BEGIN:VEVENT", f"UID:{row['_id']}@android-backup", "DTSTART" + ics_time(row["dtstart"], all_day)]
    if row["dtend"].isdigit():
        lines.append("DTEND" + ics_time(row["dtend"], all_day))
    elif row["duration"] != "null":  # a repeating event has a duration, not an end
        lines.append(f"DURATION:{row['duration']}")
    if row["rrule"] != "null":
        lines.append(f"RRULE:{row['rrule']}")
    for prop, col in (("SUMMARY", "title"), ("LOCATION", "eventLocation"), ("DESCRIPTION", "description")):
        if row[col] != "null" and row[col]:
            lines.append(f"{prop}:{ics_text(row[col])}")
    return lines + ["END:VEVENT"]


def export_calendar(a, path):
    """Every calendar on the phone as one .ics file, which any calendar app can import."""
    events = [e for e in map(ics_event, query(a, "content://com.android.calendar/events", EVENT_COLUMNS)) if e]
    with open(path, "w", encoding="utf-8", errors="replace", newline="") as f:
        f.write("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//android-backup//EN\r\n")
        for lines in events:
            for line in lines:  # RFC 5545: a line is at most 75 characters; a continuation starts with a space
                f.write("\r\n ".join(line[i:i + 73] for i in range(0, len(line), 73)) + "\r\n")
        f.write("END:VCALENDAR\r\n")
    return len(events)


SMS_COLUMNS = ["address", "date", "date_sent", "type", "read", "status", "locked", "protocol", "service_center",
               "subject", "body"]
CALL_COLUMNS = ["number", "duration", "date", "type", "presentation", "name"]


def snapshot(a, top, lf):
    """Dated copies, so a phone that lost its messages can't overwrite the last good copy."""
    snap = top / "snapshots" / f"{dt.date.today():%Y-%m-%d}"
    snap.mkdir(parents=True, exist_ok=True)
    counts, problems = {}, []

    def attempt(name, func):
        try:
            counts[name] = func()
        except (RuntimeError, subprocess.SubprocessError, OSError) as e:
            problems.append(name)
            lf.write(f"FAILED {name}: {e}\n".encode(errors="surrogateescape"))

    def sms():
        rows = query(a, "content://sms", SMS_COLUMNS)
        write_xml(snap / "sms.xml", "smses", "sms", rows)
        return len(rows)

    def calls():
        rows = query(a, "content://call_log/calls", CALL_COLUMNS)
        for r in rows:
            r["contact_name"] = r.pop("name")
        write_xml(snap / "calls.xml", "calls", "call", rows)
        return len(rows)

    attempt("contacts", lambda: export_contacts(a, snap / "contacts.vcf"))
    attempt("sms", sms)
    attempt("calls", calls)
    attempt("events", lambda: export_calendar(a, snap / "calendar.ics"))
    for name, command in (("packages.txt", "pm list packages -f --show-versioncode"), ("getprop.txt", "getprop"),
                          ("settings-global.txt", "settings list global"), ("settings-secure.txt", "settings list secure"),
                          ("settings-system.txt", "settings list system")):
        try:
            (snap / name).write_bytes(shell(a, command, timeout=120).stdout.encode(errors="surrogateescape"))
        except subprocess.SubprocessError as e:
            problems.append(name)
            lf.write(f"FAILED {name}: {e}\n".encode())
    for old in sorted((top / "snapshots").iterdir())[:-KEEP_SNAPSHOTS]:
        shutil.rmtree(old, ignore_errors=True)
    return counts, problems


# --- backup --------------------------------------------------------------------------

def fix_ownership(top):
    """adb runs as root; hand the backup to PUID:PGID. Group-only: it holds messages and contacts."""
    for root, dirs, files in os.walk(top):
        for name, mode in [(n, 0o770) for n in dirs] + [(n, 0o660) for n in files]:
            path = os.path.join(root, name)
            try:
                os.chown(path, PUID, PGID)
                os.chmod(path, mode)
            except OSError as e:
                log(f"could not fix ownership of {path}: {e}")
    os.chown(top, PUID, PGID)
    os.chmod(top, 0o770)


def backup_size(top):
    total = 0
    for root, _dirs, files in os.walk(top):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def backup_one(serial, a):
    """One backup of one device. Returns True on success."""
    label = device_label(a, serial)
    top = BACKUPS / label
    top.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logfile = LOG_DIR / f"{label}-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    started = time.time()
    transport = transport_of(a)
    light = on_mobile_data(a)
    update_state(serial, name=label, running=True, last_attempt=started, transport=transport, progress=0)
    log(f"{label}: backup starting over {transport}" + (" (mobile data: light mode)" if light else ""))
    shown = load_state().get(serial, {}).get("nickname") or label  # the folder keeps <model>-<serial>
    alerts.backup_started(shown, transport, light)
    gone = locked = False
    try:
        with open(logfile, "wb") as lf:
            n_files, copied, failed, copied_bytes, deferred = sync_files(
                a, serial, top, lf, limit=MOBILE_LIMIT if light else None)
            apks, apk_failed = (0, 0) if light else sync_apks(a, top, lf)
            counts, problems = snapshot(a, top, lf)
        parts = ["light mode (mobile data): apps wait for Wi-Fi"] if light else []
        parts += [f"{n_files} files ({copied} new, {copied_bytes / 1e9:.2f} GB)"]
        if deferred:
            parts.append(f"{deferred / 1e9:.2f} GB of files wait for Wi-Fi")
        if not light:
            parts.append(f"{apks} new APKs")
        parts += [f"{v} {k}" for k, v in counts.items()]
        if failed or apk_failed:
            parts.append(f"{failed} files + {apk_failed} APKs could not be read")
        if problems:
            parts.append("no " + ", ".join(problems))
        message, ok = "; ".join(parts), True
        fields = dict(last_success=time.time(), files=n_files, files_failed=failed, apks_failed=apk_failed,
                      copied_bytes=copied_bytes, snapshot_problems=len(problems), progress=100, light=light,
                      deferred_bytes=deferred)
    except Disconnected as e:
        message, ok, gone, fields = str(e), False, True, {}
    except (RuntimeError, subprocess.SubprocessError, OSError) as e:
        message, ok, locked, fields = str(e)[-300:], False, isinstance(e, NeedsUnlock), {}
    fix_ownership(top)
    duration = time.time() - started
    update_state(serial, running=False, last_rc=0 if ok else 1, last_duration=round(duration), last_message=message,
                 needs_unlock=locked, last_log=str(logfile), size_bytes=backup_size(top), **fields)
    log(f"{label}: {'OK' if ok else 'FAILED'} in {duration:.0f}s: {message}")
    for old in sorted(LOG_DIR.glob(f"{label}-*.log"))[:-30]:  # keep the last 30 logs per device
        old.unlink(missing_ok=True)
    alerts.backup_result(serial, shown, ok, gone, locked, message)
    return ok


def window(text):
    """(start, end) times from "HH:MM-HH:MM", or None for any time."""
    try:
        start, end = (dt.time.fromisoformat(part.strip()) for part in text.split("-"))
        return start, end
    except ValueError:
        if text:
            log(f"BACKUP_WINDOW {text!r} isn't HH:MM-HH:MM, so backups can run at any time")
        return None


WINDOW = window(BACKUP_WINDOW)
_charging = {}  # serial -> (when asked, answer): due() runs every few seconds, the phone needn't be asked that often


def in_window(now):
    if not WINDOW:
        return True
    start, end = WINDOW
    return start <= now < end if start <= end else now >= start or now < end  # 22:00-06:00 runs past midnight


def charging(serial, a):
    """True when the device is on a charger. If the phone can't be asked, wait: a backup can drain a battery."""
    asked, answer = _charging.get(serial, (0, False))
    if time.time() - asked >= 60:
        try:
            answer = bool(re.search(r"(AC|USB|Wireless|Dock) powered: true", shell(a, "dumpsys battery", timeout=30).stdout))
        except subprocess.TimeoutExpired:
            answer = False
        _charging[serial] = (time.time(), answer)
    return answer


def allowed_now(serial, a):
    """The rules for a scheduled backup. "Back up now" doesn't ask."""
    return in_window(dt.datetime.now().time()) and (not ONLY_WHEN_CHARGING or charging(serial, a))


def due(serial, a):
    """Every BACKUP_INTERVAL_HOURS, or straight away once a light-mode device is off mobile data again."""
    s = load_state().get(serial, {})
    now = time.time()
    if now - s.get("last_attempt", 0) < RETRY_MINUTES * 60:
        return False
    if now - s.get("last_success", 0) >= INTERVAL_HOURS * 3600:
        return allowed_now(serial, a)
    return bool(s.get("light")) and not on_mobile_data(a) and allowed_now(serial, a)


def schedule_rules():
    rules = (["only while charging"] if ONLY_WHEN_CHARGING else []) + (
        [f"between {WINDOW[0]:%H:%M} and {WINDOW[1]:%H:%M}"] if WINDOW else [])
    return ", ".join(rules)


def poller():
    """Keep the device list fresh, also during a backup, so a newly plugged-in device shows up at once."""
    last_connect, known_usb = 0, usb_nodes()
    while True:
        nodes = usb_nodes()
        # adb's libusb backend never hears about hotplug inside a container, so restart it to rescan
        # when something is plugged in. Not during a backup: that would cut it off.
        if nodes - known_usb and _lock.acquire(blocking=False):
            try:
                log("new USB device: restarting adb to pick it up")
                adb("kill-server", timeout=30)
                adb("start-server", timeout=30)
                last_connect = 0
            finally:
                _lock.release()
            known_usb = nodes
        elif not nodes - known_usb:
            known_usb = nodes
        if TARGETS and time.time() - last_connect >= CONNECT_EVERY:
            connect_targets()
            last_connect = time.time()
        ready, unauthorized = list_devices()
        _seen.update(ready=ready, unauthorized=unauthorized, connected={s: transport_of(a) for s, a in ready.items()})
        alerts.check_devices(ready, unauthorized, _seen["online"], TARGETS)
        time.sleep(POLL_SECONDS)


def watcher():
    """Back up whichever devices are present and due, one at a time."""
    while True:
        for serial, a in dict(_seen["ready"]).items():
            if due(serial, a):
                with _lock:
                    backup_one(serial, a)
        time.sleep(5)


def stale_watch():
    """Its own light timer, so a stuck adb call in the poller can't hold up the stale alert."""
    while True:
        alerts.check_stale(load_state())
        time.sleep(60)


def run_now(serials=None):
    ready, _ = list_devices()
    results = {}
    for serial, a in ready.items():
        if not serials or serial in serials:
            with _lock:
                results[serial] = backup_one(serial, a)
    return results


# --- web -----------------------------------------------------------------------------

def label_escape(value):
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def metrics():
    st = load_state()
    lines = ["# HELP android_backup_info Exporter info", "# TYPE android_backup_info gauge",
             f'android_backup_info{{version="{__version__}"}} 1',
             "# TYPE android_backup_unauthorized_devices gauge",
             f"android_backup_unauthorized_devices {len(_seen['unauthorized'])}"]
    series = {
        "last_success_timestamp_seconds": "last_success",
        "last_attempt_timestamp_seconds": "last_attempt",
        "last_duration_seconds": "last_duration",
        "size_bytes": "size_bytes",
        "files": "files",
        "files_failed": "files_failed",
        "apks_failed": "apks_failed",
        "snapshot_problems": "snapshot_problems",
    }
    for metric, key in series.items():
        lines.append(f"# TYPE android_backup_{metric} gauge")
        for serial, s in st.items():
            if key in s:
                lines.append(f'android_backup_{metric}{{serial="{label_escape(serial)}",name="{label_escape(s.get("name", serial))}"}} {s[key]}')
    for name in ("last_ok", "running", "progress_percent", "connected", "safe_to_unplug", "light_mode", "deferred_bytes"):
        lines.append(f"# TYPE android_backup_{name} gauge")
    for serial, s in st.items():
        lab = f'serial="{label_escape(serial)}",name="{label_escape(s.get("name", serial))}"'
        running = bool(s.get("running"))
        if "last_rc" in s:
            lines.append(f"android_backup_last_ok{{{lab}}} {1 if s['last_rc'] == 0 else 0}")
        lines.append(f"android_backup_running{{{lab}}} {1 if running else 0}")
        lines.append(f"android_backup_progress_percent{{{lab}}} {s.get('progress', 0) if running else 0}")
        lines.append(f"android_backup_connected{{{lab}}} {1 if serial in _seen['connected'] else 0}")
        lines.append(f"android_backup_safe_to_unplug{{{lab}}} {0 if running and s.get('transport') == 'usb' else 1}")
        lines.append(f"android_backup_light_mode{{{lab}}} {1 if s.get('light') else 0}")
        lines.append(f"android_backup_deferred_bytes{{{lab}}} {s.get('deferred_bytes', 0)}")
    return "\n".join(lines) + "\n"


def iso(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).isoformat(timespec="seconds") if ts else None


def devices():
    """Every known or connected device, as the status page and /api/ha both show it."""
    st = load_state()
    out = []
    for serial in sorted(set(st) | set(_seen["connected"])):
        s = st.get(serial, {})
        running = bool(s.get("running"))
        result = "running" if running else "never" if "last_rc" not in s else "ok" if s["last_rc"] == 0 else (
            "needs_unlock" if s.get("needs_unlock") else "failed")
        transport = _seen["connected"].get(serial) or s.get("transport")
        out.append({
            "serial": serial, "name": s.get("name", serial), "nickname": s.get("nickname", ""), "result": result,
            "running": running, "progress": s.get("progress", 0) if running else 0,
            "connected": serial in _seen["connected"],
            "transport": {"usb": "USB", "wifi": "Wi-Fi"}.get(transport),
            "last_success": s.get("last_success"), "last_attempt": s.get("last_attempt"),
            "size_bytes": s.get("size_bytes", 0), "duration_seconds": s.get("last_duration"),
            "message": s.get("last_message", "")})
    return out


def ha_status():
    """One flat entry per phone for Home Assistant's REST sensor: /api/status is keyed by serial,
    which a sensor template can't walk without knowing it."""
    out = []
    for d in devices():
        d["last_backup"], d["last_attempt"] = iso(d.pop("last_success")), iso(d["last_attempt"])
        out.append(d)
    return out


def fmt_ts(ts):
    return dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M") if ts else "never"


def status_page():
    return page.render(devices(), _seen["unauthorized"], version=__version__, interval=INTERVAL_HOURS, backups=BACKUPS,
                       alert_summary=alerts.summary() or "", last_test=alerts.last_test, stale_hours=alerts.STALE_HOURS,
                       enabled=alerts.events(), note=_notes["wifi"], rules=schedule_rules())


def enable_wifi(serial):
    """`adb tcpip 5555` for a device on USB, and the address to put in ADB_TARGETS. Returns a line for the page.

    Android forgets this on every reboot, so it is a button, not a one-off setup command.
    """
    a = _seen["ready"].get(serial)
    if not a or transport_of(a) != "usb":
        return "Plug the device in over USB first."
    if not _lock.acquire(blocking=False):  # tcpip restarts adb on the device, which would cut a backup off
        return "A backup is running. Try again when it has finished."
    try:
        found = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", shell(a, "ip -f inet addr show wlan0", timeout=20).stdout)
        if not found:
            return "The device isn't on Wi-Fi, so it has no address to back up from."
        if adb("-s", a, "tcpip", "5555", timeout=30).returncode != 0:
            return "The device refused to switch. Check that USB debugging is still on."
    except subprocess.TimeoutExpired:
        return "The device didn't answer. Unlock it and try again."
    finally:
        _lock.release()
    target = f"{found.group(1)}:5555"
    return (f"Done. {target} is already in ADB_TARGETS, so it can be unplugged." if target in TARGETS else
            f"Done. Add {target} to ADB_TARGETS in the container settings, then it can be unplugged.")


def set_nickname(serial, text):
    """A display name only: the backup folder stays <model>-<serial>, so renaming can't orphan a backup."""
    if serial not in load_state() and serial not in _seen["connected"]:
        return False
    update_state(serial, nickname=" ".join(text.split())[:40])
    return True


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="text/html; charset=utf-8"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path.startswith("/metrics"):
            self._send(200, metrics(), "text/plain; version=0.0.4")
        elif self.path.startswith("/setup-guide.txt"):
            self._send(200, page.guide_text(), "text/plain; charset=utf-8")
        elif self.path.startswith("/api/ha"):
            self._send(200, json.dumps(ha_status(), indent=1), "application/json")
        elif self.path.startswith("/api/status"):
            self._send(200, json.dumps(load_state(), indent=1), "application/json")
        elif self.path == "/":
            self._send(200, status_page())
        else:
            self._send(404, "not found", "text/plain")

    def _form(self):
        length = min(int(self.headers.get("Content-Length") or 0), 2000)
        return urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8", "replace"))

    def do_POST(self):
        if self.path.startswith("/api/notify-test"):
            alerts.test()  # in the request, so the page it redirects to shows the result
        elif self.path.startswith("/api/settings"):
            alerts.save_events(self._form())  # a ticked box is a field; an unticked one is absent
        elif self.path.startswith("/api/wifi"):
            _notes["wifi"] = enable_wifi(urllib.parse.unquote(self.path.partition("serial=")[2]))
        elif self.path.startswith("/api/nickname"):
            form = self._form()
            serial = urllib.parse.unquote(self.path.partition("serial=")[2])
            if not set_nickname(serial, form.get("nickname", [""])[0]):
                return self._send(404, "unknown device", "text/plain")
        elif not self.path.startswith("/api/backup"):
            return self._send(404, "not found", "text/plain")
        else:
            serial = urllib.parse.unquote(self.path.partition("serial=")[2]) or None
            if _lock.locked():
                return self._send(409, "a backup is already running", "text/plain")
            threading.Thread(target=run_now, args=([serial] if serial else None,), daemon=True).start()
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, *_):
        pass


# --- commands ------------------------------------------------------------------------

def cmd_serve(_args):
    log(f"android-backup {__version__}: every {INTERVAL_HOURS:g} h; targets {TARGETS or 'USB only'}")
    with _state_lock:  # a restart mid-backup must not leave a device showing as running
        st = load_state()
        for s in st.values():
            s["running"] = False
        if st:
            STATE_FILE.write_text(json.dumps(st, indent=1, sort_keys=True))
    alerts.describe()
    adb("start-server", timeout=30)
    threading.Thread(target=poller, daemon=True).start()
    threading.Thread(target=stale_watch, daemon=True).start()
    threading.Thread(target=watcher, daemon=True).start()
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()


def cmd_backup(args):
    connect_targets()
    results = run_now(args.serial or None)
    if not results:
        print("no authorised device connected")
    return 0 if results and all(results.values()) else 1


def cmd_status(_args):
    for serial, s in sorted(load_state().items()):
        print(f"{s.get('name', serial)}: rc={s.get('last_rc')} last_success={fmt_ts(s.get('last_success'))} "
              f"size={s.get('size_bytes', 0) / 1e9:.1f}GB via {s.get('transport', '-')}: {s.get('last_message', '')}")
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "adb":
        return subprocess.run(["adb", *sys.argv[2:]]).returncode
    p = argparse.ArgumentParser(prog="android-backup", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("serve")
    b = sub.add_parser("backup")
    b.add_argument("serial", nargs="*")
    sub.add_parser("status")
    args = p.parse_args()
    return {"serve": cmd_serve, "backup": cmd_backup, "status": cmd_status, None: cmd_serve}[args.cmd](args) or 0


if __name__ == "__main__":
    sys.exit(main())
