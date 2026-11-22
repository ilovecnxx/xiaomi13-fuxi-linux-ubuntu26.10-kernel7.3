from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


PACKAGE = Path(__file__).resolve().parents[2] / 'packaging/fuxi-modem'
SYSTEMD = PACKAGE / 'root/usr/lib/systemd'
UNIT = 'fuxi-voice-collector.service'


@unittest.skipUnless(shutil.which('bwrap'), 'requires bubblewrap')
class CollectorInstallTest(unittest.TestCase):
    def setUp(self):
        self.etc = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = self.etc / 'systemd/system'
        self.config.mkdir(parents=True)
        self.link = self.config / 'fuxi-qmi-proxy.service.wants' / UNIT
        self.command = ['bwrap', '--unshare-all', '--ro-bind', '/', '/', '--dev', '/dev',
                        '--proc', '/proc', '--tmpfs', '/run', '--tmpfs', '/var',
                        '--bind', str(self.etc), '/etc', '--ro-bind', str(SYSTEMD / 'system'),
                        '/usr/lib/systemd/system', '--ro-bind', str(SYSTEMD / 'system-preset'),
                        '/usr/lib/systemd/system-preset', '--']

    def configure(self, previous=''):
        subprocess.run([*self.command, '/bin/sh', str(PACKAGE / 'DEBIAN/postinst'),
                        'configure', previous], check=True, capture_output=True, text=True)

    def systemctl(self, *args):
        subprocess.run([*self.command, 'systemctl', '--root=/', *args, UNIT],
                       check=True, capture_output=True, text=True)

    def test_first_install_and_static_package_upgrades(self):
        for previous in ('', '0.20261122.2131', '0.20261122.2145', '0.20261122.2147'):
            with self.subTest(previous=previous):
                self.configure(previous)
                self.assertEqual(self.link.readlink(), Path('/usr/lib/systemd/system') / UNIT)
                self.configure(previous)
                self.systemctl('disable')
                self.configure('0.20261122.2148')
                self.assertFalse(self.link.is_symlink())

    def test_mask_survives_first_install_and_upgrades(self):
        self.systemctl('mask')
        for previous in ('', '0.20261122.2147', '0.20261122.2148'):
            self.configure(previous)
            self.assertEqual((self.config / UNIT).readlink(), Path('/dev/null'))
            self.assertFalse(self.link.is_symlink())

    def test_local_disable_preset_before_migration(self):
        policy = self.etc / 'systemd/system-preset/10-local.preset'
        policy.parent.mkdir()
        policy.write_text(f'disable {UNIT}\n')
        self.configure('0.20261122.2147')
        self.assertFalse(self.link.is_symlink())
        self.systemctl('enable')
        self.configure('0.20261122.2147')
        self.assertTrue(self.link.is_symlink())


if __name__ == '__main__':
    unittest.main()
