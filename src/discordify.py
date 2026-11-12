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
import updater
import single_instance
import copies

from PySide6.QtCore import QSettings, Qt, QThread, Signal, QTimer, QPointF, QMimeData, QUrl, QEvent
from PySide6.QtGui import QDesktopServices, QLinearGradient, QColor, QIcon, QPainter, QPixmap, QPainterPath, QPen, QPolygonF, QDrag
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
                               QProgressBar, QFileDialog, QSystemTrayIcon, QMenu, QSizePolicy, QHBoxLayout,
                               QMessageBox, QButtonGroup, QGraphicsDropShadowEffect)

# Byte targets with headroom under Discord's 10 MB and 20 MB-class limits.
LIMITS = {10: 9_500_000, 20: 19_000_000}
LIMIT = LIMITS[20]
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
FFMPEG = str(ROOT / 'ffmpeg.exe')


STYLE = """
QWidget { background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #1d1c26, stop:1 #15151b);
          color: #e9e9f0; font: 13px "Segoe UI Variable Text", "Segoe UI"; }
QLabel { background: transparent; border: 0; }
QWidget#dropArea { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #262536, stop:1 #1e1e29);
                   border: 1px solid #36344d; border-radius: 18px; }
QWidget#dropArea:hover { background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 #2d2b42, stop:1 #222131);
                         border-color: #8c7dff; }
QWidget#sizeToggle { background: #121218; border: 1px solid #2c2b3a; border-radius: 15px; }
QPushButton#sizeOption { background: transparent; min-height: 24px; color: #8d8da0; border: 0; border-radius: 12px;
                         padding: 0; font-size: 11px; font-weight: 600; }
QPushButton#sizeOption:hover { color: #d9d9e6; }
QPushButton#sizeOption:checked { color: white; background: qlineargradient(x1:0, y1:0, x2:1, y2:1,
                                 stop:0 #8f7fff, stop:1 #6450f5); }
QPushButton#sizeOption:disabled { color: #5c5c6c; }
QPushButton#sizeOption:checked:disabled { color: #d0cce8; background: #4a4472; }
QProgressBar { border: 0; background: #2a2a36; border-radius: 2px; }
QProgressBar::chunk { border-radius: 2px; background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                      stop:0 #6f5cff, stop:1 #b2a6ff); }
QMenu { background: #1d1d25; border: 1px solid #2f2e3d; padding: 4px; }
QMenu::item { padding: 8px 18px; border-radius: 6px; }
QMenu::item:selected { background: #34314a; }
QMenu::separator { height: 1px; background: #2f2e3d; margin: 4px 8px; }
QToolTip { background: #24232f; color: #e9e9f0; border: 1px solid #3a3850; padding: 4px 6px; }
"""


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


def compress(source, cancel, progress, selector=choose, encode=run, limit=LIMIT):
    source = Path(source)
    duration, has_audio, info = inspect(source, cancel, details=True)
    if source.suffix.lower() == '.mp4' and source.stat().st_size <= limit:
        return source
    audio = min(96000, max(16000, int(limit * 8 / duration * .12))) if has_audio else 0
    bitrate = min(25_000_000, int(limit * .94 * 8 / duration) - audio)
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
            if 0 < size <= limit:
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
            bitrate = int(bitrate * limit / size * .90)
            if bitrate < 12000:
                break
        raise RuntimeError('Could not meet the size limit. No incomplete file was saved.')


class Background(QThread):
    """Runs one function off the UI thread; update errors must never break the app."""
    ok = Signal(object)
    failed = Signal(str)

    def __init__(self, function, *args):
        super().__init__()
        self.function, self.args = function, args

    def run(self):
        try:
            self.ok.emit(self.function(*self.args))
        except Exception as exc:
            self.failed.emit(str(exc))


