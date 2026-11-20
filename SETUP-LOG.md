# Setup log

Every command run while building this fork, in order. Not part of the image
(the Dockerfile copies only `android_backup/` and the `android-backup` wrapper).
`<scratch>` is a throwaway temp folder outside the repo. No secrets appear here.

## 2026-11-20: Phase 1 (read the code, check Apprise)

| # | Command | Where | Result |
|---|---------|-------|--------|
| 1 | `ls -la . && git --version && python --version` | repo folder | empty folder; git 2.55.0, Python 3.12.10 |
| 2 | `git clone https://github.com/louij2/android-backup .` | repo folder | cloned upstream at `ab1b48f` (1.0.5) |
| 3 | `python -m unittest discover -s tests` | repo | 17 run, 16 pass, 1 error: `test_light_mode` opens `/dev/null`, which does not exist on Windows |
| 4 | `wc -l android_backup/* tests/* unraid/* Dockerfile deploy.sh android-backup .gitignore` | repo | file sizes |
| 5 | `cat Dockerfile deploy.sh unraid/android-backup.xml LICENSE .gitignore android-backup README.md` | repo | read only |
| 6 | `docker --version; docker info; wsl --status` | host | Docker 29.7.2 installed, daemon not running |
| 7 | `python -m venv apprise-venv` | `<scratch>` | throwaway venv |
| 8 | `apprise-venv/Scripts/python -m pip install apprise==2.2.0` | `<scratch>` | installed with 11 dependencies |
| 9 | `pip list; pip show apprise; du -sm site-packages` | `<scratch>` | BSD-2-Clause; about 23 MB without pip |
| 10 | `apprise-venv/Scripts/python probe.py` | `<scratch>` | offline check of URL parsing and tag matching with placeholder URLs; nothing sent |
| 11 | `grep -n "notify_type\|priority" .../apprise/plugins/ntfy.py` | `<scratch>` | ntfy priority comes only from the URL |

Apprise's documentation pages were also read (GitHub wiki, appriseit.com, PyPI, and the
`home_assistant.py` plugin source). Files were read with the editor's read and search tools.

## 2026-11-20: Phase 2 (Apprise notifications)

Files were edited with the editor's tools and one Python patch script (exact-match replacements
in `android_backup/__main__.py`, `android_backup/alerts.py` and `tests/test_light_mode.py`).

| # | Command | Where | Result |
|---|---------|-------|--------|
| 12 | `grep -n "notify\|WEBHOOK\|NOTIFY_SUCCESS\|urllib.request\|/dev/null" tests/*.py android_backup/__main__.py` | repo | found what the old notify code touched |
| 13 | `apprise-venv/Scripts/python probe2.py` | `<scratch>` | offline: Apprise's "host" is the webhook ID for Discord and the topic for ntfy.sh, so logs show a host only when it is a hostname |
| 14 | `python -m unittest discover -s tests` | repo | 17 upstream tests pass after the wiring change |
| 15 | `python -m unittest discover -s tests` | repo | 49 tests pass (17 upstream, 32 new in `tests/test_alerts.py`) |
| 16 | `PYTHONPATH=<repo> apprise-venv/Scripts/python smoke.py <scratch>/smoke-config` | `<scratch>` | real Apprise 2.2.0 against a listener on 127.0.0.1 only; found `AppriseConfig.servers()` is `services()` in 2.x |
| 17 | `sed -i` rename `servers()` to `services()` in `android_backup/alerts.py` and `tests/test_alerts.py` | repo | fixed |
| 18 | `PYTHONPATH=<repo> apprise-venv/Scripts/python smoke.py <scratch>/smoke-config` | `<scratch>` | passes: tags route, ntfy gets `X-Priority: high` on a failure, config file loads, no placeholder secret in the output |
| 19 | `python -W ignore -m unittest discover -s tests` | repo | 49 tests pass |
| 20 | `git status --short; git diff --stat` | repo | review of the change; nothing committed |

## 2026-11-20: Phases 3 and 4 (Home Assistant endpoint, template, Dockerfile, docs)

Files were edited with the editor's tools and two Python patch scripts (exact-match replacements
in `unraid/android-backup.xml` and `README.md`). Nothing was built, pushed or committed, and
neither Home Assistant nor the server was contacted.

