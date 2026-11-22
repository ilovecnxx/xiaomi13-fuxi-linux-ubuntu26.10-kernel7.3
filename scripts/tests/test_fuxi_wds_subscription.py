import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


class WdsSubscriptionTest(unittest.TestCase):
    def test_runtime_callbacks_and_placement(self):
        recipes = json.loads((ROOT / 'packaging/sources/manifest.json').read_text())
        names = ('modemmanager', '81voltd')
        if not all((ROOT / 'build/source-cache' / recipes[name]['sha256']).exists() for name in names):
            self.skipTest('requires cached modem source archives')
        if not shutil.which('cc') or not shutil.which('pkg-config'):
            self.skipTest('requires host C compiler and pkg-config')
        flags = subprocess.run(['pkg-config', '--cflags', '--libs', 'qmi-glib >= 1.38', 'mm-glib'],
                               capture_output=True, text=True)
        if flags.returncode:
            self.skipTest('requires qmi-glib 1.38 and mm-glib headers')
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)
            subprocess.run([sys.executable, ROOT / 'scripts/fuxi-sources.py', 'prepare',
                            *names, '--output', target], check=True, capture_output=True)
            source = (target / 'modemmanager/src/mm-bearer-qmi.c').read_text()
            for family in ('IPV4', 'IPV6'):
                steps = re.findall(r'case CONNECT_STEP_(\w+):', source)
                expected = [f'{step}_{family}' for step in ('WDS_CLIENT', 'BIND_DATA_PORT',
                            'BIND_SUBSCRIPTION', 'IP_FAMILY', 'ENABLE_INDICATIONS', 'START_NETWORK')]
                start = steps.index(expected[0])
                self.assertEqual(steps[start:start + len(expected)], expected)
                self.assertRegex(source, rf'case CONNECT_STEP_BIND_SUBSCRIPTION_{family}:\s+'
                                         rf'if \(bind_primary_subscription \(ctx->client_{family.lower()}, task\)\)\s+return;')
            handlers = source[source.index('static void\nprimary_subscription_checked'):
                              source.index('static void\nbind_data_port_ready')]
            (target / 'mm-bind.inc').write_text(handlers)
            binary = target / 'wds-test'
            subprocess.run(['cc', '-Wall', '-Werror', '-I' + str(target), '-I' + str(target / '81voltd'),
                            ROOT / 'scripts/tests/fixtures/fuxi_wds_subscription.c', '-o', binary,
                            *shlex.split(flags.stdout)], check=True, timeout=30)
            subprocess.run([binary], cwd=target, check=True, timeout=10,
                           env={'G_DEBUG': 'fatal-criticals'})
