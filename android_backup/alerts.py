"""Alerts through Apprise (ntfy, Home Assistant, Discord and more), each sent once.

Kept apart from __main__ so the backup code stays close to upstream. Nothing here may
raise into a backup: every entry point logs a problem and carries on.

An Apprise URL carries a token, so it is a secret. It is never logged whole: see where().

Events: started, success, failed, needs_unlock, unauthorized, wifi_lost, stale.
Which ones are sent comes from the variables until someone saves a choice on the status page.
A URL with no tag gets every event. `tag=url` limits it to the events named in the tag.
"""
import datetime as dt
import functools
import json
import os
import re
import threading
import time
from pathlib import Path

CONFIG = Path(os.environ.get("CONFIG_DIR", "/config"))
URLS = os.environ.get("APPRISE_URLS", "").strip()
CONFIG_FILE = os.environ.get("APPRISE_CONFIG", "").strip()
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()  # legacy: becomes one more Apprise target
NOTIFY_SUCCESS = os.environ.get("NOTIFY_SUCCESS", "false").lower() in ("1", "true", "yes")
NOTIFY_EVENTS = os.environ.get("NOTIFY_EVENTS", "").strip()
STALE_HOURS = float(os.environ.get("STALE_HOURS") or 48)  # Unraid passes a blank field as ""
WIFI_LOST_HOURS = float(os.environ.get("WIFI_LOST_HOURS") or 6)

ALL_EVENTS = ("started", "success", "failed", "needs_unlock", "unauthorized", "wifi_lost", "stale")
DEFAULT_EVENTS = ("failed", "needs_unlock", "unauthorized", "wifi_lost", "stale")
EVENT_LEVEL = {"started": "info", "failed": "failure", "success": "success", "needs_unlock": "warning", "unauthorized": "warning",
               "wifi_lost": "warning", "stale": "warning"}
NTFY_PRIORITY = {"failure": "high", "warning": "default", "info": "low", "success": "low"}
RETRY_SECONDS = 900  # an alert no target took is tried again, but not on every 30 s poll
SEEN_EVERY = 600  # how often a reachable Wi-Fi address is written down

SENT_FILE = CONFIG / "alerts.json"  # not state.json: every reader of that file expects one entry per device
SETTINGS_FILE = CONFIG / "settings.json"  # choices saved on the status page
_settings = None
_sent_lock = threading.Lock()
_load_lock = threading.Lock()
_targets = None
last_test = ""

_TAGS = r"[\w-]+(?:,[\w-]+)*"
_SCHEME = r"[A-Za-z][A-Za-z0-9+.-]*://"
# A URL ends at a space or comma only when another URL follows: ntfy's `tags=a,b` has commas of its own.
URL_RE = re.compile(rf"(?:(?P<tags>{_TAGS})=)?(?P<url>{_SCHEME}\S+?)(?=[\s,]+(?:{_TAGS}=)?{_SCHEME}|[\s,]*$)")


