"""A second launch hands its file to the running instance over a per-test local socket."""
from pathlib import Path
import subprocess
import sys
import time
import unittest
import uuid

SRC = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SRC))
from PySide6.QtWidgets import QApplication
import single_instance



class SingleInstanceTests(unittest.TestCase):
    def setUp(self):
        self.app = QApplication.instance() or QApplication([])
        self.name = f'DiscordifyTest-{uuid.uuid4().hex}'

    def pump(self, until, seconds=3):
        deadline = time.monotonic() + seconds
        while not until() and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)

    def test_nothing_running(self):
        self.assertFalse(single_instance.forward('x', self.name, timeout=200))

    def test_forwards_path_to_running_instance(self):
        server = single_instance.Server(self.name)
        self.assertTrue(server.listening)
        got = []
        server.received.connect(got.append)
        path = r'C:\Vídeos\clip & friends.mp4'
        # A real second process, like a right-click launch while the app is open.
        code = ('import sys; sys.path.insert(0, sys.argv[1]); import single_instance as s; '
                'print(s.forward(sys.argv[2], sys.argv[3]))')
        child = subprocess.Popen([sys.executable, '-c', code, str(SRC), path, self.name],
                                 stdout=subprocess.PIPE, text=True)
        self.pump(lambda: child.poll() is not None, seconds=10)
        self.assertEqual(child.communicate(timeout=5)[0].strip(), 'True')
        self.assertEqual(got, [path])


    def test_lock_allows_only_one_holder(self):
        import ctypes
        first = single_instance.acquire(self.name)
        self.assertTrue(first)
        self.assertIsNone(single_instance.acquire(self.name))
        self.assertIsNone(single_instance.acquire(self.name, wait=.5))
        ctypes.windll.kernel32.CloseHandle(first)
        second = single_instance.acquire(self.name)
        self.assertTrue(second)
        ctypes.windll.kernel32.CloseHandle(second)


if __name__ == '__main__':
    unittest.main()
