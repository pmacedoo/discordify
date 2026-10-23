import os
import re
import sys
import math
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal, QTimer, QPointF
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QLabel, QPushButton,
                               QProgressBar, QFileDialog, QSystemTrayIcon, QMenu, QSizePolicy, QHBoxLayout)

LIMIT = 19_000_000
ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent))
FFMPEG = str(ROOT / 'ffmpeg.exe')


class Cancelled(Exception):
    pass


def run(args, cancel, progress=None, duration=1):
    if cancel.is_set():
        raise Cancelled()
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([FFMPEG, '-hide_banner', '-nostdin', *args],
                                   stdout=subprocess.PIPE, stderr=errors,
                                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        def watch():
            while process.poll() is None:
                if cancel.wait(.15):
                    if process.poll() is None:
                        process.terminate()
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
        if process.returncode:
            raise RuntimeError(detail[-1800:] or 'O FFmpeg não conseguiu processar o vídeo.')
        return detail


def inspect(path, cancel):
    # Decode no frames: ask the demuxer for its native duration and stream list.
    info = run(['-i', str(path), '-map', '0:v:0', '-t', '0', '-f', 'null', '-'], cancel)
    match = re.search(r'Duration: (\d+):(\d+):(\d+(?:\.\d+)?)', info)
    if not match:
        raise RuntimeError('Não foi possível determinar a duração deste vídeo.')
    h, m, s = map(float, match.groups())
    duration = h * 3600 + m * 60 + s
    if duration <= 0 or not math.isfinite(duration):
        raise RuntimeError('O vídeo tem duração inválida.')
    return duration, bool(re.search(r'Stream #.*Audio:', info))


def compress(source, cancel, progress):
    source = Path(source)
    duration, has_audio = inspect(source, cancel)
    if source.suffix.lower() == '.mp4' and source.stat().st_size <= LIMIT:
        return source
    audio = min(96000, max(16000, int(LIMIT * 8 / duration * .12))) if has_audio else 0
    bitrate = min(25_000_000, int(LIMIT * .94 * 8 / duration) - audio)
    if bitrate < 12000:
        raise RuntimeError('Vídeo longo demais para caber em 19 MB com qualidade utilizável.')
    with tempfile.TemporaryDirectory(prefix='discordify-') as folder:
        temp = Path(folder) / 'video.mp4'
        for attempt in range(4):
            height = 1080 if bitrate >= 2200000 else 720 if bitrate >= 900000 else 480 if bitrate >= 350000 else 360 if bitrate >= 150000 else 240
            filters = f'scale=w=trunc(iw*min(1\\,{height}/ih)/2)*2:h=trunc(ih*min(1\\,{height}/ih)/2)*2'
            common = ['-y', '-i', str(source), '-map', '0:v:0', '-vf', filters,
                      '-c:v', 'libx264', '-preset', 'fast', '-b:v', str(bitrate),
                      '-pix_fmt', 'yuv420p', '-fps_mode', 'passthrough', '-passlogfile', str(Path(folder) / 'pass')]
            progress(0, f'Analisando compressão · tentativa {attempt + 1}')
            run([*common, '-pass', '1', '-an', '-progress', 'pipe:1', '-f', 'null', os.devnull],
                cancel, lambda p: progress(int(p * 45), 'Analisando vídeo…'), duration)
            audio_args = ['-map', '0:a:0?', '-c:a', 'aac', '-b:a', str(audio), '-ac', '2'] if has_audio else ['-an']
            run([*common, '-pass', '2', *audio_args, '-movflags', '+faststart', '-progress', 'pipe:1', str(temp)],
                cancel, lambda p: progress(45 + int(p * 54), 'Comprimindo vídeo…'), duration)
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
                raise RuntimeError('Não foi possível escolher um nome para o arquivo de saída.')
            bitrate = int(bitrate * LIMIT / size * .90)
            if bitrate < 12000:
                break
        raise RuntimeError('Não foi possível atingir 19 MB. Nenhum arquivo incompleto foi salvo.')


class Worker(QThread):
    update = Signal(int, str)
    done = Signal(str)
    failed = Signal(str)

    def __init__(self, path):
        super().__init__()
        self.path = path
        self.cancel = threading.Event()

    def run(self):
        try:
            result = compress(self.path, self.cancel, self.update.emit)
            self.done.emit(str(result))
        except Cancelled:
            self.failed.emit('Compressão cancelada.')
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
    def __init__(self, choose):
        super().__init__()
        self.choose = choose
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
        picture.setPixmap(pix)
        picture.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(picture)
        self.title = QLabel('Arraste seu vídeo aqui')
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title.setStyleSheet('font-size:19px;font-weight:600;color:#efeff5;')
        layout.addWidget(self.title)
        subtitle = QLabel('Até 19 MB · FPS original')
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        subtitle.setStyleSheet('font-size:12px;color:#92929f;')
        layout.addWidget(subtitle)
        layout.addStretch()
        for child in [picture, self.title, subtitle]:
            child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def setText(self, text):
        self.title.setText(text)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.choose()
        super().mousePressEvent(event)


class Window(QWidget):
    def __init__(self):
        super().__init__()
        self.worker = None
        self.result = None
        self.exiting = False
        self.setWindowTitle('Discordify')
        self.setWindowIcon(icon())
        self.setAcceptDrops(True)
        self.setFixedSize(520, 380)
        self.setStyleSheet('QWidget {background:#1c1d22;color:#e7e7ec;font:13px "Segoe UI";} QWidget#dropArea {background:#22232b;border:1px dashed #8274ef;border-radius:14px;} QWidget#dropArea:hover {background:#262631;border-color:#aaa0ff;} QLabel {background:transparent;border:0;} QProgressBar {border:0;background:#34353e;border-radius:3px;} QProgressBar::chunk {background:#8274fa;border-radius:3px;} QMenu {background:#202024;} QMenu::item {padding:8px 18px;} QMenu::item:selected {background:#393442;}')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 18, 22, 22)
        layout.setSpacing(18)
        header = QHBoxLayout()
        logo = QLabel()
        logo.setPixmap(self.windowIcon().pixmap(30, 30))
        header.addWidget(logo)
        title = QLabel('Discordify')
        title.setStyleSheet('font-size:19px;font-weight:600;color:#f3f3f6;')
        header.addWidget(title)
        header.addStretch()
        credit = QLabel('by pedromacedo')
        credit.setStyleSheet('font-size:11px;color:#92929f;')
        header.addWidget(credit)
        layout.addLayout(header)
        self.label = DropArea(self.pick)
        layout.addWidget(self.label, 1)
        self.status = QLabel('')
        self.status.setStyleSheet('font-size:11px;color:#a6a6b2;')
        layout.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(6)
        self.bar.setTextVisible(False)
        self.bar.setValue(0)
        layout.addWidget(self.bar)
        self.tray = QSystemTrayIcon(self.windowIcon(), self)
        self.tray.setToolTip('Discordify · vídeos de até 19 MB')
        menu = QMenu()
        menu.addAction('Abrir Discordify', self.reveal)
        menu.addAction('Escolher vídeo…', self.pick)
        self.folder_action = menu.addAction('Abrir pasta do resultado', self.open_result)
        self.folder_action.setEnabled(False)
        self.cancel_action = menu.addAction('Cancelar compressão', self.cancel_work)
        self.cancel_action.setEnabled(False)
        menu.addSeparator()
        menu.addAction('Sair', self.quit)
        self.tray.setContextMenu(menu)
        self.menu = menu
        self.tray.activated.connect(lambda reason: self.reveal() if reason in (QSystemTrayIcon.ActivationReason.Trigger, QSystemTrayIcon.ActivationReason.DoubleClick) else None)
        self.tray.messageClicked.connect(self.open_result)
        self.tray.show()

    def reveal(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def pick(self):
        self.reveal()
        if self.worker:
            return
        path, _ = QFileDialog.getOpenFileName(self, 'Escolher vídeo', '', 'Vídeos (*.mp4 *.mov *.mkv *.avi *.webm *.m4v *.wmv *.mpeg *.mpg *.ts);;Todos os arquivos (*)')
        if path:
            self.start(path)

    def dragEnterEvent(self, event):
        urls = event.mimeData().urls()
        if not self.worker and len(urls) == 1 and urls[0].isLocalFile() and Path(urls[0].toLocalFile()).is_file():
            event.acceptProposedAction()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        if not self.worker and len(urls) == 1 and urls[0].isLocalFile():
            event.acceptProposedAction()
            self.start(urls[0].toLocalFile())

    def start(self, path):
        if self.worker:
            return
        self.result = None
        self.folder_action.setEnabled(False)
        self.cancel_action.setEnabled(True)
        self.label.setText('Preparando…')
        self.status.setText('')
        self.bar.setValue(0)
        self.worker = Worker(path)
        self.worker.update.connect(self.update_progress)
        self.worker.done.connect(self.success)
        self.worker.failed.connect(self.failure)
        self.worker.finished.connect(self.finished)
        self.worker.start()

    def update_progress(self, value, message):
        self.bar.setValue(value)
        self.label.setText('Comprimindo…')
        self.status.setText(f'{message}   {value}%')

    def cancel_work(self):
        if self.worker:
            self.worker.cancel.set()
            self.cancel_action.setEnabled(False)

    def open_result(self):
        if self.result:
            subprocess.Popen(['explorer.exe', '/select,', self.result])

    def success(self, path):
        self.result = path
        self.bar.setValue(100)
        size = Path(path).stat().st_size / 1_000_000
        self.label.setText(f'Pronto. {size:.2f} MB\n\nSolte outro vídeo aqui')
        self.status.setText('Salvo na pasta do original. Pode soltar o próximo.')
        self.label.setToolTip(path)
        self.folder_action.setEnabled(True)
        self.tray.showMessage('Discordify', 'Vídeo pronto. Clique para abrir a pasta.')

    def failure(self, message):
        self.label.setText('Não foi possível comprimir.\nClique para tentar outro vídeo.')
        self.status.setText(message.splitlines()[-1][:75])
        self.label.setToolTip(message)
        self.tray.showMessage('Discordify', message[:220], QSystemTrayIcon.MessageIcon.Warning)
        self.bar.setValue(0)

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
            self.label.setText('Cancelando e encerrando…')
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
