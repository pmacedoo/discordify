"""Outdated-copy cleanup on temporary files only."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import copies


class CopiesTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.root = Path(self.folder.name)
        self.store = {'text': None}

    def tearDown(self):
        self.folder.cleanup()

    def exe(self, name, data=b'MZ'):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def run_as(self, path, version, keep_under=None):
        return copies.prune(path, version, lambda: self.store['text'],
                            lambda text: self.store.update(text=text), keep_under)

    def test_newer_download_deletes_older_and_duplicate_copies(self):
        old = self.exe('Downloads/Discordify.exe')
        self.run_as(old, '1.2.0')
        same = self.exe('Desktop/Discordify.exe')
        self.assertEqual(self.run_as(same, '1.2.0'), [old])  # Downloaded again: the earlier copy goes.
        new = self.exe('Downloads/Discordify (1).exe', b'MZ newer')
        self.assertEqual(self.run_as(new, '1.3.0'), [same])
        self.assertFalse(old.exists() or same.exists())
        self.assertTrue(new.exists())
        self.assertEqual([e['path'] for e in json.loads(self.store['text'])], [str(new)])

    def test_never_deletes_newer_changed_or_menu_copies(self):
        newer = self.exe('a/Discordify.exe')
        self.run_as(newer, '2.0.0')
        changed = self.exe('b/Discordify.exe')
        self.run_as(changed, '1.0.0')
        changed.write_bytes(b'something else the user put here')
        os.utime(changed, (1, 1))
        menu = self.exe('app/1.0.0/Discordify.exe')
        self.run_as(menu, '1.0.0', keep_under=self.root / 'app')
        current = self.exe('c/Discordify.exe')
        self.assertEqual(self.run_as(current, '1.5.0', keep_under=self.root / 'app'), [])
        self.assertTrue(newer.exists() and changed.exists() and menu.exists())

    def test_corrupt_store_is_ignored(self):
        self.store['text'] = '{not json'
        current = self.exe('Discordify.exe')
        self.assertEqual(self.run_as(current, '1.0.0'), [])
        self.assertEqual(len(json.loads(self.store['text'])), 1)


if __name__ == '__main__':
    unittest.main()
