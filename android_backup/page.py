"""The status page: one self-contained HTML page, with no files to serve and nothing to install.

Kept apart from __main__ so the page can change without touching the backup code.
Every value from a device or a person is escaped: nicknames and messages are free text.
"""
import datetime as dt
import html
import time
import urllib.parse

CSS = """
:root{--bg:#f4f6fb;--card:#fff;--ink:#1c2333;--soft:#667089;--line:#e3e7f0;--accent:#5b5bf0;--accent2:#12b3a8;
--ok:#12925a;--okbg:#dff7ea;--bad:#c62f3d;--badbg:#fde4e6;--warn:#a86400;--warnbg:#fff0cf;--run:#2563eb;--runbg:#dfeaff;
--idle:#667089;--idlebg:#eceff5}
@media (prefers-color-scheme:dark){:root{--bg:#0e111a;--card:#171b28;--ink:#e8ebf4;--soft:#97a0b8;--line:#272d40;
--ok:#4ade80;--okbg:#113222;--bad:#ff7a85;--badbg:#3d161b;--warn:#fbbf24;--warnbg:#3a2c0b;--run:#7aa7ff;--runbg:#14264d;
--idle:#97a0b8;--idlebg:#222839}}
*{box-sizing:border-box}
body{font:15px/1.45 system-ui,sans-serif;margin:0;color:var(--ink);background:var(--bg)}
.hero{padding:22px 16px 46px;color:#fff;background:linear-gradient(120deg,var(--accent),#8b5cf6,var(--accent2),var(--accent));
background-size:300% 100%;animation:drift 18s linear infinite}
.hero h1{margin:0;font-size:22px;letter-spacing:.2px}.hero p{margin:4px 0 0;opacity:.9;font-size:13px}
.hero code{background:rgba(255,255,255,.18);padding:1px 6px;border-radius:6px}
main{max-width:1100px;margin:-30px auto 40px;padding:0 16px}
.bar{display:flex;flex-wrap:wrap;gap:10px;align-items:center;background:var(--card);border:1px solid var(--line);
border-radius:14px;padding:10px 14px;margin-bottom:14px;box-shadow:0 6px 20px rgba(20,30,60,.08)}
.bar small{color:var(--soft)}.bar form{margin:0 0 0 auto}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
.card{background:var(--card);border:1px solid var(--line);border-top:4px solid var(--idle);border-radius:14px;padding:14px;
box-shadow:0 6px 20px rgba(20,30,60,.08);transition:transform .18s ease,box-shadow .18s ease}
.card:hover{transform:translateY(-3px);box-shadow:0 12px 28px rgba(20,30,60,.16)}
.boot .card{animation:rise .45s ease both}.boot .card:nth-child(2){animation-delay:.07s}.boot .card:nth-child(3){animation-delay:.14s}
.boot .card:nth-child(n+4){animation-delay:.21s}
.card.ok{border-top-color:var(--ok)}.card.failed{border-top-color:var(--bad)}.card.needs_unlock{border-top-color:var(--warn)}
.card.running{border-top-color:var(--run)}
.card header{display:flex;align-items:center;gap:8px}.card h2{margin:0;font-size:17px;flex:1;overflow-wrap:anywhere}
.sub{margin:2px 0 10px 18px;color:var(--soft);font-size:12px;overflow-wrap:anywhere}
.dot{width:10px;height:10px;border-radius:50%;background:var(--idle);flex:none}
.dot.on{background:var(--ok);animation:pulse 2s ease-out infinite}
.pill{font-size:12px;font-weight:600;padding:3px 10px;border-radius:999px;white-space:nowrap;color:var(--idle);background:var(--idlebg)}
.pill.ok{color:var(--ok);background:var(--okbg)}.pill.failed{color:var(--bad);background:var(--badbg)}
.pill.needs_unlock,.pill.stale{color:var(--warn);background:var(--warnbg)}.pill.running{color:var(--run);background:var(--runbg)}
.pill.stale{margin-left:4px}
.track{height:10px;border-radius:999px;background:var(--runbg);overflow:hidden;margin:0 0 10px}
.fill{height:100%;border-radius:999px;transition:width .6s ease;background-size:28px 28px;animation:stripes 1s linear infinite;
background-image:linear-gradient(45deg,var(--run) 25%,#7c9cff 25%,#7c9cff 50%,var(--run) 50%,var(--run) 75%,#7c9cff 75%)}
dl{display:grid;grid-template-columns:1fr 1fr;gap:8px 12px;margin:0 0 10px}dt{font-size:11px;text-transform:uppercase;
letter-spacing:.5px;color:var(--soft)}dd{margin:0;font-weight:600}
.msg{margin:0 0 12px;font-size:12px;color:var(--soft);overflow-wrap:anywhere}
.card footer{display:flex;gap:8px;align-items:flex-start;flex-wrap:wrap}.card footer form{margin:0}
button{font:inherit;font-weight:600;border:0;border-radius:10px;padding:7px 14px;cursor:pointer;color:#fff;
background:linear-gradient(135deg,var(--accent),#8b5cf6);transition:transform .1s ease,filter .15s ease}
button:hover{filter:brightness(1.1)}button:active{transform:scale(.96)}
button.quiet{color:var(--ink);background:var(--idlebg)}
details{font-size:13px}summary{cursor:pointer;list-style:none;padding:7px 12px;border-radius:10px;background:var(--idlebg);font-weight:600}
summary::-webkit-details-marker{display:none}details[open] summary{margin-bottom:8px}
details form{display:flex;gap:6px}input{font:inherit;padding:6px 10px;border-radius:10px;border:1px solid var(--line);
background:var(--bg);color:var(--ink);min-width:0;width:170px}
.prefs{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:6px;margin-bottom:14px}
.prefs summary{background:none}.prefs form{display:grid;gap:8px;padding:0 12px 10px}
.prefs label{display:flex;gap:8px;align-items:center}.prefs input{width:auto;accent-color:var(--accent)}
.prefs button{justify-self:start}.prefs small{color:var(--soft)}
.guide ol{margin:6px 12px 10px;padding-left:22px;display:grid;gap:10px}.guide li::marker{color:var(--accent);font-weight:700}
.guide li b{display:block}.guide li span{color:var(--soft);font-size:13px}
.guide .live{margin:0 12px 10px;padding:8px 12px;border-radius:10px;background:var(--runbg);color:var(--run);font-weight:600}
.guide .live.good{background:var(--okbg);color:var(--ok)}.guide .live.act{background:var(--warnbg);color:var(--warn);
animation:nudge 1.6s ease-in-out infinite}
.guide .row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:0 12px 10px}.guide .row form{margin:0}
.guide a{color:var(--accent);font-weight:600}
@keyframes nudge{50%{transform:translateX(4px)}}
.wait{background:var(--warnbg);color:var(--warn);border-radius:12px;padding:10px 14px;margin:0 0 12px;animation:rise .4s ease both}
.empty{background:var(--card);border:1px dashed var(--line);border-radius:14px;padding:26px;text-align:center;color:var(--soft)}
@keyframes drift{to{background-position:300% 0}}
@keyframes rise{from{opacity:0;transform:translateY(10px)}}
@keyframes stripes{to{background-position:28px 0}}
@keyframes pulse{0%{box-shadow:0 0 0 0 rgba(18,146,90,.55)}100%{box-shadow:0 0 0 9px rgba(18,146,90,0)}}
@media (prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}
"""

