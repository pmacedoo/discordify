"""Policy unit tests; actual FFmpeg coverage lives in test_compression.py."""
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from encoding import CPU, TTL, pick_result, read_cache, remember, forget


class SelectionTests(unittest.TestCase):
    def test_fastest_qualifying_encoder_wins(self):
        results = [{'encoder': CPU, 'quality': .98, 'seconds': 4},
                   {'encoder': 'h264_nvenc', 'quality': .97, 'seconds': 1}]
        self.assertEqual(pick_result(results), 'h264_nvenc')

    def test_faster_encoder_with_poor_quality_is_rejected(self):
        results = [{'encoder': CPU, 'quality': .98, 'seconds': 4},
                   {'encoder': 'h264_nvenc', 'quality': .90, 'seconds': 1}]
        self.assertEqual(pick_result(results), CPU)

    def test_cpu_can_win_on_speed(self):
        results = [{'encoder': CPU, 'quality': .98, 'seconds': .1},
                   {'encoder': 'h264_nvenc', 'quality': .98, 'seconds': .3}]
        self.assertEqual(pick_result(results), CPU)

    def test_missing_reference_fails_safely(self):
        self.assertEqual(pick_result([]), CPU)

    def test_cache_recovery_and_expiry(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'cache.json'
            path.write_text('broken')
            self.assertEqual(read_cache(path), {})
            remember('profile', CPU, path)
            self.assertEqual(read_cache(path)['profile']['encoder'], CPU)
            path.write_text(json.dumps({'old': {'time': time.time() - TTL - 1}, 'bad': []}))
            remember('new', 'h264_nvenc', path)
            self.assertEqual(set(read_cache(path)), {'new'})
            forget('new', path)
            self.assertEqual(read_cache(path), {})

    def test_cache_write_failure_is_nonfatal(self):
        with tempfile.TemporaryDirectory() as folder:
            remember('profile', CPU, Path(folder))


if __name__ == '__main__':
    unittest.main()