| # | Command | Where | Result |
|---|---------|-------|--------|
| 21 | `python -W ignore -m unittest discover -s tests` | repo | 54 tests pass (5 new in `tests/test_ha.py`) |
| 22 | `git branch --show-current` | repo | `main` |
| 23 | `mkdir -p contrib homeassistant; git mv deploy.sh contrib/deploy-tower.sh` | repo | upstream's deploy script kept under `contrib/` |
| 24 | Python patch of `unraid/android-backup.xml`, then `xml.dom.minidom.parseString` | repo | template points at the fork; XML parses; 18 settings |
| 25 | `bash -n deploy.sh` | repo | new `deploy.sh` has valid syntax; it was not run |
| 26 | `python <scratch>/patch_readme.py .` | repo | README sections added |
| 27 | `apprise-venv/Scripts/python -c "import yaml; yaml.safe_load(open('homeassistant/android-backup.yaml'))"` | repo | the Home Assistant YAML parses; not loaded into Home Assistant |
| 28 | `python -W ignore -m unittest discover -s tests` | repo | 54 tests pass |
| 29 | `git status --short` | repo | review of the change; nothing committed |

## 2026-11-20: Phase 5 step 1 (build the image)

`<img>` is `ghcr.io/iotemylabs/android-backup`. `t-config` and `t-backups` are throwaway folders in `<scratch>`.

| # | Command | Where | Result |
|---|---------|-------|--------|
| 30 | `docker info`; read which registries have a stored login in `~/.docker/config.json` (names only) | host | daemon not running; no stored login for any registry |
| 31 | `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"`, then `docker info` until it answered | host | engine 29.7.2 up |
| 32 | `docker build -t <img>:latest -t <img>:1.0.5-apprise.1 .` | repo | built |
| 33 | `docker image ls`; `docker pull -q python:3.13-slim` | host | image 245 MB; base `python:3.13-slim` 178 MB |
| 34 | `docker run --rm --entrypoint sh <img> -c 'adb version; python -c "import apprise ..."; du -sh site-packages; ls /app'` | host | adb 1.0.41, Apprise 2.2.0, site-packages 29 MB, only `android_backup/` in `/app` |
| 35 | `docker run --rm --entrypoint sh -v <repo>\tests:/app/tests:ro -w /app <img> -c 'python -m unittest discover -s tests'` | host | 54 tests pass on Linux |
| 36 | `docker run -d --name ab-smoke -p 127.0.0.1:19872:9872 -v t-config:/config -v t-backups:/backups -e TZ=America/New_York -e STALE_HOURS= -e APPRISE_URLS= <img>` | host | failed: `exec android-backup failed`, the wrapper had CRLF line endings from the Windows checkout |
| 37 | `git config core.autocrlf; file android-backup contrib/deploy-tower.sh deploy.sh android_backup/__main__.py` | repo | confirmed CRLF in the wrapper |
| 38 | wrote `.gitattributes` (LF for the wrapper, `*.sh`, `*.py`); `rm android-backup; git checkout -- android-backup` | repo | wrapper now LF |
| 39 | `docker build -q -t <img>:latest -t <img>:1.0.5-apprise.1 .` | repo | rebuilt |
| 40 | repeat of 35 | host | 54 tests pass on Linux |
| 41 | repeat of 36, then `docker logs ab-smoke`, `curl` of `/api/ha`, `/`, `/metrics`, `curl -X POST /api/notify-test`, `docker rm -f ab-smoke` | host | starts with blank settings; `/api/ha` returns `[]`; status page shows the test button; test endpoint returns 303; container removed |

Not pushed: this machine has no login for `ghcr.io`.

## 2026-11-20: preview of the status page over NetBird (development PC, not the server)

| # | Command | Where | Result |
|---|---------|-------|--------|
| 42 | `Get-NetIPAddress -AddressFamily IPv4` filtered to the NetBird interface | host | found the PC's NetBird address (`<netbird-ip>`) |
| 43 | `docker run -d --name android-backup-preview -p <netbird-ip>:9872:9872 -v t-config:/config -v t-backups:/backups -e TZ=America/New_York <img>:1.0.5-apprise.1` | host | running; published on the NetBird address only, with no USB access and no alert targets |
| 44 | `curl http://<netbird-ip>:9872/` and `curl http://127.0.0.1:9872/` | host | 200 on the NetBird address; nothing on localhost |

Stop it with `docker rm -f android-backup-preview`.

## 2026-11-20: status page upgrade, nicknames, alert settings, started alert

Files were edited with the editor's tools and two Python patch scripts in `<scratch>`
(`patch_page.py`, `patch_settings.py`: exact-match replacements). `<netbird-ip>` is the
development PC's NetBird address. The preview uses made-up devices in throwaway folders.

