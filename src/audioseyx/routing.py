"""Explicit bass-management assumptions shared by reference and native planning."""
import numpy as np
from scipy import signal

X3700H_CROSSOVERS = (40, 60, 80, 90, 100, 110, 120, 150, 180, 200, 250)


def crossover_responses(frequencies, crossover, model, rate=48000):
    if model not in ["lr4", "bw2_lr4"]:
        raise ValueError("Select an explicit crossover model: lr4 or bw2_lr4")
    if not np.isfinite(crossover) or not 20 <= crossover <= 1000 or crossover >= rate * .4:
        raise ValueError("Invalid crossover frequency")
    high = signal.sosfreqz(signal.butter(2, crossover, "highpass", fs=rate, output="sos"), worN=frequencies, fs=rate)[1]
    low = signal.sosfreqz(signal.butter(2, crossover, "lowpass", fs=rate, output="sos"), worN=frequencies, fs=rate)[1] ** 2
    return high ** 2 if model == "lr4" else high, low
