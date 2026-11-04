from pathlib import Path
import numpy as np

from ._java_stream import _JavaReader, _JavaObject, _java_value, _deref
from .model import Measurement, Project, channel_and_position


def import_mdat(path):
    path = Path(path)
    reader = _JavaReader(path.read_bytes())
    objects = reader.read_stream()
    measurements = []
    for root in objects:
        if not isinstance(root, _JavaObject) or root.java_class.name != "roomeqwizard.MeasData":
            continue
        spl = _deref(root.fields.get("splValues"), reader)
        if not isinstance(spl, list) or len(spl) < 2:
            continue
        count = min(len(spl), int(_java_value(root, "dataLength", reader, len(spl))))
        step = float(_java_value(root, "freqStep", reader, 0))
        start = float(_java_value(root, "startFreq", reader, 0))
        if not np.isfinite(step) or step <= 0 or count < 2:
            raise ValueError('Invalid MDAT frequency spacing; this frequency-axis encoding is unsupported')
        name = str(_java_value(root, "shortDesc", reader, f"Measurement {len(measurements) + 1}"))
        channel, position = channel_and_position(name)
        phase = _deref(root.fields.get("phaseValues"), reader)
        phase = phase[:count] if isinstance(phase, list) and len(phase) >= count else None
        measurements.append(Measurement(channel, position, name,
                                        float(_java_value(root, "sampleRate", reader, 48000)),
                                        frequency=start + np.arange(count) * step,
                                        magnitude_db=spl[:count], phase_deg=phase,
                                        metadata={"source_format": "REW_MDAT", "ir_not_decoded": True}))
    if not measurements:
        raise ValueError('No readable REW frequency measurements were found in the MDAT')
    return Project(path.stem, measurements, warnings=[
        'MDAT frequency data were read without guessing private IR fields. Use REW to obtain original impulse responses for phase or decay analysis.',
        'Check channel and position names. Unknown names are not automatically assigned to the front speakers.'])
