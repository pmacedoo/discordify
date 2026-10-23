"""Optional, per-user Explorer verb. Does not change default file associations."""
import ctypes
import filecmp
import os
from pathlib import Path
import shutil
import sys
import tempfile

if os.name == 'nt':
    import winreg

VERSION = '1.2.0'
OWNER = 'pedromacedo/discordify'
CLASSES = r'Software\Classes\SystemFileAssociations'
EXTENSIONS = ('.mp4', '.mov', '.mkv', '.avi', '.webm', '.m4v', '.wmv', '.mpeg', '.mpg', '.ts')
VERB = 'Discordify.Compress'


def key_path(extension, root=CLASSES):
    return rf'{root}\{extension}\shell\{VERB}'


def values(path):
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, path) as key:
            result = {}
            index = 0
            while True:
                try:
                    name, data, kind = winreg.EnumValue(key, index)
                    result[name] = (data, kind)
                    index += 1
                except OSError as exc:
                    if exc.winerror == 259:
                        return result
                    raise
    except FileNotFoundError:
        return None


def write_values(path, data):
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        for name, (value, kind) in data.items():
            winreg.SetValueEx(key, name, 0, kind, value)


def owned(path):
    current = values(path)
    if current is not None and current.get('DiscordifyOwner', ('',))[0] != OWNER:
        raise OSError('An existing context-menu entry belongs to another application.')
    return current


def restore(path, snapshot):
    if snapshot is None:
        try:
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, path)
        except FileNotFoundError:
            pass
        return
    current = values(path) or {}
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
        for name in current.keys() - snapshot.keys():
            winreg.DeleteValue(key, name)
    write_values(path, snapshot)


def notify_shell():
    # SHCNE_ASSOCCHANGED: refresh Explorer's association cache, without restarting Explorer.
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)


def register(executable, root=CLASSES):
    executable = Path(executable).resolve(strict=True)
    paths = [key_path(extension, root) for extension in EXTENSIONS]
    snapshots = [(path, owned(path), values(path + r'\command')) for path in paths]
    touched = []
    try:
        for path, old, command in snapshots:
            touched.append((path, old, command))
            write_values(path, {'': ('Compress with Discordify', winreg.REG_SZ),
                'Icon': (f'"{executable}",0', winreg.REG_SZ),
                'MultiSelectModel': ('Single', winreg.REG_SZ),
                'DiscordifyOwner': (OWNER, winreg.REG_SZ)})
            write_values(path + r'\command', {'': (f'"{executable}" "%1"', winreg.REG_SZ)})
    except OSError:
        for path, old, command in reversed(touched):
            restore(path + r'\command', command)
            restore(path, old)
        raise
    notify_shell()


def remove(root=CLASSES):
    paths = [key_path(extension, root) for extension in EXTENSIONS]
    for path in paths:
        owned(path)  # Check all entries before removing any of them.
    for path in paths:
        if values(path) is None:
            continue
        for target in (path + r'\command', path):
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, target)
            except FileNotFoundError:
                pass
    notify_shell()


def installed(root=CLASSES):
    return all((values(key_path(ext, root)) or {}).get('DiscordifyOwner', ('',))[0] == OWNER
               for ext in EXTENSIONS)


def install(executable, app_dir=None):
    source = Path(executable).resolve(strict=True)
    destination = (Path(app_dir) if app_dir else Path(os.environ['LOCALAPPDATA']) / 'Discordify' / 'app' / VERSION) / 'Discordify.exe'
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source != destination.resolve() and (not destination.exists() or not filecmp.cmp(source, destination, shallow=False)):
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False, suffix='.tmp') as file:
            temporary = Path(file.name)
        try:
            shutil.copyfile(source, temporary)
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    register(destination)
    return destination


def enable():
    if not getattr(sys, 'frozen', False):
        raise OSError('Build the executable before adding the right-click menu.')
    return install(sys.executable)
