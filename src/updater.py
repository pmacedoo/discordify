"""Self-update from the latest GitHub release. Only the frozen Windows executable updates itself."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import urllib.request

from shell_integration import VERSION

OWNER = 'pmacedoo'
REPO = f'{OWNER}/discordify'
API = f'https://api.github.com/repos/{REPO}/releases/latest'
ASSET = 'Discordify.exe'
SUMS = 'SHA256SUMS.txt'
RESTARTED = '--updated'
HEADERS = {'User-Agent': 'Discordify-Updater', 'Accept': 'application/vnd.github+json'}


def parse(version):
    match = re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)', version.strip())
    return tuple(map(int, match.groups())) if match else None


def newer(tag, current=VERSION):
    latest, mine = parse(tag), parse(current)
    return bool(latest and mine and latest > mine)


def fetch(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=timeout) as response:
        return response.read()


def latest_release(fetch=fetch):
    """Return (tag, exe_url, sums_url, page_url) when a newer release with both assets exists, else None."""
    release = json.loads(fetch(API))
    if release.get('draft') or release.get('prerelease') or not newer(release.get('tag_name', '')):
        return None
    # Only releases and files published by the owner's account are trusted.
    if (release.get('author') or {}).get('login') != OWNER:
        return None
    assets = {a['name']: a['browser_download_url'] for a in release.get('assets', [])
              if (a.get('uploader') or {}).get('login') == OWNER}
    if ASSET not in assets or SUMS not in assets:
        return None
    page = release.get('html_url') or f'https://github.com/{REPO}/releases/latest'
    return release['tag_name'], assets[ASSET], assets[SUMS], page


def expected_hash(sums_text, name=ASSET):
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip('*') == name and re.fullmatch(r'[0-9a-fA-F]{64}', parts[0]):
            return parts[0].lower()
    raise ValueError(f'{name} is missing from {SUMS}.')


def download(release, folder, fetch=fetch):
    """Download and verify the new executable into folder; return its path."""
    exe_url, sums_url = release[1], release[2]
    digest = expected_hash(fetch(sums_url).decode('utf-8', 'replace'))
    data = fetch(exe_url, timeout=300)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('The downloaded update failed its checksum.')
    with tempfile.NamedTemporaryFile(dir=folder, delete=False, suffix='.download') as file:
        file.write(data)
    return Path(file.name)


def swap(new_file, executable):
    """Put new_file in place of the running executable. Windows allows renaming a running exe."""
    executable = Path(executable)
    old = executable.with_name(executable.name + '.old')
    old.unlink(missing_ok=True)
    os.replace(executable, old)
    try:
        os.replace(new_file, executable)
    except OSError:
        os.replace(old, executable)
        raise
    return executable


def cleanup(executable=sys.executable):
    """Delete what an update leaves next to the exe: the replaced build and unfinished downloads."""
    executable = Path(executable)
    for leftover in [executable.with_name(executable.name + '.old'), *executable.parent.glob('tmp*.download')]:
        try:
            leftover.unlink(missing_ok=True)
        except OSError:
            pass  # Still locked by the exiting old process; the next launch removes it.


def enabled():
    return getattr(sys, 'frozen', False) and os.name == 'nt'


def download_next_to_app(release):
    """Run off the UI thread. Returns the verified downloaded file."""
    return download(release, Path(sys.executable).parent)


def install_and_restart(new_file):
    executable = swap(new_file, sys.executable)
    subprocess.Popen([str(executable), RESTARTED], close_fds=True,
                     creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
