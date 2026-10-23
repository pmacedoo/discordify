"""Build a self-contained Windows executable. Run from a Windows Python environment."""
import os
import filecmp
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

import imageio_ffmpeg
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'src'))
from discordify import icon


def main(dist_dir=None):
    if sys.platform != 'win32':
        raise SystemExit('Build requer Windows x64.')
    bundled = Path(imageio_ffmpeg.get_ffmpeg_exe())
    destination = ROOT / 'src' / 'ffmpeg.exe'
    if not destination.exists() or not filecmp.cmp(bundled, destination, shallow=False):
        shutil.copyfile(bundled, destination)
    app = QApplication([])
    icon().pixmap(256, 256).save(str(ROOT / 'assets' / 'discordify.ico'))
    env = os.environ.copy()
    # Keep unrelated ICU/UCRT DLLs from other applications out of the package.
    env['PATH'] = os.pathsep.join([str(Path(sys.executable).parent),
                                 str(Path(os.environ['WINDIR']) / 'System32'),
                                 os.environ['WINDIR']])
    subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean',
                    '--onefile', '--windowed', '--name', 'Discordify',
                    '--icon', str(ROOT / 'assets' / 'discordify.ico'),
                    '--add-binary', f'{ROOT / "src" / "ffmpeg.exe"};.',
                    '--distpath', str(Path(dist_dir).resolve() if dist_dir else ROOT / 'dist'),
                    '--workpath', str(ROOT / 'build'),
                    '--specpath', str(ROOT / 'build'),
                    str(ROOT / 'src' / 'discordify.py')], check=True, env=env)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Build Discordify for Windows.')
    parser.add_argument('--dist-dir', help='Build into a separate directory, e.g. while another version is running.')
    main(parser.parse_args().dist_dir)
