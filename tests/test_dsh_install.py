import contextlib
import io
import logging
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from notifyhub import __version__
from notifyhub import dsh_install
from notifyhub.cli import main


class DshDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.binary = Path(self.temp.name) / 'DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh'
        self.binary.parent.mkdir(parents=True)
        self.binary.write_text('#!/bin/sh\nexit 0\n')
        self.binary.chmod(0o755)
        self.env = patch.dict(os.environ, {'NOTIFYHUB_DSH_BIN': '', 'DSH_HOME': self.temp.name})
        self.env.start()
        self.candidates = patch.object(dsh_install, '_desktop_cli_candidates', return_value=[self.binary])
        self.candidates.start()

    def tearDown(self):
        self.candidates.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_desktop_without_path_uses_bundled_cli(self):
        with patch.object(dsh_install.shutil, 'which', return_value=None):
            self.assertEqual(dsh_install.dsh_binary('desktop'), str(self.binary))
            self.assertTrue(dsh_install.status()['dsh_found'])

    def test_desktop_prefers_its_own_cli_while_web_uses_path(self):
        with patch.object(dsh_install.shutil, 'which', return_value='/npm/bin/dsh'):
            self.assertEqual(dsh_install.dsh_binary('desktop'), str(self.binary))
            self.assertEqual(dsh_install.dsh_binary('web'), '/npm/bin/dsh')

    def test_override_can_select_a_custom_install(self):
        custom = Path(self.temp.name) / 'custom-dsh'
        custom.write_text('test'); custom.chmod(0o755)
        with patch.dict(os.environ, {'NOTIFYHUB_DSH_BIN': str(custom)}):
            self.assertEqual(dsh_install.dsh_binary('desktop'), str(custom))

    def test_gui_install_invokes_discovered_binary_even_with_spaces(self):
        with patch.object(dsh_install.shutil, 'which', return_value=None), patch.object(dsh_install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'ok', '')) as run:
            result = dsh_install.install('desktop')
        self.assertTrue(result['ok'])
        self.assertEqual(run.call_args.args[0][0], str(self.binary))
        self.assertEqual(run.call_args.args[0][1:4], ['plugin', '--profile', 'desktop'])

    def test_packaged_gui_preserves_its_market_installation(self):
        profile = Path(self.temp.name).resolve() / 'profiles/desktop'
        plugin = profile / 'node_modules/dsh-notifyhub'
        (plugin / 'scripts').mkdir(parents=True)
        (plugin / 'scripts/notifyhub.py').touch()
        (profile / 'package.json').write_text('{"dependencies": {"dsh-notifyhub": "release-url"}}')
        with patch.object(dsh_install, 'find_plugin', return_value=plugin), patch.object(dsh_install.subprocess, 'run') as run:
            self.assertTrue(dsh_install.install('desktop')['ok'])
            run.assert_not_called()
            self.assertTrue(dsh_install.build_command('web')[-1].endswith(f'/v{__version__}/dsh-notifyhub.tgz'))

    def test_nonexecutable_bundle_is_not_used(self):
        if os.name == 'nt': self.skipTest('POSIX execute permission')
        self.binary.chmod(0o644)
        with patch.object(dsh_install.shutil, 'which', return_value=None):
            self.assertIsNone(dsh_install.dsh_binary('desktop'))

    def test_spawn_error_is_actionable_instead_of_gui_500(self):
        with patch.object(dsh_install.subprocess, 'run', side_effect=FileNotFoundError('missing interpreter')):
            result = dsh_install.install('desktop')
        self.assertFalse(result['ok'])
        self.assertIn('无法执行', result['error'])

    def test_desktop_initialization_error_identifies_the_next_step(self):
        outcome = subprocess.CompletedProcess([], 1, '', 'Open DSH once to initialize its profile.')
        with patch.object(dsh_install.subprocess, 'run', return_value=outcome):
            result = dsh_install.install('desktop')
        self.assertFalse(result['ok'])
        self.assertIn('初始化配置', result['error'])

    def test_missing_cli_has_install_guidance_and_failure_exit(self):
        with patch.object(dsh_install, '_desktop_cli_candidates', return_value=[]), patch.object(dsh_install.shutil, 'which', return_value=None):
            result = dsh_install.install('desktop')
            self.assertFalse(result['ok'])
            self.assertIn('npm install -g', result['error'])
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main(['install-dsh', '--profile', 'desktop']), 1)

    def test_user_applications_directory_is_searched(self):
        self.candidates.stop()
        with patch.object(Path, 'home', return_value=Path('/user-home')):
            candidates = list(dsh_install._desktop_cli_candidates())
        self.assertIn(Path('/user-home/Applications/DeepSeek Harness.app/Contents/Resources/runtime/cli/bin/dsh'), candidates)
        self.candidates.start()