class Worker(QThread):
    update = Signal(int, str)
    done = Signal(str, bytes)
    failed = Signal(str)

    def __init__(self, path, limit=LIMIT):
        super().__init__()
        self.path = path
        self.limit = limit
        self.cancel = threading.Event()

    def run(self):
        try:
            result = compress(self.path, self.cancel, self.update.emit, limit=self.limit)
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
    gradient = QLinearGradient(0, 0, 64, 64)
    gradient.setColorAt(0, QColor('#9b8cff'))
    gradient.setColorAt(1, QColor('#5b48f2'))
    p.setBrush(gradient)
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
        painter.setPen(QPen(QColor('#b3a9ff'), 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
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
        painter.setBrush(QColor('#b3a9ff'))
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
        self.title.setStyleSheet('font-size:17px;font-weight:600;color:#f4f4f8;letter-spacing:.2px;')
        layout.addWidget(self.title)
        self.hint = QLabel('or click to browse')
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setStyleSheet('font-size:12px;color:#8d8da0;')
        layout.addWidget(self.hint)
        layout.addStretch()
        for child in [picture, self.title, self.hint]:
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def setText(self, text):
        self.title.setText(text)
        self.hint.setVisible(text == 'Drop a video')

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
        self.update_tasks = set()
        self.pending_update = None
        self.setWindowTitle('Discordify')
        self.setWindowIcon(icon())
        self.setAcceptDrops(True)
        self.setFixedSize(420, 320)
        self.settings = QSettings('pedromacedo', 'Discordify')
        self.size_mb = 10 if self.settings.value('size_mb', 20, int) == 10 else 20
        self.setStyleSheet(STYLE)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 20, 24, 22)
        layout.setSpacing(14)
        header = QHBoxLayout()
        header.setSpacing(10)
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(28, 28))
        header.addWidget(logo)
        names = QVBoxLayout()
        names.setSpacing(0)
        title = QLabel('Discordify')
        title.setStyleSheet('font-size:15px;font-weight:700;color:#f6f6fa;')
        names.addWidget(title)
        credit = QLabel('by pedromacedo')
        credit.setStyleSheet('font-size:10px;color:#7d7d90;')
        names.addWidget(credit)
        header.addLayout(names)
        header.addStretch()
        toggle = QWidget()
        toggle.setObjectName('sizeToggle')
        toggle.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        toggle.setToolTip('Target file size. 10 MB fits Discord without Nitro.')
        row = QHBoxLayout(toggle)
        row.setContentsMargins(3, 3, 3, 3)
        row.setSpacing(2)
        self.size_group = QButtonGroup(self)
        for mb in (10, 20):
            button = QPushButton(f'{mb} MB')
            button.setObjectName('sizeOption')
            button.setCheckable(True)
            button.setFixedSize(54, 24)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setChecked(mb == self.size_mb)
            self.size_group.addButton(button, mb)
            row.addWidget(button)
        self.size_group.idClicked.connect(self.set_size)
        header.addWidget(toggle)
        layout.addLayout(header)
        self.label = DropArea(self.pick)
        glow = QGraphicsDropShadowEffect(self.label)
        glow.setBlurRadius(36)
        glow.setOffset(0, 8)
        glow.setColor(QColor(0, 0, 0, 110))
        self.label.setGraphicsEffect(glow)
        self.label.fileMissing.connect(self.clear_result)
        layout.addWidget(self.label, 1)
        self.status = QLabel('')
        self.status.linkActivated.connect(lambda _: self.pick())
        self.status.setStyleSheet('font-size:11px;color:#a6a6b2;')
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(5)
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
        if updater.enabled():
            self.tidy_up()
            # Only once, at launch.
            QTimer.singleShot(3_000, self.check_updates)

    def run_background(self, function, *args, ok=None, failed=None):
        task = Background(function, *args)
        if ok:
            task.ok.connect(ok)
        if failed:
            task.failed.connect(failed)
        task.finished.connect(lambda: (self.update_tasks.discard(task), task.deleteLater()))
        self.update_tasks.add(task)
        task.start()

    def tidy_up(self):
        """At launch: turn the right-click menu on by default and delete outdated copies of the app."""
        updater.cleanup()
        try:
            if not self.settings.value('context_menu_removed', False, bool) and not shell_integration.installed():
                shell_integration.enable()
            shell_integration.refresh()
        except OSError:
            pass  # The right-click menu is optional (or owned by another app); never block startup on it.
        apps = Path(os.environ['LOCALAPPDATA']) / 'Discordify' / 'app'
        copies.prune(sys.executable, shell_integration.VERSION,
                     lambda: self.settings.value('copies', ''), lambda text: self.settings.setValue('copies', text),
                     keep_under=apps)

    def check_updates(self):
        self.run_background(updater.latest_release, ok=self.offer_update)

    def offer_update(self, release):
        if not release:
            return
        tag, page = release[0], release[3]
        box = QMessageBox(self)
        box.setWindowTitle('Discordify')
        box.setIcon(QMessageBox.Icon.Information)
        box.setText(f'Discordify {tag} is available. You have v{updater.VERSION}.')
        box.setInformativeText(f'Update now? You can review the release first on '
                               f'<a href="{page}">GitHub</a>.')
        box.setTextFormat(Qt.TextFormat.RichText)
        update = box.addButton('Update', QMessageBox.ButtonRole.AcceptRole)
        github = box.addButton('View on GitHub', QMessageBox.ButtonRole.HelpRole)
        box.addButton('Not now', QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(update)
        self.reveal()
        box.exec()
        if box.clickedButton() is github:
            QDesktopServices.openUrl(QUrl(page))
        elif box.clickedButton() is update:
            self.tray.showMessage('Discordify', f'Downloading {tag}…')
            self.run_background(updater.download_next_to_app, release, ok=self.update_ready,
                                failed=lambda message: self.tray.showMessage(
                                    'Discordify', f"Couldn't download the update: {message}"[:220],
                                    QSystemTrayIcon.MessageIcon.Warning))

    def update_ready(self, path):
        self.pending_update = str(path)
        if not self.worker:
            self.apply_update()
        else:
            self.tray.showMessage('Discordify', 'The update will install when this compression finishes.')

    def apply_update(self):
        path, self.pending_update = self.pending_update, None
        try:
            updater.install_and_restart(path)
        except OSError as exc:
            Path(path).unlink(missing_ok=True)
            self.tray.showMessage('Discordify', f"Couldn't install the update: {exc}"[:220], QSystemTrayIcon.MessageIcon.Warning)
            return
        self.tray.hide()
        QApplication.quit()

    def set_toggle_enabled(self, enabled):
        for button in self.size_group.buttons():
            button.setEnabled(enabled)

    def set_size(self, mb):
        self.size_mb = mb
        self.settings.setValue('size_mb', mb)

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def add_context_menu(self):
        try:
            shell_integration.enable()
            self.settings.setValue('context_menu_removed', False)
            self.tray.showMessage('Discordify', 'Right-click menu added. On Windows 11, check “Show more options.”')
        except OSError as exc:
            self.tray.showMessage('Discordify', str(exc), QSystemTrayIcon.MessageIcon.Warning)

    def remove_context_menu(self):
        try:
            shell_integration.remove()
            self.settings.setValue('context_menu_removed', True)
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

    def open_from_another_launch(self, path):
        self.reveal()
        if not path or not Path(path).is_file():
            return
        if self.worker:
            self.tray.showMessage('Discordify', 'Already compressing a video. Try again when it finishes.')
            return
        self.start(path)

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
        self.set_toggle_enabled(False)
        self.worker = Worker(path, LIMITS[self.size_mb])
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
        self.set_toggle_enabled(True)
        self.cancel_action.setEnabled(False)
        if self.exiting:
            QApplication.quit()
        elif self.pending_update:
            self.apply_update()

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
    # Windows also passes dropped files as arguments when dropped onto the executable.
    file = sys.argv[1] if len(sys.argv) == 2 and Path(sys.argv[1]).is_file() else None
    # Only one Discordify at a time. After an update, wait for the old process to finish exiting.
    lock = single_instance.acquire(wait=20 if updater.RESTARTED in sys.argv else 0)
    if not lock:
        message = str(Path(file).resolve()) if file else single_instance.SHOW
        for _ in range(20):  # The running one may still be starting its listener.
            if single_instance.forward(message, timeout=500):
                break
        sys.exit(0)
    window = Window()
    instance = single_instance.Server(parent=window)
    instance.received.connect(window.open_from_another_launch)
    if updater.RESTARTED in sys.argv:
        # Restarted by a background update: stay in the tray like before.
        window.tray.showMessage('Discordify', f'Updated to v{updater.VERSION}.')
    else:
        window.show()
    if file:
        QTimer.singleShot(0, lambda: window.start(file))
    sys.exit(app.exec())
