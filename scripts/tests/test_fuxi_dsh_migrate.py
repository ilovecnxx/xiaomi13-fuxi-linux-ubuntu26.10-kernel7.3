import importlib.util
import io
import json
from pathlib import Path
import sys
import stat
import tarfile
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "fuxi-dsh-migrate.py"
SPEC = importlib.util.spec_from_file_location("fuxi_dsh_migrate", SCRIPT)
MIGRATE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MIGRATE
SPEC.loader.exec_module(MIGRATE)


class DshMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "phone-home"
        self.dsh = self.home / ".dsh"
        self.dsh.mkdir(parents=True)
        (self.dsh / "sessions").mkdir()
        (self.dsh / "sessions" / "a.jsonl.zst").write_bytes(b"session-data")
        (self.dsh / "attachments" / "v1" / "objects" / "aa").mkdir(parents=True)
        (self.dsh / "attachments" / "v1" / "objects" / "aa" / "object").write_bytes(b"image")
        (self.dsh / "storages").mkdir()
        (self.dsh / "storages" / "settings.json").write_text('{"theme":"dark"}\n', encoding="utf-8")
        (self.dsh / "profiles").mkdir()
        (self.dsh / "profiles" / "web.yml").write_text("profile: web\n", encoding="utf-8")
        (self.dsh / ".credentials.yaml").write_text("token: do-not-copy\n", encoding="utf-8")
        (self.home / ".config" / "systemd" / "user").mkdir(parents=True)
        (self.home / ".config" / "systemd" / "user" / "dsh-web.service").write_text("[Service]\n", encoding="utf-8")
        (self.home / ".config" / "dsh-web.env").parent.mkdir(parents=True, exist_ok=True)
        (self.home / ".config" / "dsh-web.env").write_text("DEEPSEEK_API_KEY=do-not-copy\n", encoding="utf-8")
        (self.home / ".config" / "dsh-app-chromium").mkdir()
        (self.home / ".config" / "dsh-app-chromium" / "Cookies").write_bytes(b"browser-cookie")
        (self.home / ".local" / "state" / "dsh-web").mkdir(parents=True)
        (self.home / ".local" / "state" / "dsh-web" / "url.txt").write_text("http://127.0.0.1/?token=do-not-copy\n", encoding="utf-8")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "README.md").write_text("workspace\n", encoding="utf-8")
        (self.workspace / ".env").write_text("SECRET=do-not-copy\n", encoding="utf-8")
        (self.workspace / "env-link").symlink_to(".env")

    def tearDown(self):
        self.temp.cleanup()

    def test_default_inventory_separates_credentials_and_keeps_data(self):
        inventory = MIGRATE.build_inventory(self.dsh, user_home=self.home, workspaces=[self.workspace])
        names = {entry.archive_path for entry in inventory.entries}
        self.assertIn("dsh_home/sessions/a.jsonl.zst", names)
        self.assertIn("dsh_home/attachments/v1/objects/aa/object", names)
        self.assertIn("dsh_home/settings/profiles/web.yml", names)
        self.assertIn("workspaces/0/README.md", names)
        self.assertNotIn("dsh_home/settings/.credentials.yaml", names)
        self.assertNotIn("workspaces/0/.env", names)
        excluded = {(item.source_root, item.source_path) for item in inventory.excluded}
        self.assertIn(("dsh_home", ".credentials.yaml"), excluded)
        self.assertIn(("user_home", ".config/dsh-web.env"), excluded)
        self.assertIn(("user_home", ".config/dsh-app-chromium"), excluded)
        self.assertIn(("user_home", ".local/state/dsh-web/url.txt"), excluded)

    def test_backup_and_verify_round_trip_without_secret_bytes(self):
        output = self.root / "backup.tar.gz"
        inventory = MIGRATE.build_inventory(self.dsh, workspaces=[self.workspace])
        result = MIGRATE.write_backup(inventory, output)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(MIGRATE.verify_backup(output)["status"], "ok")
        self.assertEqual(stat.S_IMODE(output.stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(str(output) + ".manifest.json").stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(Path(str(output) + ".sha256").stat().st_mode), 0o600)
        with tarfile.open(output, "r:gz") as archive:
            names = archive.getnames()
            self.assertIn("manifest.json", names)
            payload = b"".join(
                member_data for name in names
                if (member := archive.getmember(name)).isfile()
                for member_data in [archive.extractfile(member).read()]
            )
        self.assertNotIn(b"do-not-copy", payload)
        self.assertEqual((self.root / "backup.tar.gz.sha256").read_text().split()[0], MIGRATE.archive_digest(output))

    def test_include_secrets_requires_explicit_acknowledgement_at_cli(self):
        with self.assertRaises(SystemExit) as raised:
            MIGRATE.main([
                "backup", "--dsh-home", str(self.dsh), "--include-secrets",
                "--output", str(self.root / "rejected.tar.gz"),
            ])
        self.assertEqual(raised.exception.code, 2)
        self.assertFalse((self.root / "rejected.tar.gz").exists())

    def test_include_secrets_is_recorded_and_verifiable(self):
        output = self.root / "with-secrets.tar.gz"
        inventory = MIGRATE.build_inventory(self.dsh, user_home=self.home, workspaces=[self.workspace], include_secrets=True)
        result = MIGRATE.write_backup(inventory, output)
        self.assertTrue(result["include_secrets"])
        self.assertEqual(MIGRATE.verify_backup(output)["status"], "ok")
        with tarfile.open(output, "r:gz") as archive:
            manifest = json.loads(archive.extractfile("manifest.json").read())
            self.assertTrue(manifest["include_secrets"])
            self.assertIn("credentials/user_home/.config/dsh-web.env", archive.getnames())
            self.assertIn("credentials/user_home/.config/dsh-app-chromium/Cookies", archive.getnames())

    def test_only_relative_symlinks_staying_inside_source_tree_are_archived(self):
        target = self.root / "outside-secret"
        target.write_text("do-not-copy\n", encoding="utf-8")
        link = self.dsh / "external-link"
        link.symlink_to(target)
        internal_link = self.dsh / "settings-link"
        internal_link.symlink_to("profiles/web.yml")
        inventory = MIGRATE.build_inventory(self.dsh, workspaces=[self.workspace])
        entry = next(item for item in inventory.entries if item.source_path == "settings-link")
        self.assertEqual(entry.kind, "symlink")
        self.assertIn(("dsh_home", "external-link", "unsafe-symlink-target"), {
            (item.source_root, item.source_path, item.reason) for item in inventory.excluded
        })
        self.assertIn(("workspace_0", "env-link", "unsafe-symlink-target"), {
            (item.source_root, item.source_path, item.reason) for item in inventory.excluded
        })
        output = self.root / "symlink.tar.gz"
        MIGRATE.write_backup(inventory, output)
        with tarfile.open(output, "r:gz") as archive:
            member = archive.getmember("dsh_home/settings/settings-link")
            self.assertTrue(member.issym())
            self.assertEqual(member.linkname, "profiles/web.yml")
            self.assertNotIn("dsh_home/settings/external-link", archive.getnames())
            self.assertNotIn(b"do-not-copy", archive.extractfile("manifest.json").read())

    def test_changed_source_cannot_publish_unverified_archive(self):
        inventory = MIGRATE.build_inventory(self.dsh)
        (self.dsh / "sessions" / "a.jsonl.zst").write_bytes(b"changed-after-audit")
        output = self.root / "stale.tar.gz"
        with self.assertRaisesRegex(ValueError, "self-verification"):
            MIGRATE.write_backup(inventory, output)
        self.assertFalse(output.exists())
        self.assertFalse(Path(str(output) + ".manifest.json").exists())
        self.assertFalse(Path(str(output) + ".sha256").exists())

    def rewrite_archive(self, source, destination, *, manifest_data=None, add_extra=False):
        with tarfile.open(source, "r:gz") as original, tarfile.open(destination, "w:gz") as rewritten:
            for member in original.getmembers():
                if member.isfile():
                    data = original.extractfile(member).read()
                    if member.name == "manifest.json" and manifest_data is not None:
                        data = manifest_data
                        member.size = len(data)
                    rewritten.addfile(member, io.BytesIO(data))
                else:
                    rewritten.addfile(member)
            if add_extra:
                member = tarfile.TarInfo("unlisted.txt")
                member.size = 1
                rewritten.addfile(member, io.BytesIO(b"x"))

    def test_verifier_rejects_corrupt_manifest(self):
        output = self.root / "valid.tar.gz"
        MIGRATE.write_backup(MIGRATE.build_inventory(self.dsh), output)
        broken = self.root / "broken-manifest.tar.gz"
        self.rewrite_archive(output, broken, manifest_data=b"{broken\n")
        result = MIGRATE.verify_backup(broken)
        self.assertEqual(result["status"], "invalid")
        self.assertTrue(any("cannot read backup" in error for error in result["errors"]))

    def test_verifier_rejects_unlisted_archive_members(self):
        output = self.root / "valid.tar.gz"
        MIGRATE.write_backup(MIGRATE.build_inventory(self.dsh), output)
        extended = self.root / "unlisted-member.tar.gz"
        self.rewrite_archive(output, extended, add_extra=True)
        result = MIGRATE.verify_backup(extended)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("unlisted archive member: unlisted.txt", result["errors"])

    def test_verifier_detects_changed_sidecar_manifest(self):
        output = self.root / "valid.tar.gz"
        MIGRATE.write_backup(MIGRATE.build_inventory(self.dsh), output)
        sidecar = Path(str(output) + ".manifest.json")
        sidecar.write_text("{}\n", encoding="utf-8")
        result = MIGRATE.verify_backup(output)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("sidecar manifest does not match archive manifest", result["errors"])


if __name__ == "__main__":
    unittest.main()
