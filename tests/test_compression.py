"""Integration tests using generated videos and the actual FFmpeg encoder."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
import discordify as app


def ffmpeg(args):
    result = subprocess.run([app.FFMPEG, '-hide_banner', '-nostdin', '-y', *args],
                            capture_output=True)
    assert result.returncode == 0, result.stderr.decode('utf-8', 'replace')[-2000:]
    return result.stderr.decode('utf-8', 'replace')


def timestamps(path):
    log = ffmpeg(['-i', str(path), '-map', '0:v:0', '-vf', 'showinfo',
                  '-fps_mode', 'passthrough', '-f', 'null', os.devnull])
    return [float(t) for t in re.findall(r'\bn:\s*\d+\s+pts:.*?pts_time:([\d.e+-]+)', log)]


def main():
    assert Path(app.FFMPEG).exists(), 'Execute python build.py primeiro.'
    with tempfile.TemporaryDirectory(prefix='discordify-test-') as folder:
        folder = Path(folder)
        cases = [('60', '1280x720', '8', [], 480),
                 ('30000/1001', '320x240', '3', [], 90),
                 ('60', '320x240', '3', ['-vf', "select='if(lt(t,1),1,not(mod(n,2)))'", '-fps_mode', 'vfr'], 120)]
        for index, (rate, size, duration, extra, count) in enumerate(cases):
            source = folder / f'vídeo {index}.mkv'
            audio = ['-f', 'lavfi', '-i', f'sine=frequency=440:duration={duration}'] if index == 0 else []
            ffmpeg(['-f', 'lavfi', '-i', f'testsrc2=size={size}:rate={rate}:duration={duration}',
                    *audio, *extra, '-c:v', 'ffv1', '-c:a', 'pcm_s16le', str(source)])
            original = source.read_bytes()
            if index == 0:
                assert len(original) > app.LIMIT
            result = app.compress(source, threading.Event(), lambda *args: None)
            assert 0 < result.stat().st_size <= app.LIMIT
            assert source.read_bytes() == original
            before, after = timestamps(source), timestamps(result)
            assert len(before) == len(after) == count
            assert max(abs(a - b) for a, b in zip(before, after)) < .0011
            assert app.compress(result, threading.Event(), lambda *args: None) == result
            print(f'{rate} FPS / case {index}: {len(after)} frames, {result.stat().st_size} bytes — OK')
            if index == 0:
                calls = []
                def device_loss(args, *rest, **kwargs):
                    encoder = args[args.index('-c:v') + 1]
                    calls.append(encoder)
                    if encoder == 'h264_nvenc':
                        Path(args[-1]).write_bytes(b'partial GPU output')
                        raise RuntimeError('Simulated device loss')
                    return app.run(args, *rest, **kwargs)
                fallback = app.compress(source, threading.Event(), lambda *args: None,
                    selector=lambda *args: ('h264_nvenc', None), encode=device_loss)
                assert calls == ['h264_nvenc', 'libx264']
                assert len(timestamps(fallback)) == count
                assert fallback.stat().st_size <= app.LIMIT
                assert source.read_bytes() == original
                print('Simulated GPU failure -> real CPU conversion — OK')
        cancel = threading.Event()
        cancel.set()
        try:
            app.compress(source, cancel, lambda *args: None)
            raise AssertionError('Expected cancellation')
        except app.Cancelled:
            pass
        invalid = folder / 'invalid.mp4'
        invalid.write_bytes(b'not a video')
        try:
            app.compress(invalid, threading.Event(), lambda *args: None)
            raise AssertionError('Expected invalid input failure')
        except RuntimeError:
            pass
    print('All compression integration tests passed.')


if __name__ == '__main__':
    main()
