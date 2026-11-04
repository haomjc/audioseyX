import numpy as np


def parse_target_curve(text):
    points = []
    for index, line in enumerate(text.lstrip("\ufeff").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(("#", "*", "//")):
            continue
        fields = line.replace(",", " ").split()
        if len(fields) != 2:
            raise ValueError(f"Target curve line {index} must contain: frequency_Hz gain_dB")
        try:
            frequency, level = map(float, fields)
        except ValueError as error:
            raise ValueError(f"Target curve line {index} does not contain valid numbers") from error
        if not np.isfinite([frequency, level]).all() or frequency <= 0 or abs(level) > 200:
            raise ValueError('The target curve contains invalid frequencies or gains')
        points.append((frequency, level))
    if not 2 <= len(points) <= 10000:
        raise ValueError('The target curve requires 2-10000 points')
    points.sort()
    if any(a[0] >= b[0] for a, b in zip(points, points[1:])):
        raise ValueError('Target curve frequencies must be unique')
    return {"frequency": [p[0] for p in points], "level_db": [p[1] for p in points],
            "reference": "normalised_at_1khz"}


def target_offset(curve, frequencies):
    source = np.log(np.asarray(curve["frequency"], dtype=float))
    levels = np.asarray(curve["level_db"], dtype=float)
    reference = np.interp(np.log(1000), source, levels)
    return np.interp(np.log(np.maximum(frequencies, 0.1)), source, levels) - reference
