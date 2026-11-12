"""One Discordify per user: a second launch (e.g. from the right-click menu) hands its file to the first."""
import ctypes
from ctypes import wintypes
import getpass
import os
import time

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

NAME = f'Discordify-{getpass.getuser()}'
ERROR_ALREADY_EXISTS = 183
SHOW = ''  # Message meaning "just bring the window up".


def forward(message, name=NAME, timeout=1500):
    """Send message to a running instance. True if one received it."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(timeout):
        return False
    data = message.encode('utf-8') + b'\n'
    if socket.write(data) != len(data):
        return False
    socket.waitForBytesWritten(timeout)  # Unreliable on Windows pipes; the disconnect below is the receipt.
    # The running instance hangs up once it has read the whole message.
    return (socket.state() == QLocalSocket.LocalSocketState.UnconnectedState
            or socket.waitForDisconnected(timeout))


def acquire(name=NAME, wait=0):
    """Take the per-user Discordify lock. Returns a handle to keep for the process lifetime,
    or None if another Discordify holds it (after waiting up to `wait` seconds)."""
    if os.name != 'nt':
        return True
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
    deadline = time.monotonic() + wait
    while True:
        handle = kernel32.CreateMutexW(None, False, 'Local\\' + name)
        if handle and ctypes.get_last_error() != ERROR_ALREADY_EXISTS:
            return handle  # Released by Windows when this process exits, even on a crash.
        if handle:
            kernel32.CloseHandle(handle)
        if time.monotonic() >= deadline:
            return None
        time.sleep(.25)


class Server(QObject):
    received = Signal(str)

    def __init__(self, name=NAME, parent=None):
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        QLocalServer.removeServer(name)
        self.listening = self.server.listen(name)
        self.server.newConnection.connect(self.accept)

    def accept(self):
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            buffer = bytearray()

            def read(socket=socket, buffer=buffer):
                buffer.extend(bytes(socket.readAll()))
                if b'\n' in buffer:
                    socket.readyRead.disconnect()
                    self.received.emit(buffer.split(b'\n', 1)[0].decode('utf-8', 'replace'))
                    socket.disconnectFromServer()
            socket.readyRead.connect(read)
            socket.disconnected.connect(socket.deleteLater)
            read()
