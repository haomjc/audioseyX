"""Common coefficient-reserve policy for planning and native preparation."""
from __future__ import annotations

import numpy as np

from .compat import CompatibilityDataUnavailable
from .native import compile_channel


def required_headroom_db(peak, limit=.5):
    if not np.isfinite([peak,limit]).all() or peak<0 or limit<=0:raise ValueError('Coefficient reserve inputs must be finite and nonnegative')
    needed=max(0.,20*np.log10(max(float(peak),1e-15)/limit))
    return float(np.ceil(needed*10-1e-9)/10)


def curve_headroom(curves):
    channels={i['channel'] for i in next(iter(curves.values()))['channels']}
    peaks={c:0. for c in channels};complete={c:True for c in channels}
    for design in curves.values():
        if {i['channel'] for i in design['channels']}!=channels:raise ValueError('Headroom planning needs the same outputs in every curve')
        for item in design['channels']:
            c=item['channel'];peaks[c]=max(peaks[c],float(np.max(np.abs(item['taps']))))
            try:
                compiled=compile_channel(item,design['tier'])
                peaks[c]=max(peaks[c],float(np.max(np.abs(compiled['coefficients']))))
            except CompatibilityDataUnavailable:
                complete[c]=False
    return {'compensation_db':{c:required_headroom_db(peaks[c]) for c in sorted(channels)},
            'native_encoding_available':complete,'coefficient_peak_limit':.5,
            'notice':'The same tenth-dB coefficient-reserve rule used in native preparation. Without optional encoder resources, the FIR peak supplies a necessary lower bound only; native preparation still checks the actual codec peak. This is not an acoustic clipping or driver-output certificate.'}