def log(msg):
    print(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def never_raise(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # the message could quote a URL, so only the type is logged
            log(f"alerts: {fn.__name__} failed: {type(e).__name__}")
    return wrapper


# --- targets -------------------------------------------------------------------------

def _saved():
    global _settings
    if _settings is None:
        try:
            _settings = json.loads(SETTINGS_FILE.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            _settings = {}
    return _settings


@never_raise
def save_events(chosen):
    """Keep the status page's choice. From then on it wins over NOTIFY_EVENTS and NOTIFY_SUCCESS."""
    global _settings
    _settings = {**_saved(), "events": sorted(e for e in chosen if e in ALL_EVENTS)}
    SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(_settings, indent=1, sort_keys=True))
    tmp.replace(SETTINGS_FILE)


def events():
    """The events that are sent. A Discord-only install keeps upstream's alerts and gains none."""
    if "events" in _saved():
        return set(_saved()["events"])
    if NOTIFY_EVENTS:
        chosen = {e.strip() for e in NOTIFY_EVENTS.split(",") if e.strip()}
    elif URLS or CONFIG_FILE:
        chosen = set(DEFAULT_EVENTS)
    else:
        chosen = {"failed"}
    if NOTIFY_SUCCESS:
        chosen.add("success")
    return chosen


def split_urls(text):
    """[(tags, url)] from a list separated by spaces or commas, each entry `url` or `tag,tag=url`."""
    return [({t for t in (m["tags"] or "").split(",") if t}, m["url"]) for m in URL_RE.finditer(text)]


def where(server):
    """scheme://host, the only part of a target that is safe to log.

    Apprise's "host" is the webhook ID for Discord and the topic for ntfy.sh, so it is
    shown only when it looks like a hostname.
    """
    scheme = server.url(privacy=True).partition("://")[0]
    host = str(getattr(server, "host", "") or "")
    return f"{scheme}://{host if '.' in host or ':' in host or host == 'localhost' else '...'}"


def _target(server_by_level, tags):
    return {"servers": server_by_level, "tags": {str(t) for t in tags}, "where": where(server_by_level["info"])}


def _from_url(apprise, url, tags):
    """One server per level. ntfy takes its priority from the URL only, so each level gets its own
    unless the URL already sets one."""
    if re.match(r"ntfys?://", url, re.I) and not re.search(r"[?&]priority=", url, re.I):
        sep = "&" if "?" in url else "?"
        servers = {lvl: apprise.Apprise.instantiate(f"{url}{sep}priority={p}") for lvl, p in NTFY_PRIORITY.items()}
    else:
        servers = dict.fromkeys(NTFY_PRIORITY, apprise.Apprise.instantiate(url))
    return _target(servers, tags) if all(servers.values()) else None


def _load():
    if not (URLS or CONFIG_FILE or WEBHOOK):
        return []
    try:
        import apprise
    except ImportError:
        log("alerts: the apprise package is not installed, so no alerts will be sent")
        return []
    found = []
    for n, (tags, url) in enumerate(split_urls(URLS), 1):
        target = _from_url(apprise, url, tags)
        if target:
            found.append(target)
        else:
            log(f"alerts: APPRISE_URLS entry {n} ({url.partition('://')[0]}://...) is not a URL Apprise understands")
    if CONFIG_FILE:
        if Path(CONFIG_FILE).is_file():
            cfg = apprise.AppriseConfig()
            cfg.add(CONFIG_FILE)
            found += [_target(dict.fromkeys(NTFY_PRIORITY, s), s.tags) for s in cfg.services()]
        else:
            log(f"alerts: APPRISE_CONFIG {CONFIG_FILE} is not a file")
    if WEBHOOK:
        legacy = _from_url(apprise, WEBHOOK, set())
        if not legacy:
            log("alerts: DISCORD_WEBHOOK_URL is not a Discord webhook URL")
        elif legacy["servers"]["info"].url() not in {t["servers"]["info"].url() for t in found}:
            found.append(legacy)  # skipped when APPRISE_URLS already has it, so nothing is sent twice
    return found


def targets():
    global _targets
    with _load_lock:
        if _targets is None:
            _targets = _load()
        return _targets


@never_raise
def describe():
    found = targets()
    places = ", ".join(t["where"] + (f" [{','.join(sorted(t['tags']))}]" if t["tags"] else "") for t in found)
    log(f"alerts: {len(found)} target(s){': ' + places if places else ''}; events {','.join(sorted(events()))}")


@never_raise
def summary():
    """For the status page: how many targets. No hosts: the page has no login."""
    count = len(targets())
    return f"{count} alert target(s)" if count else "No alert targets set"


# --- sending -------------------------------------------------------------------------

def _send(target, level, title, body):
    try:
        import apprise
        ap = apprise.Apprise()
        ap.add(target["servers"][level])
        ok = bool(ap.notify(title=title, body=body, notify_type=apprise.NotifyType(level)))
    except Exception as e:  # never let an alert failure break a backup
        log(f"alerts: {target['where']} raised {type(e).__name__}")
        ok = False
    return ok


@never_raise
def notify(event, device, title, body):
    """Send one alert. True if a target took it, False if none did, None if there was nowhere to send it."""
    if event not in events():
        return None
    chosen = [t for t in targets() if not t["tags"] or event in t["tags"]]
    if not chosen:
        return None
    results = [_send(t, EVENT_LEVEL[event], title, body) for t in chosen]
    for t, ok in zip(chosen, results):
        if not ok:
            log(f"alerts: {event} for {device} did not reach {t['where']}")
    return any(results)


@never_raise
def test():
    """One message to every target, whatever its tags, so each can be checked from the status page.

    At warning level, so ntfy delivers it the way it will deliver a real alert."""
    global last_test
    found = targets()
    failed = [t["where"] for t in found
              if not _send(t, "warning", "🔔 Android backup: test alert", "If you can read this, this target works.")]
    last_test = f"{dt.datetime.now():%H:%M:%S}: {len(found) - len(failed)} of {len(found)} target(s) took the test alert"
    if failed:
        last_test += f" (failed: {', '.join(failed)})"
    log(f"alerts: {last_test}")
    return last_test


# --- sent once -----------------------------------------------------------------------

def _memory():
    try:
        mem = json.loads(SENT_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        mem = {}
    mem.setdefault("sent", {})
    mem.setdefault("seen", {})
    return mem


def _remember(mem):
    SENT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = SENT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(mem, indent=1, sort_keys=True))
    tmp.replace(SENT_FILE)


@never_raise
def once(event, key, device, title, body):
    """Send an alert that stays true for a while (stale, waiting for Allow) only the first time.

    Remembered on disk, so a restart doesn't send it again. clear() forgets it.
    """
    with _sent_lock:
        mem = _memory()
        last = mem["sent"].get(f"{event}:{key}")
        if last and (last["ok"] or time.time() - last["at"] < RETRY_SECONDS):
            return
        ok = notify(event, device, title, body)
        if ok is not None:
            mem["sent"][f"{event}:{key}"] = {"at": time.time(), "ok": ok}
            _remember(mem)


@never_raise
def clear(event, key):
    with _sent_lock:
        mem = _memory()
        if mem["sent"].pop(f"{event}:{key}", None):
            _remember(mem)


def _last_seen(target, online):
    """When a Wi-Fi address last answered. The clock starts the first time the address is checked."""
    with _sent_lock:
        mem = _memory()
        now = time.time()
        seen = mem["seen"].get(target)
        if seen is None or (online and now - seen >= SEEN_EVERY):
            mem["seen"][target] = seen = now
            _remember(mem)
        return now if online else seen


# --- events --------------------------------------------------------------------------

@never_raise
def backup_started(label, transport, light):
    notify("started", label, f"📲 Android backup started: {label}",
           f"Connected over {'Wi-Fi' if transport == 'wifi' else 'USB'} and backing up"
           + (" (mobile data: light mode)." if light else "."))


@never_raise
def backup_result(serial, label, ok, gone, locked, message):
    """After every backup. Unplugging mid-run isn't a failure: it resumes next time."""
    if ok:
        clear("stale", serial)
        clear("needs_unlock", serial)
        notify("success", label, f"✅ Android backup OK: {label}", message)
    elif locked and "needs_unlock" in events():
        once("needs_unlock", serial, label, f"🔒 Android backup is waiting: {label}",
             "Unlock your phone once to let the backup run.")
    elif not gone:
        notify("failed", label, f"⚠️ Android backup FAILED: {label}", message)


@never_raise
def check_devices(ready, unauthorized, online, wifi_targets):
    """After every poll: phones waiting for Allow, and Wi-Fi addresses that have gone quiet.

    A phone that isn't authorised has no readable serial, so it is known by its adb address.
    """
    for a in unauthorized:
        once("unauthorized", a, a, f"🔐 Android backup: {a} is waiting for Allow",
             "Tap Allow on the phone and tick “Always allow”.")
    for a in set(ready.values()) | set(online):
        clear("unauthorized", a)
    for t in wifi_targets:
        reachable = t in online or t in unauthorized
        quiet = time.time() - _last_seen(t, reachable)
        if reachable:
            clear("wifi_lost", t)
        elif WIFI_LOST_HOURS and quiet >= WIFI_LOST_HOURS * 3600:
            once("wifi_lost", t, t, f"📡 Android backup: {t} is unreachable over Wi-Fi",
                 f"Not seen for {quiet / 3600:.0f} hours. Plug it in over USB and run `adb tcpip 5555` again, "
                 "it probably rebooted.")


@never_raise
def check_stale(state):
    """One alert per stale stretch. A good backup clears it (see backup_result)."""
    for serial, s in state.items():
        last = s.get("last_success")
        if not STALE_HOURS or not last or s.get("running"):
            continue
        age = (time.time() - last) / 3600
        if age >= STALE_HOURS:
            name = s.get("nickname") or s.get("name", serial)
            once("stale", serial, name, f"⏰ Android backup is stale: {name}",
                 f"No good backup for {age:.0f} hours (last: {dt.datetime.fromtimestamp(last):%Y-%m-%d %H:%M}).")
        else:
            clear("stale", serial)