# Refresh the live part without a reload, but not while someone is typing a nickname.
JS = """
setTimeout(()=>document.body.classList.remove('boot'),1500);
setInterval(async()=>{
  if(document.querySelector('details[open]:not(.guide), input:focus'))return;
  try{
    const r=await fetch('/',{cache:'no-store'});
    const next=new DOMParser().parseFromString(await r.text(),'text/html').getElementById('live');
    const live=document.getElementById('live');
    const open=live.querySelector('details.guide')?.open;
    if(next&&next.innerHTML!==live.innerHTML){live.innerHTML=next.innerHTML;
      if(open)live.querySelector('details.guide').open=true;}
  }catch(e){}
},5000);
"""

EVENTS = (("started", "Backup started (a device connected and is backing up)"),
          ("success", "Backup succeeded"),
          ("failed", "Backup failed"),
          ("needs_unlock", "Phone needs unlocking after a reboot"),
          ("unauthorized", "Phone is waiting for Allow"),
          ("wifi_lost", "Wi-Fi phone can't be reached"),
          ("stale", "No good backup for a long time"))
# (title, what to do). Written for any Android phone; maker-specific notes say so.
GUIDE = (
    ("Turn on Developer options",
     "On the phone, open Settings > About phone (Samsung: then Software information) and tap Build number seven times."),
    ("Turn on USB debugging",
     "In Settings > Developer options, turn on USB debugging."),
    ("Stop the phone forgetting the server",
     "Also in Developer options, turn on Disable adb authorization timeout, if it's there. "
     "Without it Android asks again after 7 days without a connection."),
    ("Plug it in and tap Allow",
     "Use a data cable into the server. On the phone, tick Always allow from this computer and tap Allow. "
     "Samsung: if no prompt appears, look at Settings > Security and privacy > Auto Blocker, which can block USB commands."),
    ("Optional: back up over Wi-Fi",
     "While it is still plugged in, press Switch to Wi-Fi below. Add the address it shows to ADB_TARGETS in the "
     "container settings, and give the phone a fixed address on your router. "
     "Android forgets this when the phone reboots: plug it in and press the button again."),
    ("Name it and run the first backup",
     "Use Rename on the device's card, then Back up now. The first backup copies everything and can take hours."),
)
PILL = {"ok": "Backed up", "failed": "Failed", "needs_unlock": "Unlock phone", "never": "No backup yet"}
LINK = {"USB": "USB", "Wi-Fi": "Wi-Fi"}


