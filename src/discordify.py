import os
import re
import sys
import math
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from encoding import CPU, choose, options, forget
import shell_integration

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPointF, QMimeData, QUrl, QEvent
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPainterPath, QPen, QPolygonF, QDrag
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
                               QProgressBar, QFileDialog, QSystemTrayIcon, QMenu, QSizePolicy, QHBoxLayout)

LIMIT = 19_000_000
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
FFMPEG = str(ROOT / 'ffmpeg.exe')


class Cancelled(Exception):
    pass


def run(args, cancel, progress=None, duration=1, timeout=None):
    if cancel.is_set():
        raise Cancelled()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([FFMPEG, '-hide_banner', '-nostdin', *args],
                                   stdout=subprocess.PIPE, stderr=errors,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        timed_out = threading.Event()
        started = time.monotonic()
        def watch():
            while process.poll() is None:
                if cancel.wait(.05) or (timeout and time.monotonic() - started > timeout):
                    if not cancel.is_set():
                        timed_out.set()
                    if process.poll() is None:
                        try:
                            process.kill()
                        except OSError:
                            pass
                    return
        monitor = threading.Thread(target=watch, daemon=True)
        monitor.start()
        for raw in process.stdout:
            line = raw.decode('utf-8', 'replace').strip()
            if progress and line.startswith('out_time_us='):
                try:
                    progress(min(1, int(line.split('=')[1]) / 1_000_000 / duration))
                except ValueError:
                    pass
        process.wait()
        monitor.join()
        process.stdout.close()
        errors.seek(0)
        detail = errors.read().decode('utf-8', 'replace')
        if cancel.is_set():
            raise Cancelled()
        if timed_out.is_set():
            raise RuntimeError('Encoder test timed out.')
        if process.returncode:
            raise RuntimeError(detail[-1800:] or 'FFmpeg could not process this video.')
        return detail


def inspect(path, cancel, details=False):
    # Decode no frames: ask the demuxer for its native duration and stream list.
    info = run(['-i', str(path), '-map', '0:v:0', '-t', '0', '-f', 'null', '-'], cancel)
    match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', info)
    if not match:
        raise RuntimeError('Could not determine the video duration.')
    h, m, s = map(float, match.groups())
    duration = h * 3600 + m * 60 + s
    if duration <= 0 or not math.isfinite(duration):
        raise RuntimeError('Invalid video duration.')
    result = (duration, bool(re.search(r'Stream #.*Audio:', info)))
    return (*result, info) if details else result


def compress(source, cancel, progress, selector=choose, encode=run):
    source = Path(source)
    duration, has_audio, info = inspect(source, cancel, details=True)
    if source.suffix.lower() == '.mp4' and source.stat().st_size <= LIMIT:
        return source
    audio = min(96000, max(16000, int(LIMIT * 8 / duration * .12))) if has_audio else 0
    bitrate = min(25_000_000, int(LIMIT * .94 * 8 / duration) - audio)
    if bitrate < 12000:
        raise RuntimeError('This video is too long to fit with usable quality.')
    with tempfile.TemporaryDirectory(prefix='discordify-') as folder:
        temp = Path(folder) / 'video.mp4'
        height = 1080 if bitrate >= 2200000 else 720 if bitrate >= 900000 else 480 if bitrate >= 350000 else 360 if bitrate >= 150000 else 240
        filters = f'scale=w=trunc(iw*min(1\\,{height}/ih)/2)*2:h=trunc(ih*min(1\\,{height}/ih)/2)*2'
        try:
            encoder, key = selector(source, duration, bitrate, filters, info, FFMPEG, run, cancel, progress)
        except RuntimeError:
            encoder, key = CPU, None
        for attempt in range(5):
            height = 1080 if bitrate >= 2200000 else 720 if bitrate >= 900000 else 480 if bitrate >= 350000 else 360 if bitrate >= 150000 else 240
            filters = f'scale=w=trunc(iw*min(1\\,{height}/ih)/2)*2:h=trunc(ih*min(1\\,{height}/ih)/2)*2'
            common = ['-y', '-i', str(source), '-map', '0:v:0', '-vf', filters,
                      *options(encoder, bitrate)]
            progress(0, 'Compressing…')
            audio_args = ['-map', '0:a:0?', '-c:a', 'aac', '-b:a', str(audio), '-ac', '2'] if has_audio else ['-an']
            try:
                encode([*common, *audio_args, '-movflags', '+faststart', '-progress', 'pipe:1', str(temp)],
                    cancel, lambda p: progress(int(p * 99), 'Compressing…'), duration)
            except RuntimeError:
                if encoder == CPU:
                    raise
                encoder = CPU
                if key:
                    forget(key)
                continue
            size = temp.stat().st_size
            if 0 < size <= LIMIT:
                # Exclusive creation prevents overwriting an existing file, including a concurrent output.
                for n in range(10000):
                    suffix = '' if n == 0 else f' ({n})'
                    destination = source.with_name(f'{source.stem}_discordify{suffix}.mp4')
                    try:
                        output = destination.open('xb')
                    except FileExistsError:
                        continue
                    try:
                        with output, temp.open('rb') as inp:
                            shutil.copyfileobj(inp, output)
                    except BaseException:
                        destination.unlink(missing_ok=True)
                        raise
                    return destination
                raise RuntimeError('Could not choose an output filename.')
            bitrate = int(bitrate * LIMIT / size * .90)
            if bitrate < 12000:
                break
        raise RuntimeError('Could not meet the size limit. No incomplete file was saved.')


class Worker(QThread):
    update = Signal(int, str)
    done = Signal(str, bytes)
    failed = Signal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.cancel = threading.Event()

    def run(self):
        try:
            result = compress(self.path, self.cancel, self.update.emit)
            preview = b''
            try:
                if not self.cancel.is_set():
                    with tempfile.TemporaryDirectory(prefix='discordify-preview-') as folder:
                        image = Path(folder) / 'preview.png'
                        run(['-y', '-i', str(result), '-an', '-vf',
                             'thumbnail=12,scale=160:90:force_original_aspect_ratio=decrease',
                             '-frames:v', '1', str(image)], self.cancel, timeout=5)
                        if image.exists():
                            preview = image.read_bytes()
            except (RuntimeError, OSError, Cancelled):
                pass  # A thumbnail failure must not invalidate a completed video.
            self.done.emit(str(result), preview)
        except Cancelled:
            self.failed.emit('Compression cancelled.')
        except Exception as exc:
            self.failed.emit(str(exc))


def icon():
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor('#7765ff'))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(2, 2, 60, 60, 18, 18)
    p.setPen(QColor('white'))
    font = p.font()
    font.setBold(True)
    font.setPixelSize(40)
    p.setFont(font)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, 'd')
    p.end()
    return QIcon(pix)


