"""Actual HKCU registry tests in an isolated namespace; never change real file associations."""
from pathlib import Path
import sys
import tempfile
import unittest
import uuid
import winreg

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import shell_integration as shell


class ShellTests(unittest.TestCase):
    def setUp(self):
        self.root = rf'Software\DiscordifyTests\{uuid.uuid4().hex}'
        self.folder = tempfile.TemporaryDirectory()
        self.exe = Path(self.folder.name) / 'space & unicode vídeo.exe'
        self.exe.write_bytes(b'MZ test fixture; never launched')

    def tearDown(self):
        # Delete only keys under the explicit, unique test namespace.
        def delete(path):
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
                    children = []
                    for index in range(winreg.QueryInfoKey(key)[0]):
                        children.append(winreg.EnumKey(key, index))
                for child in children:
                    delete(path + '\\' + child)
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
            except FileNotFoundError:
                pass
        assert self.root.startswith('Software\\DiscordifyTests\\')
        delete(self.root)
        self.folder.cleanup()

    def test_register_quoting_repeat_update_and_remove(self):
        shell.register(self.exe, self.root)
        shell.register(self.exe, self.root)
        self.assertTrue(shell.installed(self.root))
        for extension in shell.EXTENSIONS:
            path = shell.key_path(extension, self.root)
            self.assertEqual(shell.values(path)[''][0], 'Compress with Discordify')
            self.assertEqual(shell.values(path + '\\command')[''][0], f'"{self.exe.resolve()}" "%1"')
        other = Path(self.folder.name) / 'updated.exe'
        other.write_bytes(b'MZ another fixture')
        shell.register(other, self.root)
        self.assertIn(str(other), shell.values(shell.key_path('.mp4', self.root) + '\\command')[''][0])
        shell.remove(self.root)
        shell.remove(self.root)
        self.assertFalse(shell.installed(self.root))

    def test_conflicting_entry_is_preserved_before_any_writes(self):
        path = shell.key_path('.mkv', self.root)
        shell.write_values(path, {'': ('Other app', winreg.REG_SZ)})
        with self.assertRaises(OSError):
            shell.register(self.exe, self.root)
        self.assertEqual(shell.values(path)[''][0], 'Other app')
        self.assertIsNone(shell.values(shell.key_path('.mp4', self.root)))
        with self.assertRaises(OSError):
            shell.remove(self.root)
        self.assertEqual(shell.values(path)[''][0], 'Other app')


if __name__ == '__main__':
    unittest.main()