def ago(ts, now):
    if not ts:
        return "never"
    minutes = max(0, now - ts) / 60
    if minutes < 1:
        return "just now"
    if minutes < 90:
        return f"{minutes:.0f} min ago"
    if minutes < 48 * 60:
        return f"{minutes / 60:.0f} h ago"
    return f"{minutes / 1440:.0f} days ago"


def took(seconds):
    if seconds is None:
        return "-"
    return f"{seconds // 60:.0f} min {seconds % 60:.0f} s" if seconds >= 60 else f"{seconds:.0f} s"


def card(d, now, stale_hours):
    e = html.escape
    q = urllib.parse.quote(d["serial"], safe="")
    result, last = d["result"], d["last_success"]
    pill = f"Running {d['progress']}%" if result == "running" else PILL[result]
    stale = stale_hours and last and result != "running" and now - last >= stale_hours * 3600
    when = dt.datetime.fromtimestamp(last).strftime("%Y-%m-%d %H:%M") if last else "never"
    sub = f"{e(d['name'])} &middot; {e(d['serial'])}" if d["nickname"] and d["name"] != d["serial"] else e(d["serial"])
    progress = (f"<div class=track><div class=fill style='width:{max(2, min(100, int(d['progress'])))}%'></div></div>"
                if result == "running" else "")
    return f"""<article class="card {result}">
<header><span class="dot{' on' if d['connected'] else ''}" title="{'connected' if d['connected'] else 'away'}"></span>
<h2>{e(d['nickname'] or d['name'])}</h2><span class="pill {result}">{pill}</span>{'<span class="pill stale">Stale</span>' if stale else ''}</header>
<p class=sub>{sub}</p>{progress}
<dl><div><dt>Last backup</dt><dd title="{when}">{ago(last, now)}</dd></div>
<div><dt>Link</dt><dd>{LINK.get(d['transport'], '-') if d['connected'] else 'away'}</dd></div>
<div><dt>Size</dt><dd>{d['size_bytes'] / 1e9:.1f} GB</dd></div>
<div><dt>Took</dt><dd>{took(d['duration_seconds'])}</dd></div></dl>
<p class=msg>{e(d['message'])}</p>
<footer><form method=post action="/api/backup?serial={q}"><button>Back up now</button></form>
<details><summary>Rename</summary><form method=post action="/api/nickname?serial={q}">
<input name=nickname maxlength=40 placeholder="Nickname" value="{e(d['nickname'], quote=True)}" aria-label="Nickname">
<button>Save</button></form></details></footer></article>"""