class DropArea(QWidget):
    fileMissing = Signal()

    def __init__(self, choose):
        super().__init__()
        self.choose = choose
        self.output_path = None
        self.press_position = None
        self.drag_active = False
        self.setObjectName('dropArea')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addStretch()
        pix = QPixmap(64, 72)
        pix.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pix)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#a49aff'), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        path = QPainterPath()
        path.moveTo(13, 5)
        path.lineTo(39, 5)
        path.lineTo(55, 21)
        path.lineTo(55, 63)
        path.quadTo(55, 67, 51, 67)
        path.lineTo(13, 67)
        path.quadTo(9, 67, 9, 63)
        path.lineTo(9, 9)
        path.quadTo(9, 5, 13, 5)
        painter.drawPath(path)
        painter.drawPolyline(QPolygonF([QPointF(39, 5), QPointF(39, 21), QPointF(55, 21)]))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor('#a49aff'))
        painter.drawPolygon(QPolygonF([QPointF(26, 32), QPointF(26, 52), QPointF(43, 42)]))
        painter.end()
        picture = QLabel()
        self.picture = picture
        self.default_picture = pix.scaled(40, 45, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        self.preview_pixmap = None
        picture.setPixmap(self.default_picture)
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(picture)
        self.title = QLabel('Drop a video')
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet('font-size:16px;font-weight:600;color:#efeff5;')
        layout.addWidget(self.title)
        layout.addStretch()
        for child in [picture, self.title]:
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def setText(self, text):
        self.title.setText(text)

    def set_output(self, path, preview=b''):
        self.output_path = Path(path).resolve() if path else None
        self.press_position = None
        image = QPixmap()
        self.preview_pixmap = image if path and preview and image.loadFromData(preview) else None
        self.picture.setPixmap(self.preview_pixmap.scaled(160, 90, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation) if self.preview_pixmap else self.default_picture)
        self.setCursor(Qt.CursorShape.OpenHandCursor if path else Qt.CursorShape.PointingHandCursor)

    def make_drag(self):
        if not self.output_path or not self.output_path.is_file():
            self.fileMissing.emit()
            return None
        drag = QDrag(self)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(self.output_path))])
        drag.setMimeData(mime)
        image = self.preview_pixmap.scaled(128, 72, Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation) if self.preview_pixmap else self.window().windowIcon().pixmap(32, 32)
        drag.setPixmap(image)
        drag.setHotSpot(image.rect().center())
        return drag

    def execute_drag(self, drag):
        return drag.exec(Qt.DropAction.CopyAction, Qt.DropAction.CopyAction)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_position = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if (self.press_position is not None and self.output_path and not self.drag_active
                and self.isVisible() and self.isEnabled()
                and event.buttons() & Qt.MouseButton.LeftButton
                and (event.position().toPoint() - self.press_position).manhattanLength() >= QApplication.startDragDistance()):
            self.press_position = None
            drag = self.make_drag()
            if drag is not None:
                self.drag_active = True
                try:
                    self.execute_drag(drag)
                finally:
                    self.drag_active = False
                    self.press_position = None
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        start = self.press_position
        self.press_position = None
        if (event.button() == Qt.MouseButton.LeftButton and start is not None
                and not self.output_path and self.rect().contains(event.position().toPoint())
                and (event.position().toPoint() - start).manhattanLength() < QApplication.startDragDistance()):
            self.choose()
        super().mouseReleaseEvent(event)

    def event(self, event):
        if event.type() in (QEvent.Type.Hide, QEvent.Type.WindowDeactivate, QEvent.Type.UngrabMouse):
            self.press_position = None
        return super().event(event)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.result = None
        self.exiting = False
        self.setWindowTitle('Discordify')
        self.setWindowIcon(icon())
        self.setAcceptDrops(True)
        self.setFixedSize(400, 280)
        self.setStyleSheet('QWidget {background:#1c1d22;color:#e7e7ec;font:13px "Segoe UI";} QWidget#dropArea {background:#22232b;border:1px dashed #8274ef;border-radius:14px;} QWidget#dropArea:hover {background:#262631;border-color:#aaa0ff;} QLabel {background:transparent;border:0;} QProgressBar {border:0;background:#34353e;border-radius:3px;} QProgressBar::chunk {background:#8274fa;border-radius:3px;} QMenu {background:#202024;} QMenu::item {padding:8px 18px;} QMenu::item:selected {background:#393442;}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(12)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(24, 24))
        header.addWidget(logo)
        title = QLabel('Discordify')
        title.setStyleSheet('font-size:16px;font-weight:600;color:#f3f3f6;')
        header.addWidget(title)
        header.addStretch()
        credit = QLabel('by pedromacedo')
        credit.setStyleSheet('font-size:11px;color:#92929f;')
        header.addWidget(credit)
        layout.addLayout(header)
        self.label = DropArea(self.pick)
        self.label.fileMissing.connect(self.clear_result)
        layout.addWidget(self.label, 1)
        self.status = QLabel('')
        self.status.linkActivated.connect(lambda _: self.pick())
        self.status.setStyleSheet('font-size:11px;color:#a6a6b2;')
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.status.hide()
        self.bar.hide()
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('Discordify')
        menu = QMenu()
        menu.addAction('Open Discordify', self.reveal)
        menu.addAction('Choose video…', self.pick)
        self.folder_action = menu.addAction('Open output folder', self.open_result)
        self.folder_action.setEnabled(False)
        self.cancel_action = menu.addAction('Cancel compression', self.cancel_work)
        self.cancel_action.setEnabled(False)
        menu.addSeparator()
        menu.addAction('Add to right-click menu', self.add_context_menu)
        menu.addAction('Remove from right-click menu', self.remove_context_menu)
        menu.addSeparator()
        menu.addAction('Quit', self.quit)
        self.tray.setContextMenu(menu)
        self.menu = menu
        self.tray.activated.connect(lambda reason: self.reveal() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(self.open_result)
        self.tray.show()

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def add_context_menu(self):
        try:
            shell_integration.enable()
            self.tray.showMessage('Discordify', 'Right-click menu added. On Windows 11, check “Show more options.”')
        except OSError as exc:
            self.tray.showMessage('Discordify', str(exc), QSystemTrayIcon.MessageIcon.Warning)

    def remove_context_menu(self):
        try:
            shell_integration.remove()
            self.tray.showMessage('Discordify', 'Right-click menu removed.')
        except OSError as exc:
            self.tray.showMessage('Discordify', str(exc), QSystemTrayIcon.MessageIcon.Warning)

    def pick(self):
        self.reveal()
        if self.worker:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Choose video', '', 'Videos (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.mpeg *.mpg *.ts);;All files (*)')
        if path:
            self.start(path)

    def dragEnterEvent(self, event):
        if event.source() is self.label:
            event.ignore()
            return
        urls = event.mimeData().urls()
        if not self.worker and len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).is_file():
            event.acceptProposedAction()

    def dropEvent(self, event):
        if event.source() is self.label:
            event.ignore()
            return
        urls = event.mimeData().urls()
        if not self.worker and len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.start(urls[0].toLocalFile())

    def start(self, path):
        if self.worker:
            return
        self.result = None
        self.label.set_output(None)
        self.label.setToolTip('')
        self.folder_action.setEnabled(False)
        self.cancel_action.setEnabled(True)
        self.label.setText('Getting ready…')
        self.status.setText('')
        self.status.show()
        self.bar.show()
        self.bar.setValue(0)
        self.worker = Worker(path)
        self.worker.update.connect(self.update_progress)
        self.worker.done.connect(self.success)
        self.worker.failed.connect(self.failure)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def update_progress(self, value, message):
        self.bar.setValue(value)
        self.label.setText('Getting ready…' if message.startswith('Finding') else 'Compressing…')
        self.status.setText('' if message.startswith('Finding') else f'{value}%')

    def cancel_work(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_action.setEnabled(False)

    def open_result(self):
        if self.result:
            subprocess.Popen(['explorer.exe', '/select,', self.result])

    def clear_result(self):
        self.result = None
        self.label.set_output(None)
        self.label.setText('Drop a video')
        self.label.setToolTip('')
        self.folder_action.setEnabled(False)
        self.status.setText('The output file is no longer available.')
        self.status.show()
        self.bar.hide()

    def success(self, path, preview=b''):
        self.result = path
        self.label.set_output(path, preview)
        self.bar.setValue(100)
        self.label.setText('Drag your video out')
        self.status.setText('<a href="choose" style="color:#a6a6b2;text-decoration:none;">Choose another video</a>')
        self.label.setToolTip(path)
        self.folder_action.setEnabled(True)
        self.tray.showMessage('Discordify', 'Your video is ready. Click to open its folder.')

    def failure(self, message):
        cancelled = message == 'Compression cancelled.'
        self.label.setText('Cancelled' if cancelled else "Couldn't compress")
        self.status.setText('Drop another video to try again.')
        self.label.setToolTip(message)
        if not cancelled:
            self.tray.showMessage('Discordify', message[:220], QSystemTrayIcon.MessageIcon.Warning)
        self.bar.setValue(0)
        self.bar.hide()

    def finished(self):
        self.worker.deleteLater()
        self.worker = None
        self.cancel_action.setEnabled(False)
        if self.exiting:
            QApplication.quit()

    def quit(self):
        if self.worker:
            self.exiting = True
            self.worker.cancel.set()
            self.label.setText('Cancelling…')
        else:
            QApplication.quit()

    def closeEvent(self, event):
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.hide()
            event.ignore()
        else:
            event.ignore()
            self.quit()


if __name__ == '__main__':
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName('Discordify')
    window = Window()
    window.show()
    # Windows also passes dropped files as arguments when dropped onto the executable.
    if len(sys.argv) == 2 and Path(sys.argv[1]).is_file():
        QTimer.singleShot(0, lambda: window.start(sys.argv[1]))
    sys.exit(app.exec())