| # | Command | Where | Result |
|---|---------|-------|--------|
| 45 | `Get-NetTCPConnection`, `Get-NetConnectionProfile`, `Get-NetFirewallProfile`, `netbird status -d`; NetBird policy list | host | read only, to diagnose a timeout from another NetBird peer: listener and tunnel fine, firewall rules unreadable without admin; nothing changed |
| 46 | wrote sample `state.json` into `t-config`; `curl http://<netbird-ip>:9872/ -o status-page-sample.html` | `<scratch>` | saved copy of upstream's page for review |
| 47 | `python <scratch>/patch_page.py .` | repo | new `android_backup/page.py`, nicknames |
| 48 | `python -W ignore -m unittest discover -s tests` | repo | 70 tests pass |
| 49 | `python <scratch>/patch_settings.py .` | repo | alert settings on the page, `started` alert |
| 50 | `python -W ignore -m unittest discover -s tests`; YAML parse check of `homeassistant/android-backup.yaml` | repo | 76 tests pass; YAML parses |
| 51 | `docker build -q -t <img>:latest -t <img>:1.0.5-apprise.1 .` | repo | rebuilt |
| 52 | `docker run --rm --entrypoint sh -v <repo>	ests:/app/tests:ro -w /app <img>:latest -c 'python -m unittest discover -s tests'` | host | 76 tests pass on Linux |
| 53 | `docker rm -f android-backup-preview`; `docker run -d --name android-backup-preview -p <netbird-ip>:9872:9872 -v t-config:/config -v t-backups:/backups -e TZ=America/New_York <img>:latest` | host | preview restarted on the new image |
| 54 | rewrote sample `state.json`; `curl http://<netbird-ip>:9872/ -o status-page-new.html`; opened the page in the built-in browser | `<scratch>` | new page renders with all five states |
| 55 | `date +%s` on host and in the container | host | clocks agree |

## 2026-11-20: add-a-device guide

| # | Command | Where | Result |
|---|---------|-------|--------|
| 56 | `python <scratch>/patch_guide.py .` | repo | guide panel, `/setup-guide.txt`, `POST /api/wifi` |
| 57 | `python -W ignore -m unittest discover -s tests` | repo | 82 tests pass |
| 58 | `docker build -q -t <img>:latest -t <img>:1.0.5-apprise.1 .` | repo | rebuilt |
| 59 | `docker run --rm --entrypoint sh -v <repo>	ests:/app/tests:ro -w /app <img>:latest -c 'python -m unittest discover -s tests'` | host | 82 tests pass on Linux |
| 60 | `docker rm -f android-backup-preview`; `docker run -d --name android-backup-preview -p <netbird-ip>:9872:9872 -v t-config:/config -v t-backups:/backups -e TZ=America/New_York <img>:latest` | host | preview restarted on the new image |
| 61 | refreshed sample timestamps in `t-config/state.json`; `curl` of `/` and `/setup-guide.txt`; opened the page in the built-in browser | `<scratch>` | guide renders and stays open across the live refresh; checklist downloads |

The "Switch to Wi-Fi" button was tested with adb mocked only. No phone has been connected.

## 2026-11-20: older versions, charging and window rules, calendar export

| # | Command | Where | Result |
|---|---------|-------|--------|
| 62 | `python <scratch>/patch_backup.py .` | repo | versions, charging and window rules, calendar export |
| 63 | `python -W ignore -m unittest discover -s tests` | repo | 1 upstream test failed: `pull()` had gained an argument |
| 64 | `python <scratch>/patch_backup2.py .` | repo | `pull()` restored to upstream's; the old copy is kept with a hard link before the pull |
| 65 | `python -W ignore -m unittest discover -s tests` | repo | 100 tests pass (18 new in `tests/test_backup_extras.py`) |
| 66 | `python <scratch>/patch_docs3.py .` | repo | template, README, CHANGELOG and this log updated; template XML parses |
| 67 | `docker build -q -t <img>:latest -t <img>:1.0.5-apprise.1 .` | repo | rebuilt |
| 68 | `docker run --rm --entrypoint sh -v <repo>\tests:/app/tests:ro -w /app <img>:latest -c 'python -m unittest discover -s tests'` | host | 100 tests pass on Linux |
| 69 | `docker rm -f android-backup-preview`; `docker run -d --name android-backup-preview ...` as in 60 | host | preview restarted on the new image |

The calendar export and the charging check were tested with adb mocked only. No phone has been connected.

## 2026-11-20: GitHub fork and first push

| # | Command | Where | Result |
|---|---------|-------|--------|
| 70 | `gh repo fork louij2/android-backup --clone=false` | repo | created `iotemylabs/android-backup`, a public fork |
| 71 | `git remote rename origin upstream`; `git remote add origin https://github.com/iotemylabs/android-backup.git` | repo | `origin` is the fork, `upstream` is louij2 |
| 72 | `grep -rnIE "<user name, tunnel addresses, host names, email>" .` | repo | nothing private in the files; only placeholder addresses |
| 73 | `git config user.name iotemylabs`; `git config user.email <GitHub noreply address>` | repo | commit identity for this repo only |
| 74 | `sed -i ... LICENSE` | repo | added the fork copyright line under upstream's |
| 75 | `git add -A`; `git commit`; `git push -u origin main` | repo | one commit on top of upstream 1.0.5, pushed to the fork |