def guide_text():
    """The same steps as a plain-text checklist, to download and keep next to the phone."""
    lines = ["android-backup: add a device", "============================", ""]
    for n, (title, text) in enumerate(GUIDE, 1):
        lines += [f"[ ] {n}. {title}", f"      {text}", ""]
    return "\n".join(lines)


def guide(devices, waiting, note):
    """The add-a-device steps, with a line that says what the server can see right now."""
    e = html.escape
    usb = [d for d in devices if d["connected"] and d["transport"] == "USB"]
    if waiting:
        live = f"<p class='live act'>👉 {e(', '.join(waiting))} is waiting: tap Allow on the phone (step 4).</p>"
    elif usb:
        live = f"<p class='live good'>✅ {e(', '.join(d['nickname'] or d['name'] for d in usb))} is connected over USB.</p>"
    else:
        live = "<p class=live>🔌 No new device seen yet. This line updates when one is plugged in.</p>"
    steps = "".join(f"<li><b>{e(title)}</b><span>{e(text)}</span></li>" for title, text in GUIDE)
    buttons = "".join(
        f"<form method=post action=\"/api/wifi?serial={urllib.parse.quote(d['serial'], safe='')}\">"
        f"<button>Switch {e(d['nickname'] or d['name'])} to Wi-Fi</button></form>" for d in usb)
    return f"""<details class="prefs guide"{' open' if waiting or note else ''}><summary>➕ Add a device</summary>
{live}<ol>{steps}</ol>
<div class=row>{buttons}<small>{e(note)}</small></div>
<div class=row><a href="/setup-guide.txt" download="android-backup-setup.txt">⬇ Download this checklist</a></div></details>"""


def render(devices, waiting, *, version, interval, backups, alert_summary, last_test, stale_hours, enabled=(), note="", rules=""):
    e = html.escape
    now = time.time()
    banners = "".join(f"<p class=wait>⚠ <b>{e(a)}</b>: tap <b>Allow</b> on the device (tick “Always allow”).</p>"
                      for a in waiting)
    cards = "".join(card(d, now, stale_hours) for d in devices)
    boxes = "".join(f"<label><input type=checkbox name={name}{' checked' if name in enabled else ''}> {e(label)}</label>"
                    for name, label in EVENTS)
    body = f"<div class=grid>{cards}</div>" if cards else (
        "<p class=empty>No devices yet. Turn on USB debugging and plug one in.</p>")
    return f"""<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>android-backup</title><style>{CSS}</style><body class=boot>
<div class=hero><h1>android-backup {e(version)}</h1>
<p>Every {interval:g} h per device, {e(rules) or "whenever it's connected"} &middot; backups in <code>{e(str(backups))}</code></p></div>
<main><div id=live>{banners}
<div class=bar><span>🔔 {e(alert_summary)}</span><small>{e(last_test)}</small>
<form method=post action="/api/notify-test"><button class=quiet>Send test alert</button></form></div>
<details class=prefs><summary>⚙ Alert settings</summary><form method=post action="/api/settings">
<small>Tick the alerts you want. Saving here takes over from the NOTIFY_EVENTS and NOTIFY_SUCCESS variables.</small>
{boxes}<button>Save</button></form></details>
{guide(devices, waiting, note)}
{body}</div></main><script>{JS}</script></body></html>"""
