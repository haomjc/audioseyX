"""File-only raw recording clock tools; no audio-device or receiver access."""
from __future__ import annotations
import base64
import hashlib
import json
from pathlib import Path
import secrets
import zipfile
from fractions import Fraction

import numpy as np
from scipy import signal
from scipy.io import wavfile

from .clock_sync import pilot_signal, estimate_clock, correct_recording


def _mono_wav(path, channel=0):
    rate, data = wavfile.read(path)
    channels = 1 if data.ndim == 1 else data.shape[1] if data.ndim == 2 else 0
    if not channels or not isinstance(channel, int) or not 0 <= channel < channels:
        raise ValueError('Choose a valid channel from an original PCM/float recording WAV')
    if data.ndim == 2: data = data[:, channel]
    if data.dtype.kind == 'u':
        middle = (np.iinfo(data.dtype).max + 1) / 2
        value = (data.astype(float) - middle) / middle
    elif data.dtype.kind == 'i': value = data.astype(float) / (-float(np.iinfo(data.dtype).min))
    elif data.dtype.kind == 'f': value = data.astype(float)
    else: raise ValueError('Unsupported raw audio sample format')
    if not np.isfinite(value).all(): raise ValueError('The original recording contains non-finite samples')
    return rate, value, channels


def clock_reference(home, times=(.5, 2., 3.5), rate=48000):
    times = np.asarray(times, dtype=float)
    if times.ndim != 1 or not 3 <= len(times) <= 20 or not np.isfinite(times).all() or times[0] < .25 or times[-1] > 120 or np.any(np.diff(times) < .5):
        raise ValueError('Use 3-20 distinct pilot times between 0.25 and 120 seconds, at least 0.5 seconds apart')
    pilot = pilot_signal(rate) * .1
    value = np.zeros(round((times[-1] + .3) * rate) + len(pilot))
    for second in times:
        start = round(second * rate); value[start:start + len(pilot)] += pilot
    directory = Path(home) / 'exports' / ('clock-reference-' + secrets.token_hex(8)); directory.mkdir(parents=True)
    wavfile.write(directory / 'pilot.wav', rate, pilot.astype(np.float64))
    wavfile.write(directory / 'reference.wav', rate, value.astype(np.float64))
    report = {'kind': 'known_clock_pilot_reference', 'sample_rate_hz': rate, 'marker_times_s': times.tolist(),
              'reference_created_only': True, 'sound_played': False,
              'notice': 'Continuous reference WAV and its pilot template. File creation does not play sound. Record a stationary acoustic path using a separate capture workflow; this is not an IR or a room-correction filter.'}
    (directory / 'protocol.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    return _archive(directory), report


def _archive(directory):
    output = directory.with_suffix('.zip')
    with zipfile.ZipFile(output, 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(directory.iterdir()):
            if path.is_file(): z.write(path, path.name)
    return output


def correct_clock_files(home, body, cancel, progress):
    directory = Path(home) / 'uploads' / ('clock-' + secrets.token_hex(8)); directory.mkdir(parents=True)
    originals = {}
    total = 0
    for name in ['recording', 'pilot']:
        entry = body.get(name)
        if not isinstance(entry, dict) or not isinstance(entry.get('data'), str):
            raise ValueError('Supply original recording and pilot-template WAV files')
        raw = base64.b64decode(entry['data'], validate=True); total += len(raw)
        if not raw or total > 96 * 1024**2: raise ValueError('The two original WAV inputs must total at most 96 MiB')
        target = directory / (name + '.wav'); target.write_bytes(raw)
        originals[name] = {'filename': Path(str(entry.get('filename', name + '.wav'))).name,
                           'file_sha256': hashlib.sha256(raw).hexdigest(), 'path': target}
    progress(8, 'Reading original recording and known pilot; no audio playback')
    rate, recording, channels = _mono_wav(originals['recording']['path'], int(body.get('channel', 0)))
    pilot_rate, pilot, pilot_channels = _mono_wav(originals['pilot']['path'])
    if pilot_channels != 1: raise ValueError('Use a mono pilot template')
    if pilot_rate != rate:
        ratio = Fraction(int(rate), int(pilot_rate))
        pilot = signal.resample_poly(pilot, ratio.numerator, ratio.denominator)
    progress(18, 'Matching known clock pilots in the original recording')
    analysis = estimate_clock(recording, pilot, rate, body.get('marker_times_s', []),
        latency_guess_s=float(body.get('latency_guess_s', 0)), search_radius_s=float(body.get('search_radius_s', .2)),
        maximum_ppm=float(body.get('maximum_ppm', 3000)), cancel=cancel)
    progress(40, 'Resampling the complete selected recording channel at continuous fractional positions')
    corrected, mapping = correct_recording(recording, analysis, taps=int(body.get('taps', 128)),
                                           memory_mib=float(body.get('memory_mib', 512)), cancel=cancel)
    output = Path(home) / 'exports' / ('clock-correction-' + secrets.token_hex(8)); output.mkdir(parents=True)
    wavfile.write(output / 'clock-corrected-recording.wav', rate, corrected.astype(np.float64))
    report = {'kind': 'raw_recording_clock_correction_not_an_IR', 'analysis': analysis, 'mapping': mapping,
              'selected_recording_channel': int(body.get('channel', 0)), 'original_recording_channels': channels,
              'original_files': {k: {a: b for a, b in v.items() if a != 'path'} for k, v in originals.items()},
              'raw_originals_retained': True, 'sound_played': False, 'receiver_contacted': False,
              'notice': 'Separate clock-corrected raw recording, not a deconvolved impulse or an AVR calibration. Only the selected recording channel is exported. The full original WAVs are retained locally. Use output_start_time_s from the mapping when preserving timing.'}
    (output / 'clock-report.json').write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    (output / 'README.txt').write_text('This archive contains a clock-corrected RAW RECORDING, not an impulse response.\n'
        'Do not import it as a measured IR without proper deconvolution.\n'
        'Original uploads remain unchanged. Timing origin and finite interpolation precision are in clock-report.json.\n', encoding='utf-8')
    progress(95, 'Packaging the corrected recording and complete timing map')
    return _archive(output), report
