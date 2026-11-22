import json
import logging
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from notifyhub.config import ensure_config, load_config, save_config
from notifyhub.gui import install_dsh_payload
from notifyhub import native_install, dsh_install


class NativeGuiInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        ensure_config(self.config)
        cfg = load_config(self.config)
        cfg['channels']['feishu_app'].update(app_id='test-app', app_secret='test-secret', receive_id='ou_owner')
        save_config(cfg, self.config)
        self.runtime = self.root / 'runtime'
        self.home = self.root / 'dsh-home'
        self.profile = self.home / 'profiles/desktop/cordis.patch.yml'
        self.profile.parent.mkdir(parents=True)
        self.profile.write_text('# User settings\n- id: unrelated\n  config:\n    script: !!js () => true\n')
        self.source = self.root / 'source'
        for name in ('dsh', 'dsh-native-bridge'):
            p = self.source / 'integrations' / name
            p.mkdir(parents=True)
            (p / 'package.json').write_text('{}')
        (self.source / 'scripts').mkdir()
        (self.source / 'scripts/notifyhub.py').write_text('# launcher')
        python = native_install._python(self.runtime)
        python.parent.mkdir(parents=True)
        python.touch()
        self.patches = [patch.object(dsh_install, 'find_plugin', return_value=self.source / 'integrations/dsh'),
                        patch.object(dsh_install, 'dsh_binary', return_value='/desktop/dsh'),
                        patch.object(native_install, '_run'),
                        patch.object(native_install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, '', ''))]
        for p in self.patches: p.start()

    def tearDown(self):
        for p in reversed(self.patches): p.stop()
        self.temp.cleanup()

    def install(self, **options):
        return native_install.install(self.config, {'profile': 'desktop', **options}, runtime=self.runtime, dsh_home=self.home)

    def test_installs_both_plugins_disabled_and_keeps_source_config_and_ledger(self):
        source = self.config.read_bytes()
        ledger = self.root / 'ledger.json'; ledger.write_text('keep')
        result = self.install(enabled=False, allow_open_ids=[])
        self.assertTrue(result['ok'], result)
        cfg = load_config(native_install.native_config_path(self.config))
        self.assertFalse(cfg['bridge']['enabled'])
        self.assertEqual(self.config.read_bytes(), source)
        self.assertEqual(ledger.read_text(), 'keep')
        command = native_install._run.call_args.args[0]
        self.assertIn('link:' + (self.runtime / 'integrations/dsh').as_posix(), command)
        self.assertIn('link:' + (self.runtime / 'integrations/dsh-native-bridge').as_posix(), command)
        self.assertIn('!!js () => true', self.profile.read_text())
        self.assertIn('enabled: false', self.profile.read_text())
        self.assertIn('- id: dsh-notifyhub', self.profile.read_text())
        self.assertIn(str(self.config), self.profile.read_text())

    def test_enable_requires_explicit_allowlist_before_mutation(self):
        old = self.profile.read_text()
        result = self.install(enabled=True, allow_open_ids=[])
        self.assertFalse(result['ok'])
        native_install._run.assert_not_called()
        self.assertEqual(self.profile.read_text(), old)
        self.assertFalse(native_install.native_config_path(self.config).exists())

    def test_repeat_install_preserves_advanced_config_and_has_one_override(self):
        self.assertTrue(self.install(enabled=True, allow_open_ids=['ou_owner'])['ok'])
        target = native_install.native_config_path(self.config)
        cfg = load_config(target);cfg['bridge']['timeout_seconds'] = 999
        save_config(cfg, target)
        result = self.install(enabled=False, allow_open_ids=['ou_owner'], card_actions=True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(load_config(target)['bridge']['timeout_seconds'], 999)
        self.assertTrue(load_config(target)['bridge']['card_actions'])
        self.assertEqual(self.profile.read_text().count('- id: notifyhub-native-bridge'), 1)
        self.assertTrue(Path(result['backup_path'], 'native-config.json').exists())

    def test_package_install_failure_does_not_enable_or_replace_profile(self):
        old = self.profile.read_bytes()
        native_install._run.side_effect = RuntimeError('npm failed')
        result = self.install(enabled=True, allow_open_ids=['ou_owner'])
        self.assertFalse(result['ok'])
        self.assertEqual(self.profile.read_bytes(), old)
        self.assertFalse(native_install.native_config_path(self.config).exists())

    def test_rejects_web_and_invalid_ids(self):
        result = native_install.install(self.config, {'profile': 'web'}, runtime=self.runtime, dsh_home=self.home)
        self.assertFalse(result['ok'])
        self.assertFalse(self.install(enabled=True, allow_open_ids=['not-an-open-id'])['ok'])
        native_install._run.assert_not_called()

    def test_gui_routes_modes_without_silently_enabling_native(self):
        with patch.object(dsh_install, 'install', return_value={'ok': True}) as notify, patch.object(native_install, 'install', return_value={'ok': True}) as native:
            install_dsh_payload(self.config, {'profile': 'desktop'})
            notify.assert_called_once_with('desktop');native.assert_not_called()
            options = {'profile': 'desktop', 'mode': 'native', 'enabled': False}
            install_dsh_payload(self.config, options)
            native.assert_called_once_with(self.config, options)
            self.assertFalse(install_dsh_payload(self.config, {'mode': 'unknown'})['ok'])

    def test_native_dependency_does_not_count_as_notification_plugin(self):
        file = self.home / 'profiles/desktop/package.json'
        file.write_text(json.dumps({'dependencies': {'dsh-notifyhub-native-bridge': 'link:plugin'}}))
        with patch.dict('os.environ', {'DSH_HOME': str(self.home)}):
            self.assertFalse(dsh_install.profile_installed('desktop'))

    def test_profile_patch_preserves_other_plugins_and_state_path(self):
        old = '- id: notifyhub-native-bridge\n  config:\n    enabled: false\n    state_path: /custom/ledger.json\n- id: other\n  config:\n    value: !!js 1 + 2\n'
        new = native_install._patch_profile(old, ['/a b/python', 'launcher'], True)
        self.assertIn('state_path: /custom/ledger.json', new)
        self.assertIn('value: !!js 1 + 2', new)
        self.assertEqual(new.count('- id: notifyhub-native-bridge'), 1)
