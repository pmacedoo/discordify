"""Qt mouse-event tests for file export; external Discord drops require manual validation."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from discordify import DropArea


class RecordingArea(DropArea):
    """Capture the OS handoff at an explicit boundary; leave mouse handling unchanged."""
    def __init__(self, choose):
        super().__init__(choose)
        self.exports = []

    def execute_drag(self, drag):
        self.exports.append([Path(url.toLocalFile()) for url in drag.mimeData().urls()])
        return Qt.DropAction.IgnoreAction


class DragTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'vídeo ready.mp4'
        self.path.write_bytes(b'file payload is handed off by URL')
        self.clicks = []
        self.area = RecordingArea(lambda: self.clicks.append(True))
        self.area.resize(360, 220)
        self.area.show()
        self.app.processEvents()

    def tearDown(self):
        self.area.close()
        self.area.deleteLater()
        self.app.processEvents()
        self.temp.cleanup()

    def gesture(self, distance):
        point = QPoint(120, 100)
        QTest.mousePress(self.area, Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseMove(self.area, point + QPoint(distance, 0))
        QTest.mouseRelease(self.area, Qt.MouseButton.LeftButton, pos=point + QPoint(distance, 0))

    def test_idle_click_selects_on_release(self):
        QTest.mousePress(self.area, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        self.assertEqual(self.clicks, [])
        QTest.mouseRelease(self.area, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        self.assertEqual(len(self.clicks), 1)

    def test_threshold_url_and_cancelled_drag_do_not_consume_file(self):
        self.area.set_output(self.path)
        self.gesture(1)
        self.assertEqual(self.area.exports, [])
        self.gesture(QApplication.startDragDistance() + 20)
        self.assertEqual(self.area.exports, [[self.path.resolve()]])
        self.assertEqual(self.clicks, [])
        self.assertTrue(self.path.exists())
        self.assertFalse(self.area.drag_active)
        self.assertIsNone(self.area.press_position)
        self.gesture(QApplication.startDragDistance() + 20)
        self.assertEqual(len(self.area.exports), 2)

    def test_release_and_hide_cancel_pending_gesture(self):
        self.area.set_output(self.path)
        self.gesture(0)
        QTest.mouseMove(self.area, QPoint(200, 100))
        self.assertEqual(self.area.exports, [])
        QTest.mousePress(self.area, Qt.MouseButton.LeftButton, pos=QPoint(100, 100))
        self.area.hide()
        self.area.show()
        self.app.processEvents()
        QTest.mouseMove(self.area, QPoint(200, 100))
        QTest.mouseRelease(self.area, Qt.MouseButton.LeftButton, pos=QPoint(200, 100))
        self.assertEqual(self.area.exports, [])

    def test_missing_file_and_cleared_result_cannot_export(self):
        self.area.set_output(self.path)
        missing = []
        self.area.fileMissing.connect(lambda: missing.append(True))
        self.path.unlink()
        self.gesture(QApplication.startDragDistance() + 20)
        self.assertEqual(len(missing), 1)
        self.assertEqual(self.area.exports, [])
        self.area.set_output(None)
        self.gesture(QApplication.startDragDistance() + 20)
        self.assertEqual(self.area.exports, [])

    def test_handoff_contains_the_real_local_file(self):
        native = DropArea(lambda: None)
        native.show()
        native.set_output(self.path)
        drag = native.make_drag()
        urls = drag.mimeData().urls()
        self.assertEqual(len(urls), 1)
        self.assertTrue(urls[0].isLocalFile())
        self.assertEqual(Path(urls[0].toLocalFile()), self.path.resolve())
        self.assertTrue(drag.mimeData().hasFormat('text/uri-list'))
        self.assertTrue(self.path.exists())
        native.close()
        native.deleteLater()


if __name__ == '__main__':
    unittest.main()
