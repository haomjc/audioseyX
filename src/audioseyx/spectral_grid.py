"""Full-record responses on a common physical frequency grid."""
from __future__ import annotations

from collections import defaultdict
import numpy as np
from scipy import signal
from scipy.fft import rfft


def record_response(measurement, frequencies, nfft, rate):
    if measurement.ir is None:
        magnitude = 10 ** (np.interp(frequencies, measurement.frequency, measurement.magnitude_db) / 20)
        phase = np.interp(frequencies, measurement.frequency, np.unwrap(np.radians(measurement.phase_deg))) if measurement.phase_deg is not None else np.zeros_like(frequencies)
        return magnitude * np.exp(1j * phase)
    if measurement.sample_rate == rate:
        if len(measurement.ir) > nfft:
            raise ValueError("The complete measured impulse does not fit the analysis grid")
        response = rfft(measurement.ir, nfft)
    else:
        response = np.zeros(len(frequencies), dtype=complex)
        count = int(np.count_nonzero(frequencies <= measurement.sample_rate / 2))
        if count > 1:
            response[:count] = signal.zoom_fft(measurement.ir, [frequencies[0], frequencies[count - 1]],
                                                m=count, fs=measurement.sample_rate, endpoint=True)
        elif count:
            response[0] = np.sum(measurement.ir)
    return response * np.exp(-2j * np.pi * frequencies * measurement.start_time_s)


def filter_response(item, frequencies, nfft, rate):
    taps = np.asarray(item["taps"], dtype=float)
    if item["sample_rate"] == rate:
        if len(taps) > nfft:
            raise ValueError("The complete filter does not fit the analysis grid")
        return rfft(taps, nfft)
    response = np.zeros(len(frequencies), dtype=complex)
    count = int(np.count_nonzero(frequencies <= item["sample_rate"] / 2))
    if count > 1:
        response[:count] = signal.zoom_fft(taps, [frequencies[0], frequencies[count - 1]], m=count,
                                            fs=item["sample_rate"], endpoint=True)
    elif count:
        response[0] = np.sum(taps)
    return response


class SpectralGrid:
    def __init__(self, project, design, *, memory_mib=512, cancel=None):
        if not project.measurements:
            raise ValueError("Measurements are required for calibration analysis")
        self.project, self.design = project, design
        self.items = {item["channel"]: item for item in design["channels"]}
        if set(self.items) != set(project.channels) or len(self.items) != len(design["channels"]):
            raise ValueError("Exactly one complete filter is required for every measured output")
        self.rate = max([m.sample_rate for m in project.measurements] + [i["sample_rate"] for i in self.items.values()])
        duration = max([len(m.ir) / m.sample_rate for m in project.measurements if m.ir is not None] or [0])
        duration += max(len(i["taps"]) / i["sample_rate"] for i in self.items.values())
        self.nfft = 1 << int(np.ceil(np.log2(max(1024, duration * self.rate + 1))))
        bins = self.nfft // 2 + 1
        estimate = bins * (len(project.measurements) + len(self.items) + 24) * 32
        if not np.isfinite(memory_mib) or not 32 <= memory_mib <= 32768 or estimate > memory_mib * 1024**2:
            raise ValueError("The complete calibration grid exceeds the explicit memory budget; no records were removed")
        self.estimated_memory_mib = estimate / 1024**2
        self.frequency = np.fft.rfftfreq(self.nfft, 1 / self.rate)
        self.low = max([float(m.frequency[0]) if m.ir is None else 0 for m in project.measurements])
        self.high = min(float(m.frequency[-1]) if m.ir is None else m.sample_rate / 2 for m in project.measurements)
        self.groups = defaultdict(list)
        self.responses = []
        self.filters = {c: filter_response(i, self.frequency, self.nfft, self.rate) for c, i in self.items.items()}
        for index, measurement in enumerate(project.measurements):
            if cancel is not None and cancel.is_set():
                raise InterruptedError("Calibration response analysis cancelled")
            self.groups[(measurement.channel, measurement.position)].append(index)
            self.responses.append(record_response(measurement, self.frequency, self.nfft, self.rate))

    def corrected(self, index):
        return self.responses[index] * self.filters[self.project.measurements[index].channel]


def position_weights(project, settings):
    positions = sorted({m.position for m in project.measurements})
    supplied = settings.get("position_weights", {})
    if set(supplied) - set(positions):
        raise ValueError("Calibration weights refer to unavailable training positions")
    values = np.array([float(supplied.get(p, 1)) for p in positions])
    if not np.isfinite(values).all() or np.any(values <= 0):
        raise ValueError("Calibration position weights must be positive and finite")
    values /= values.sum()
    return dict(zip(positions, values.tolist()))
