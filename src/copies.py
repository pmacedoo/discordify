"""Remember where Discordify.exe copies live and delete the outdated ones.

Each launch records its own path. A copy is deleted only if Discordify recorded it itself, it is not
newer than the running version, and its size and timestamp still match what was recorded, so a file
the user replaced or renamed into that spot is never touched.
"""
import json
from pathlib import Path
import re


def _version(text):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', str(text))
    return tuple(map(int, match.groups())) if match else None


def _stamp(path):
    info = path.stat()
    return info.st_size, int(info.st_mtime)


def _same(a, b):
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return str(a).lower() == str(b).lower()


def prune(executable, version, load, save, keep_under=None):
    """load() -> stored JSON text or None; save(text). Returns the paths that were deleted."""
    executable = Path(executable)
    try:
        entries = json.loads(load() or '[]')
        entries = [e for e in entries if isinstance(e, dict) and isinstance(e.get('path'), str)]
    except (ValueError, TypeError):
        entries = []
    mine = _version(version)
    deleted, kept = [], []
    for entry in entries:
        path = Path(entry['path'])
        if _same(path, executable):
            continue
        if not path.is_file():
            continue  # Already gone; forget it.
        theirs = _version(entry.get('version'))
        protected = keep_under and _same(path.parent.parent, keep_under)  # Right-click menu copies.
        try:
            unchanged = list(_stamp(path)) == [entry.get('size'), entry.get('mtime')]
        except OSError:
            unchanged = False
        if not protected and unchanged and mine and theirs and theirs <= mine:
            try:
                path.unlink()
                deleted.append(path)
                continue
            except OSError:
                pass  # Probably still running; try again next launch.
        kept.append(entry)
    try:
        size, mtime = _stamp(executable)
        kept.append({'path': str(executable), 'version': version, 'size': size, 'mtime': mtime})
    except OSError:
        pass
    save(json.dumps(kept))
    return deleted
