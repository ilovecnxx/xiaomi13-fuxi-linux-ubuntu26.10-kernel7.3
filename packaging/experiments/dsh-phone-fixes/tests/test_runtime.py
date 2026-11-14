"""Exercise deployment failures against isolated files and mocked system boundaries."""
import fcntl
import http.server
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error

ROOT = Path(__file__).resolve().parents[1]
MOCK = r'''#!/usr/bin/python3
import json, os, pathlib, signal, sys
name = pathlib.Path(sys.argv[0]).name
root = pathlib.Path(os.environ["FIXTURE"])
options = json.loads((root / "options.json").read_text())
state_path = root / "services.json"
state = json.loads(state_path.read_text())
args = sys.argv[1:]
prefix = root / "prefix"
package = prefix / "lib/node_modules/@deepseek-ai/dsh"
if name == "id":
    # The phone launcher selects user fuxi; never exercise host sudo in fixtures.
    print("fuxi" if args == ["-un"] else "1000")
elif name == "sudo":
    if options.get("backup_fail") and args[1:3] == ["cp", "-a"]:
        sys.exit(1)
    if options.get("restore_fail") and args[1] == "mv" and args[-2].endswith("/dsh") and ".dsh-backup." in args[-2]:
        sys.exit(1)
    os.execvp(args[1], args[1:])
elif name == "npm":
    if args[0] == "view":
        print(options.get("latest", "2.0.0"))
        sys.exit(options.get("resolve_status", 0))
    (package / "version").write_text("broken" if options.get("install_fail") else "2.0.0")
    binary = prefix / "bin/dsh"
    binary.unlink(missing_ok=True)
    if options.get("signal"):
        os.kill(os.getppid(), signal.SIGTERM)
        sys.exit(143)
    if options.get("install_fail"):
        sys.exit(1)
    binary.symlink_to(package / "dsh")
elif name == "systemctl":
    args = args[1:]
    command, service = args[0], args[-1]
    with (root / "calls").open("a") as out:
        out.write(command + " " + service + "\n")
    if command == "is-active":
        sys.exit(0 if state.get(service) else 3)
    if command == "stop":
        state[service] = False
    elif command in ("start", "restart"):
        if options.get("start_fail") and (package / "version").read_text() == "2.0.0":
            sys.exit(1)
        state[service] = True
    state_path.write_text(json.dumps(state))
elif name == "dsh-web-health":
    if options.get("busy") and "--idle" in args:
        sys.exit(1)
    sys.exit(1 if options.get("health_fail") and (package / "version").read_text() == "2.0.0" else 0)
elif name == "dsh-post-update-fixes":
    sys.exit(1 if options.get("fixes_fail") else 0)
elif name == "pgrep":
    sys.exit(1)
elif name == "ss":
    print(options.get("connections", ""), end="")
    sys.exit(options.get("ss_status", 0))
elif name == "netstat":
    print(options.get("netstat", ""))
'''


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dsh-tests-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.state = self.root / "state"
        self.state.mkdir()
        self.prefix = self.root / "prefix"
        self.package = self.prefix / "lib/node_modules/@deepseek-ai/dsh"
        self.package.mkdir(parents=True)
        (self.package / "version").write_text("1.0.0")
        (self.package / "dsh").write_text('#!/bin/bash\ncat "$(dirname -- "$0")/../lib/node_modules/@deepseek-ai/dsh/version"\n')
        (self.package / "dsh").chmod(0o755)
        (self.prefix / "bin").mkdir()
        (self.prefix / "bin/dsh").symlink_to(self.package / "dsh")
        for name in ("dsh-auto-update", "dsh-web-idle-check", "dsh-web-launcher", "dsh-web-open"):
            shutil.copy2(ROOT / "bin" / name, self.bin / name)
        for name in ("id", "sudo", "npm", "systemctl", "dsh-web-health", "dsh-post-update-fixes", "pgrep", "ss", "netstat"):
            path = self.bin / name
            path.write_text(MOCK)
            path.chmod(0o755)
        self.options = {}
        self.services = {"dsh-web.service": True, "dsh-app-window.service": False}
        self.env = dict(os.environ, FIXTURE=str(self.root), HOME=str(self.root),
                        PATH=f"{self.bin}:/usr/bin:/bin", DSH_WEB_STATE=str(self.state),
                        DSH_INSTALL_PREFIX=str(self.prefix))

    def run_script(self, name="dsh-auto-update", **env):
        (self.root / "options.json").write_text(json.dumps(self.options))
        (self.root / "services.json").write_text(json.dumps(self.services))
        return subprocess.run([str(self.bin / name)], env=dict(self.env, **env),
                              capture_output=True, text=True, timeout=15)

    def assert_restored(self):
        self.assertEqual((self.package / "version").read_text(), "1.0.0")
        self.assertTrue((self.prefix / "bin/dsh").is_symlink())
        self.assertEqual(json.loads((self.root / "services.json").read_text()), self.services)
        self.assertEqual(list((self.prefix / "lib/node_modules").glob(".dsh-backup.*")), [])

    def test_partial_npm_failure_restores_package_binary_and_services(self):
        self.options["install_fail"] = True
        result = self.run_script()
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assert_restored()

    def test_sigterm_during_install_rolls_back(self):
        self.options["signal"] = True
        self.assertEqual(self.run_script().returncode, 143)
        self.assert_restored()

    def test_health_failure_rolls_back(self):
        self.options["health_fail"] = True
        self.services["dsh-app-window.service"] = True
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assert_restored()

    def test_service_start_failure_rolls_back(self):
        self.options["start_fail"] = True
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assert_restored()

    def test_fix_failure_rolls_back(self):
        self.options["fixes_fail"] = True
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assert_restored()

    def test_wrong_installed_version_rolls_back(self):
        self.options["latest"] = "3.0.0"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assert_restored()

    def test_failed_rollback_retains_original_recovery_files(self):
        self.options.update(install_fail=True, restore_fail=True)
        self.assertNotEqual(self.run_script().returncode, 0)
        backups = list((self.prefix / "lib/node_modules").glob(".dsh-backup.*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual((backups[0] / "dsh/version").read_text(), "1.0.0")
        self.assertTrue((backups[0] / "bin").is_symlink())
        self.assertIn("ROLLBACK FAILED", (self.state / "auto-update.log").read_text())

    def test_success_preserves_closed_window(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.package / "version").read_text(), "2.0.0")
        self.assertEqual(json.loads((self.root / "services.json").read_text()), self.services)
        self.assertNotIn("restart dsh-app-window.service", (self.root / "calls").read_text())

    def test_success_restores_inactive_server(self):
        self.services["dsh-web.service"] = False
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual(json.loads((self.root / "services.json").read_text()), self.services)

    def test_update_defers_while_an_agent_is_running(self):
        self.options["busy"] = True
        self.assertEqual(self.run_script().returncode, 0)
        self.assertEqual((self.package / "version").read_text(), "1.0.0")
        self.assertNotIn("stop dsh-web.service", (self.root / "calls").read_text())

    def test_already_latest_does_not_touch_services(self):
        self.options["latest"] = "1.0.0"
        self.assertEqual(self.run_script().returncode, 0)
        self.assertFalse((self.root / "calls").exists())

    def test_invalid_version_cannot_trigger_install(self):
        self.options["latest"] = "2.0.0\n3.0.0"
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertEqual((self.package / "version").read_text(), "1.0.0")
        self.assertFalse((self.root / "calls").exists())

    def test_failed_backup_never_stops_server(self):
        self.options["backup_fail"] = True
        self.assertNotEqual(self.run_script().returncode, 0)
        self.assertNotIn("stop dsh-web.service", (self.root / "calls").read_text())
        self.assert_restored()

    def test_concurrent_update_is_skipped(self):
        with (self.state / "auto-update.lock").open("w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(self.run_script().returncode, 0)
        self.assertFalse((self.root / "calls").exists())

    def test_repeated_desktop_launch_reuses_the_window(self):
        self.assertEqual(self.run_script("dsh-web-open").returncode, 0)
        self.services = json.loads((self.root / "services.json").read_text())
        self.assertTrue(self.services["dsh-app-window.service"])
        self.assertEqual(self.run_script("dsh-web-open").returncode, 0)
        calls = (self.root / "calls").read_text()
        self.assertEqual(calls.count("start dsh-app-window.service"), 2)
        self.assertNotIn("restart", calls)

    def test_launcher_secures_token_redacts_every_line_and_preserves_exit(self):
        (self.package / "dsh").write_text('#!/bin/bash\nprintf "dsh web: http://127.0.0.1:3080/?token=synthetic-secret\\nerror token=other-secret"\nexit 7\n')
        (self.state / "url.txt").touch(mode=0o664)
        result = self.run_script("dsh-web-launcher")
        self.assertEqual(result.returncode, 7, result.stderr)
        self.assertNotIn("synthetic-secret", result.stdout)
        self.assertNotIn("other-secret", result.stdout)
        self.assertIn("[redacted]", result.stdout)
        self.assertEqual((self.state / "url.txt").read_text().strip(), "http://127.0.0.1:3080/?token=synthetic-secret")
        self.assertEqual((self.state / "url.txt").stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o700)

    def idle(self, stamp, **env):
        (self.state / "last-activity").write_text(str(stamp))
        return self.run_script("dsh-web-idle-check", **env)

    def test_idle_closes_confirmed_old_server(self):
        self.assertEqual(self.idle(int(time.time()) - 8000).returncode, 0)
        self.assertFalse(json.loads((self.root / "services.json").read_text())["dsh-web.service"])

    def test_idle_keeps_connected_clients(self):
        self.options["connections"] = "0 0 127.0.0.1:3080 127.0.0.1:40000\n"
        self.assertEqual(self.idle(0).returncode, 0)
        self.assertTrue(json.loads((self.root / "services.json").read_text())["dsh-web.service"])

    def test_idle_keeps_window_open(self):
        self.services["dsh-app-window.service"] = True
        self.assertEqual(self.idle(0).returncode, 0)
        self.assertGreater(int((self.state / "last-activity").read_text()), 0)

    def test_idle_keeps_server_when_ss_fails(self):
        self.options["ss_status"] = 1
        self.assertEqual(self.idle(0).returncode, 0)
        self.assertTrue(json.loads((self.root / "services.json").read_text())["dsh-web.service"])

    def test_idle_keeps_background_agent_running_without_clients(self):
        self.options["busy"] = True
        self.assertEqual(self.idle(0).returncode, 0)
        self.assertTrue(json.loads((self.root / "services.json").read_text())["dsh-web.service"])

    def test_idle_recovers_invalid_empty_and_future_stamps(self):
        for stamp in ("", "garbage", "a[$(touch INJECTED)]", "999999999999"):
            with self.subTest(stamp=stamp):
                self.assertEqual(self.idle(stamp).returncode, 0)
                self.assertGreater(int((self.state / "last-activity").read_text()), 0)
        self.assertFalse((self.root / "INJECTED").exists())

    def test_idle_rejects_bad_numeric_settings(self):
        for env in ({"DSH_WEB_IDLE_MIN": "bad"}, {"DSH_WEB_PORT": "99999"}, {"DSH_WEB_IDLE_MIN": "-1"}):
            with self.subTest(env=env):
                self.assertEqual(self.idle(0, **env).returncode, 2)

    def test_idle_netstat_recognizes_full_local_address(self):
        (self.bin / "ss").unlink()
        for name in ("date", "awk"):
            (self.bin / name).symlink_to(shutil.which(name))
        self.env["PATH"] = str(self.bin)
        self.options["netstat"] = "tcp 0 0 127.0.0.1:3080 127.0.0.1:1234 ESTABLISHED\n"
        self.assertEqual(self.idle(0).returncode, 0)
        self.assertGreater(int((self.state / "last-activity").read_text()), 0)


class HealthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        loader = importlib.machinery.SourceFileLoader("health", str(ROOT / "bin/dsh-web-health"))
        cls.health = importlib.util.module_from_spec(importlib.util.spec_from_loader(loader.name, loader))
        loader.exec_module(cls.health)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="dsh-health-")
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.status = 200
        self.running = False
        self.rpc_ok = True
        self.redirect = "./"
        self.body = b"<html>__DSH_BOOT_READY__ __DSH_CONNECTION_RECOVERY__</html>"
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path == "/?token=synthetic":
                    self.send_response(302)
                    self.send_header("Set-Cookie", "test-auth=yes; Path=/")
                    self.send_header("Location", owner.redirect)
                    self.end_headers()
                elif self.headers.get("Cookie") != "test-auth=yes":
                    self.send_response(401)
                    self.end_headers()
                else:
                    self.send_response(owner.status)
                    self.send_header("Content-Type", "text/html")
                    self.end_headers()
                    self.wfile.write(owner.body)

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                result = {"ok": owner.rpc_ok, "value": {"items": [{"running": owner.running}]}}
                self.wfile.write(json.dumps({"rpcId": request["rpcId"], "result": result}).encode())

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.port = self.server.server_port
        (self.state / "url.txt").write_text(f"http://127.0.0.1:{self.port}/?token=synthetic")

    def test_follows_auth_exchange_and_verifies_boot_injections(self):
        self.assertTrue(self.health.probe(self.state, self.port, 2))

    def test_rejects_plain_success_page(self):
        self.body = b"<html>unrelated server</html>"
        self.assertFalse(self.health.probe(self.state, self.port, 2))

    def test_rejects_server_error(self):
        self.status = 500
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.health.probe(self.state, self.port, 2)
        caught.exception.close()

    def test_rejects_stale_token(self):
        (self.state / "url.txt").write_text(f"http://127.0.0.1:{self.port}/?token=expired")
        with self.assertRaises(urllib.error.HTTPError) as caught:
            self.health.probe(self.state, self.port, 2)
        caught.exception.close()

    def test_rejects_broken_rpc_even_if_page_renders(self):
        self.rpc_ok = False
        self.assertFalse(self.health.probe(self.state, self.port, 2))

    def test_refuses_external_redirect_before_sending_it_a_request(self):
        self.redirect = "http://example.invalid/"
        with self.assertRaisesRegex(ValueError, "unexpected redirect"):
            self.health.probe(self.state, self.port, 2)

    def test_rejects_malformed_activity_evidence(self):
        self.running = "false"
        self.assertFalse(self.health.probe(self.state, self.port, 2, idle=True))

    def test_idle_check_detects_running_agents(self):
        self.running = True
        self.assertTrue(self.health.probe(self.state, self.port, 2))
        self.assertFalse(self.health.probe(self.state, self.port, 2, idle=True))

    def test_rejects_remote_and_wrong_port_urls(self):
        for url in (f"http://example.com:{self.port}/?token=synthetic", "http://127.0.0.1:1/?token=synthetic"):
            (self.state / "url.txt").write_text(url)
            self.assertFalse(self.health.probe(self.state, self.port, 2))


if __name__ == "__main__":
    unittest.main()
