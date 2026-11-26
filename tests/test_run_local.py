from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import psutil

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_local


class LauncherStartupTests(unittest.TestCase):
    def test_default_and_compatibility_entry_points_run_official_checks(self):
        from livekit_vlm_agent.config import Settings

        for flags in ([], ['--mobile'], ['--qwen-live', '--official-proactive'],
                      ['--trial-port', '8097']):
            with self.subTest(flags=flags), tempfile.TemporaryDirectory() as directory:
                settings = Settings(_env_file=None, model_provider='yibuapi', yibuapi_api_key='test')
                with (
                    patch.multiple(run_local, RUNTIME=Path(directory), PID_FILE=None, STOP_FILE=None),
                    patch.object(sys, 'argv', ['run_local.py', *flags]),
                    patch.object(run_local, 'Settings', return_value=settings),
                    patch('livekit_vlm_agent.official.bridge.node_executable',
                          side_effect=RuntimeError('Node.js is required')) as node,
                    patch.object(run_local.subprocess, 'Popen') as spawn,
                    self.assertRaises(SystemExit) as stopped,
                ):
                    run_local.main()
                self.assertEqual(stopped.exception.code, 1)
                node.assert_called_once()
                spawn.assert_not_called()


class LauncherRecordTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.pid_file = Path(directory) / 'local-services.json'
        self.stop_file = Path(directory) / 'local-services.stop'
        self.enterContext(patch.multiple(run_local, PID_FILE=self.pid_file, STOP_FILE=self.stop_file))

    def set_record(self, launcher, **children):
        self.pid_file.write_text(json.dumps({'launcher': launcher, 'children': children}), encoding='utf-8')

    def test_start_replaces_legacy_record_after_all_processes_exit(self):
        self.set_record(100, worker=101)
        self.stop_file.write_text('stop', encoding='utf-8')
        processes = [psutil.NoSuchProcess(100), psutil.NoSuchProcess(101), psutil.Process()]
        with patch.object(psutil, 'Process', side_effect=processes):
            record = run_local.claim_record()
        self.assertEqual(record, run_local.read_record())
        self.assertEqual(record['launcher'], os.getpid())
        self.assertIn('launcher', record['creation_times'])
        self.assertFalse(self.stop_file.exists())

    def test_stop_discards_stale_record_without_waiting_for_a_launcher(self):
        self.set_record(100, worker=101)
        with patch.object(psutil, 'Process', side_effect=psutil.NoSuchProcess(100)), patch.object(run_local.time, 'sleep') as sleep:
            self.assertEqual(run_local.stop_services(), 0)
        self.assertFalse(self.pid_file.exists())
        self.assertFalse(self.stop_file.exists())
        sleep.assert_not_called()

    def test_active_launcher_is_preserved_and_can_be_stopped(self):
        record = run_local.claim_record()
        self.stop_file.write_text('stop', encoding='utf-8')
        with self.assertRaisesRegex(SystemExit, 'already have a launcher'):
            run_local.claim_record()
        self.assertEqual(run_local.read_record(), record)
        self.assertTrue(self.stop_file.exists())
        self.stop_file.unlink()

        def launcher_handles_stop(_):
            self.assertEqual(self.stop_file.read_text(encoding='utf-8'), 'stop')
            self.stop_file.unlink()
            self.pid_file.unlink()

        with patch.object(run_local.time, 'sleep', side_effect=launcher_handles_stop):
            self.assertEqual(run_local.stop_services(), 0)

    def test_abandoned_record_with_live_service_is_preserved(self):
        self.set_record(100, worker=os.getpid())
        processes = [psutil.NoSuchProcess(100), psutil.Process()] * 2
        with patch.object(psutil, 'Process', side_effect=processes):
            for action in (run_local.claim_record, run_local.stop_services):
                with self.assertRaisesRegex(SystemExit, 'may still be running'):
                    action()
        self.assertTrue(self.pid_file.exists())
        self.assertFalse(self.stop_file.exists())

    def test_reused_pid_does_not_identify_the_old_launcher(self):
        previous = {'launcher': os.getpid(), 'children': {},
                    'creation_times': {'launcher': psutil.Process().create_time() - 60}}
        run_local.write_record(previous)
        current = run_local.claim_record()
        self.assertNotEqual(current['creation_times'], previous['creation_times'])

    def test_concurrent_start_has_only_one_owner(self):
        def try_start():
            try:
                return run_local.claim_record()
            except SystemExit as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: try_start(), range(2)))
        self.assertIn(run_local.read_record(), results)
        self.assertEqual(sum(isinstance(result, dict) for result in results), 1)
        self.assertTrue(any(isinstance(result, str) and 'already have a launcher' in result for result in results))

    def test_unverifiable_records_are_preserved(self):
        self.pid_file.write_text('{', encoding='utf-8')
        with self.assertRaisesRegex(SystemExit, 'Cannot verify'):
            run_local.claim_record()
        self.assertEqual(self.pid_file.read_text(encoding='utf-8'), '{')

        self.set_record(100)
        with patch.object(psutil, 'Process', side_effect=psutil.AccessDenied(100)):
            with self.assertRaisesRegex(SystemExit, 'already have a launcher'):
                run_local.claim_record()
        self.assertEqual(run_local.read_record()['launcher'], 100)
