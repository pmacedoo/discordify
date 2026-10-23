"""Measured encoder selection. All probes use real media and normal FFmpeg APIs."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
from functools import lru_cache

CPU = 'libx264'
ENCODERS = (CPU, 'h264_nvenc', 'h264_qsv', 'h264_amf')
TTL = 7 * 86400
CACHE = Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'Discordify' / 'encoders-v2.json'


def options(encoder, bitrate):
    presets = {
        CPU: ['-preset', 'veryfast'],
        'h264_nvenc': ['-preset', 'p4', '-rc', 'vbr', '-multipass', 'disabled'],
        'h264_qsv': ['-preset', 'veryfast'],
        'h264_amf': ['-quality', 'balanced', '-rc', 'vbr_peak'],
    }
    return ['-c:v', encoder, *presets[encoder], '-b:v', str(bitrate),
            '-pix_fmt', 'nv12' if encoder == 'h264_qsv' else 'yuv420p',
            '-fps_mode', 'passthrough']


@lru_cache(maxsize=1)
def hardware_id():
    if os.name != 'nt':
        return os.uname().machine
    try:
        result = subprocess.run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
                                 'Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,PNPDeviceID | ConvertTo-Json -Compress'],
                                capture_output=True, timeout=8,
                                creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode == 0:
            return result.stdout.decode('utf-8', 'replace').strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    # Do not reuse a persistent decision when hardware cannot be identified.
    return f'unknown-{os.getpid()}'


def profile_key(info, duration, bitrate, ffmpeg):
    video = next((line for line in info.splitlines() if 'Video:' in line), '')
    dimensions = re.search(r'\b(\d{2,})x(\d{2,})\b', video)
    fps = re.search(r'([\d.]+) (?:fps|tbr)', video)
    binary = Path(ffmpeg).stat()
    profile = [hardware_id(), binary.st_size, binary.st_mtime_ns,
               dimensions.group(0) if dimensions else video,
               fps.group(1) if fps else 'vfr', round(math.log2(max(1, bitrate)) * 4),
               0 if duration < 10 else 1 if duration < 60 else 2]
    # Frozen extraction changes mtime across launches; size + bundled build identity is stable.
    profile[2] = 'ffmpeg-7.1-policy-4'
    return hashlib.sha256(json.dumps(profile).encode()).hexdigest()


def read_cache(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def forget(key, path=CACHE):
    # A transient GPU failure should trigger a fresh measurement next time.
    try:
        entries = read_cache(path)
        entries.pop(key, None)
        Path(path).write_text(json.dumps(entries), encoding='utf-8')
    except OSError:
        pass


def remember(key, encoder, path=CACHE, measurements=None):
    try:
        entries = read_cache(path)
        now = time.time()
        entries = {k: v for k, v in entries.items() if isinstance(v, dict) and
                   isinstance(v.get('time'), (int, float)) and 0 <= now - v['time'] < TTL}
        entries[key] = {'encoder': encoder, 'time': now}
        if measurements is not None:
            entries[key]['measurements'] = measurements
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=Path(path).parent,
                                         delete=False, suffix='.tmp') as file:
            temp = Path(file.name)
            json.dump(entries, file)
        try:
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)
    except OSError:
        pass  # An unwritable cache must never prevent a conversion.


def pick_result(results):
    """Fastest measured candidate within 0.02 SSIM of the CPU reference."""
    baseline = next((r for r in results if r['encoder'] == CPU), None)
    if baseline is None:
        return CPU
    acceptable = [r for r in results if r['quality'] >= baseline['quality'] - .02]
    return min(acceptable, key=lambda r: r['seconds'])['encoder']


def choose(source, duration, bitrate, filters, info, ffmpeg, run, cancel,
           progress, cache_path=CACHE, candidates=ENCODERS):
    hardware = hardware_id().lower()
    if candidates == ENCODERS and not hardware.startswith('unknown-') and os.name == 'nt':
        vendors = {'h264_nvenc': ('nvidia',), 'h264_qsv': ('intel',), 'h264_amf': ('amd', 'radeon')}
        candidates = tuple(e for e in candidates if e == CPU or any(v in hardware for v in vendors[e]))
    key = profile_key(info, duration, bitrate, ffmpeg)
    cached = read_cache(cache_path).get(key, {})
    stamp = cached.get('time') if isinstance(cached, dict) else None
    age = time.time() - stamp if isinstance(stamp, (int, float)) else TTL
    if isinstance(cached, dict) and 0 <= age < TTL and cached.get('encoder') in candidates:
        return cached['encoder'], key
    progress(0, 'Finding the fastest encoder…')
    results = []
    with tempfile.TemporaryDirectory(prefix='discordify-probe-') as folder:
        reference = Path(folder) / 'reference.mkv'
        sample_duration = min(1.5, duration)
        run(['-y', '-ss', str(max(0, duration * .4 - sample_duration / 2)),
             '-i', str(source), '-t', str(sample_duration), '-map', '0:v:0',
             '-vf', filters, '-an', '-c:v', 'ffv1', '-pix_fmt', 'yuv420p',
             '-fps_mode', 'passthrough', str(reference)], cancel, timeout=30)
        for encoder in candidates:
            sample = Path(folder) / f'{encoder}.mp4'
            try:
                started = time.perf_counter()
                run(['-y', '-i', str(reference), '-map', '0:v:0', '-an',
                     *options(encoder, bitrate), str(sample)], cancel, timeout=20)
                elapsed = time.perf_counter() - started
                # Two sample lengths distinguish encoder startup from sustained throughput.
                # Retain the observed sample cost as a floor when measurements are noisy.
                estimated = elapsed
                if duration > sample_duration and sample_duration > .5:
                    short_duration = .25
                    started = time.perf_counter()
                    run(['-y', '-i', str(reference), '-t', str(short_duration),
                         '-map', '0:v:0', '-an', *options(encoder, bitrate),
                         str(Path(folder) / f'{encoder}-short.mp4')], cancel, timeout=20)
                    short_elapsed = time.perf_counter() - started
                    per_second = max(.001, (elapsed - short_elapsed) / (sample_duration - short_duration))
                    estimated = max(elapsed, short_elapsed) + per_second * (duration - sample_duration)
                quality_log = run(['-i', str(reference), '-i', str(sample),
                    '-lavfi', '[0:v]settb=AVTB,setpts=PTS-STARTPTS[a];[1:v]settb=AVTB,setpts=PTS-STARTPTS[b];[a][b]ssim',
                    '-an', '-f', 'null', os.devnull], cancel, timeout=20)
                quality = re.findall(r'All:([\d.]+)', quality_log)
                if quality:
                    results.append({'encoder': encoder, 'seconds': estimated,
                                    'sample_seconds': elapsed, 'quality': float(quality[-1])})
            except RuntimeError:
                continue  # Advertised encoders can lack a compatible GPU or driver.
    winner = pick_result(results)
    remember(key, winner, cache_path, results)
    return winner, key
