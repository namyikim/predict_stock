"""운영체제와 무관하게 Windows 드라이브 간 경로 기록을 검증한다(2026-10-02)."""
import ntpath
import tempfile
import json
from types import SimpleNamespace
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
import run_weekly_sequence as runner


class SnapshotPathTests(unittest.TestCase):
    def test_same_drive_keeps_relative_path(self):
        with patch.object(runner.os, 'path', ntpath), patch.object(runner, 'ROOT', r'D:\repo'):
            self.assertEqual(runner.snapshot_display_path(r'D:\repo\cache\samsung.csv'), r'cache\samsung.csv')
            self.assertEqual(runner.snapshot_display_path(r'D:\data\samsung.csv'), r'..\data\samsung.csv')

    def test_different_drive_keeps_absolute_path(self):
        with patch.object(runner.os, 'path', ntpath), patch.object(runner, 'ROOT', r'D:\repo'):
            self.assertEqual(runner.snapshot_display_path(r'C:\data\samsung.csv'), r'C:\data\samsung.csv')

    def test_different_unc_share_keeps_absolute_path(self):
        with patch.object(runner.os, 'path', ntpath), patch.object(runner, 'ROOT', r'\\server\repo\project'):
            self.assertEqual(runner.snapshot_display_path(r'\\server\data\samsung.csv'), r'\\server\data\samsung.csv')

    def test_local_path_preserves_existing_relative_representation(self):
        self.assertEqual(runner.snapshot_display_path(ROOT / 'cache' / 'samsung.csv'),
                         str(Path('cache') / 'samsung.csv'))


class ManifestPathTests(unittest.TestCase):
    def test_cross_drive_fallback_preserves_manifest_identity_and_resume(self):
        class Recorded(Exception):
            pass

        with tempfile.TemporaryDirectory() as tmp:
            snapshot_path = Path(tmp) / 'snapshot.csv'
            snapshot_path.write_text('date,close\n2026-09-01,100\n')
            snapshot = SimpleNamespace(path=snapshot_path, sha256='snapshot-hash',
                                       ticker='005930.KS', adjusted=True, fetched_at='2026-09-02')
            runs = []
            start_run = runner.start_run

            def capture(*args, **kwargs):
                state, resumed = start_run(*args, **kwargs)
                runs.append((state, resumed))
                raise Recorded()

            with patch.object(runner, 'load_ohlcv_snapshot', return_value=snapshot), \
                    patch.object(runner, 'start_run', side_effect=capture), \
                    patch.object(runner, 'versions', return_value={}), \
                    patch.object(runner.os.path, 'relpath', side_effect=ValueError('different mount')):
                for _ in range(2):
                    with self.assertRaises(Recorded):
                        runner.main(['--task', 'S02', '--target', 'samsung', '--mode', 'quick',
                                     '--storage', tmp, '--results', str(Path(tmp) / 'results'), '--resume'])
            self.assertEqual([resumed for _, resumed in runs], [False, True])
            self.assertEqual(runs[0][0].run_dir, runs[1][0].run_dir)
            manifest = json.loads(runs[0][0].manifest_path.read_text())
            self.assertEqual(manifest['snapshot']['path'], str(snapshot_path))
            self.assertEqual(manifest['snapshot']['sha256'], 'snapshot-hash')
            self.assertTrue(manifest['data_hash'])
