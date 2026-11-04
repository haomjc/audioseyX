from __future__ import annotations

import numpy as np
from scipy import signal

from . import __version__
from .analysis import analyze_measurement, decay_analysis
from .dsp import design_channel, spectrum
from .model import Measurement


def run_benchmarks():
    fs = 48000
    checks = []
    impulse = np.zeros(16384)
    impulse[240] = 1
    analysis = analyze_measurement(Measurement("FL", "p0", "known delay", fs, impulse, start_time_s=-.001))
    delay_error = float(np.max(np.abs(np.asarray(analysis["spectrum"]["group_delay_ms"]) - 4)))
    checks.append({"name": 'Pure delay and measurement start time', "passed": delay_error < 1e-6,
                   "expected_ms": 4, "maximum_error_ms": delay_error})
    rng = np.random.default_rng(571)
    t = np.arange(2 * fs) / fs
    decay = rng.normal(size=len(t)) * np.exp(-np.log(1000) * t / .45)
    fit = decay_analysis(decay, fs)["fits"]["T30"]
    checks.append({"name": 'Known exponential decay', "passed": fit["available"] and abs(fit["estimate_s"] - .45) < .025,
                   "expected_s": .45, "estimated_s": fit["estimate_s"]})
    short_t = np.arange(16384) / fs
    short = rng.normal(size=len(short_t)) * np.exp(-np.log(1000) * short_t / 2)
    short_fit = decay_analysis(short, fs)["fits"]["T30"]
    checks.append({"name": 'Slow decay truncated by a short recording', "passed": not short_fit["available"], "reason": short_fit["reason"]})
    b1, a1 = signal.iirpeak(55, 8, fs=fs)
    b2, a2 = signal.iirpeak(110, 6, fs=fs)
    def room(gain):
        return impulse + gain * signal.lfilter(b1, a1, impulse) + 1.5 * signal.lfilter(b2, a2, impulse)
    training = [Measurement("FL", f"p{i}", "training", fs, room(gain)) for i, gain in enumerate([3, 4, 5])]
    holdout = Measurement("FL", "held_out", "not used for design", fs, room(3.5))
    f = np.geomspace(25, 200, 600)
    original = spectrum(holdout, f)
    before = 20 * np.log10(np.maximum(np.abs(original), 1e-12))
    settings = {"low_hz": 25, "high_hz": 200, "bass_db": 0, "tilt_db_oct": 0}
    for tier in ["MultEQ", "XT", "XT32"]:
        design = design_channel(training, tier, "FL", settings)
        actual = spectrum(Measurement("FL", "filter", "actual truncated taps", design["sample_rate"], design["taps"]), f)
        after = 20 * np.log10(np.maximum(np.abs(original * actual), 1e-12))
        error_a, error_b = float(np.sqrt(np.mean(before ** 2))), float(np.sqrt(np.mean(after ** 2)))
        checks.append({"name": tier + ' / synthetic holdout position', "passed": bool(np.isfinite(error_b) and error_b < error_a),
                       "before_rms_db": error_a, "after_rms_db": error_b,
                       "design_taps": design["design_length"], "held_out_used_for_design": False})
    # A frequency-only, repeatable 35 dB notch must not receive the full allowed
    # boost simply because spatial variance happens to be zero.
    notch = Measurement("FL", "p0", "deep notch", frequency=[20, 40, 50, 60, 80, 150, 300, 1000, 20000],
                        magnitude_db=[0, 0, -35, 0, 0, 0, 0, 0, 0])
    design = design_channel([notch], "XT32", "FL", settings)
    at_null = 20 * np.log10(abs(spectrum(Measurement("FL", "f", "filter", fs, design["taps"]), np.asarray([50.]))[0]))
    checks.append({"name": 'Boost protection for a repeatable deep null', "passed": bool(at_null < 1), "gain_at_null_db": float(at_null)})
    return {"version": __version__, "origin": "synthetic", "all_passed": all(c["passed"] for c in checks),
            "checks": checks, "hardware_tested": False,
            "notice": 'Fixed-ground-truth numerical benchmarks, including a synthetic position excluded from design. These cases do not establish real-room results or equivalence to commercial products.'}
