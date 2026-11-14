# DSH Phone Deployment Fixes

## Recovery Status (2026-11-14)

Recovered from `04-phone-device/fuxi/Projects/dsh-phone-fixes` in the local recovery
collection. Scripts and installer are preserved byte-for-byte; the runtime test
fixture additionally mocks the phone user/UID so it can run on another host.
Original logs and device audit output are excluded. This directory is an isolated source
candidate, not part of the standard package or `fuxi-dsh-deploy.sh`.
Statements below describe the recovered deployment, not a new device acceptance.

The mocked runtime suite can be run without installing anything:

```sh
cd packaging/experiments/dsh-phone-fixes
python3 -m unittest discover -s tests -p test_runtime.py -k RuntimeTests -v
```

The full suite also binds localhost sockets. Run it on an unrestricted host before
integration; browser checks require the installed phone environment and DSH source.
Do not execute `install.sh` as a verification step: it writes user/system helpers.
Before promotion, review the hardcoded `fuxi` user and UID 1000, the `next` update
channel, the DSH RPC contract, and compatibility with the maintained deployment.
Power loss/SIGKILL recovery and current on-device behavior remain unverified.

On 2026-11-14, all 24 mocked runtime tests passed on the recovery host. The nine
HTTP tests and phone browser smoke were not run because socket access/device
access was unavailable. This is partial validation, not promotion approval.

Local deployment fixes for Xiaomi 13 running Ubuntu/Phosh. The upstream DSH checkout is at `/home/fuxi/Projects/deepseek-harness`, release `0.2.2-rc.1`, commit `fcc0ad46`. This project maintains the phone's service helpers; it does not fork or replace DSH's application packages.

## Fixed behavior

- The updater restores both the package directory and CLI symlink after partial npm installation, startup failure, presentation-fix failure, failed authentication/API checks, or a handled termination signal. Backups use private, unpredictable directories beside the installation; failed recovery retains its files and reports failure.
- Health checks perform the launch-token/cookie exchange, verify the injected application page, and call the read-only Session list RPC. HTTP 401/500, an unrelated HTTP 200 page, expired tokens, and failed RPC replies do not count as healthy.
- Automatic updates continue to follow npm `next`. Running Agents defer an update; closed application windows stay closed. An originally stopped server is started for verification and then stopped again.
- Idle checks retain the server while a window, client connection, or running Agent exists. Invalid timestamps, future timestamps, invalid numeric settings, unavailable connection monitoring, and unavailable RPC activity evidence cannot trigger a false idle shutdown. The netstat fallback recognizes complete local addresses.
- Launch tokens are stored with mode `0600` in a mode `0700` state directory. URL publication is atomic; token query values are redacted on every launcher log line.
- Window startup waits for authenticated readiness and a live Wayland socket. Clicking the desktop icon again reuses the existing window. Legacy update entrypoints delegate to the maintained updater instead of deleting the installation and copying a hardcoded old backup.
- Post-update fixes no longer overwrite the idle checker, remove profile entries by name prefix, or create backups on every service startup. Unchanged desktop entries and icons are not rewritten.

## Install and verify

```sh
./install.sh
python3 -m unittest discover -s tests -v
shellcheck -S warning bin/dsh-auto-update bin/dsh-web-launcher bin/dsh-web-idle-check bin/dsh-post-update-fixes bin/dsh-app-window-run bin/dsh-web-open bin/dsh-apply-update bin/dsh-apply-tweaks bin/dsh-restart-window install.sh
~/.local/bin/dsh-web-health
node tests/browser-smoke.mjs
```

The installer updates `~/.local/bin/dsh-*`, `/usr/local/bin/dsh-web-open`, and the fallback runner symlink. It reapplies desktop/icon fixes but does not restart services. Python failure tests mock npm, sudo, and systemd in a temporary installation; they never upgrade the real npm package or alter real chat data.

The browser smoke uses Playwright from the upstream checkout and the installed Chromium, reads the current token privately, checks responsive layouts, Chinese draft input, sidebar navigation, and live system-theme changes, and writes screenshots into `~/.local/state/dsh-web/audit-screenshots`. It sends no model prompt.

Deployment settings are `DSH_WEB_STATE`, `DSH_WEB_PORT` (default `3080`), `DSH_WEB_IDLE_MIN` (default `120`), `DSH_UPDATE_CHANNEL` (default `next`), and `DSH_INSTALL_PREFIX` (default `/usr/local`). Configure shared settings consistently for each user service. The helper currently validates DSH's `session/list` RPC and boot-injection names; an upstream protocol change requires a matching helper update.

## Recovery

Original phone helpers were preserved in `~/.local/state/dsh-web/audit-backup-20261108/`. No credentials or Session files were changed. The service's existing `ExecStartPre=dsh-post-update-fixes` remains effective across npm updates. Logs are in `~/.local/state/dsh-web/`; `url.txt` contains a credential and must not be printed in logs or reports.

The automatic update rollback handles ordinary command failures and catchable termination signals. A power loss or SIGKILL cannot run a shell exit trap; retained `.dsh-backup.*` directories under the npm prefix are the recovery source in that situation.
