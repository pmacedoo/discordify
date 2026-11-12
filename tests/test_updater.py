"""Updater logic with a fake fetch; never touches the network or the real executable."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
import updater


def fake(release, files):
    def fetch(url, timeout=20):
        return json.dumps(release).encode() if url == updater.API else files[url]
    return fetch


def release(tag, names=('Discordify.exe', 'SHA256SUMS.txt'), author='pmacedoo', uploader='pmacedoo', **extra):
    return {'tag_name': tag, 'author': {'login': author},
            'assets': [{'name': n, 'browser_download_url': f'https://x/{n}', 'uploader': {'login': uploader}} for n in names],
            **extra}


class UpdaterTests(unittest.TestCase):
    def test_versions(self):
        self.assertTrue(updater.newer('v9.0.0', '1.2.0'))
        self.assertTrue(updater.newer('v1.10.0', '1.9.0'))
        self.assertFalse(updater.newer('v1.2.0', '1.2.0'))
        self.assertFalse(updater.newer('v1.1.9', '1.2.0'))
        self.assertFalse(updater.newer('nightly', '1.2.0'))

    def test_latest_release(self):
        found = updater.latest_release(fake(release('v99.0.0', html_url='https://x/page'), {}))
        self.assertEqual((found[0], found[3]), ('v99.0.0', 'https://x/page'))
        self.assertIsNone(updater.latest_release(fake(release(f'v{updater.VERSION}'), {})))
        self.assertIsNone(updater.latest_release(fake(release('v99.0.0', prerelease=True), {})))
        self.assertIsNone(updater.latest_release(fake(release('v99.0.0', names=('Discordify.exe',)), {})))
        self.assertIsNone(updater.latest_release(fake(release('v99.0.0', author='someone'), {})))
        self.assertIsNone(updater.latest_release(fake(release('v99.0.0', uploader='someone'), {})))

    def test_download_verifies_checksum(self):
        data = b'MZ new build'
        good = f'{hashlib.sha256(data).hexdigest()}  Discordify.exe\n{"0" * 64}  other.zip\n'.encode()
        rel = ('v99.0.0', 'https://x/Discordify.exe', 'https://x/SHA256SUMS.txt', 'https://x/page')
        with tempfile.TemporaryDirectory() as folder:
            path = updater.download(rel, folder, fake({}, {rel[1]: data, rel[2]: good}))
            self.assertEqual(path.read_bytes(), data)
            bad = good.replace(hashlib.sha256(data).hexdigest().encode(), b'1' * 64)
            with self.assertRaises(ValueError):
                updater.download(rel, folder, fake({}, {rel[1]: data, rel[2]: bad}))

    def test_swap_and_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            exe, new = Path(folder) / 'Discordify.exe', Path(folder) / 'x.download'
            exe.write_bytes(b'old')
            new.write_bytes(b'new')
            updater.swap(new, exe)
            self.assertEqual(exe.read_bytes(), b'new')
            self.assertTrue((Path(folder) / 'Discordify.exe.old').exists())
            (Path(folder) / 'tmpabc.download').write_bytes(b'partial')
            updater.cleanup(exe)
            self.assertFalse((Path(folder) / 'tmpabc.download').exists())
            self.assertFalse((Path(folder) / 'Discordify.exe.old').exists())


if __name__ == '__main__':
    unittest.main()
