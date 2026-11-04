"""Optional, user-supplied native compatibility data; never downloaded implicitly."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import numpy as np


class CompatibilityDataUnavailable(ValueError):
    pass


def _resource(name):
    directory = os.environ.get("AUDIOSEYX_COMPAT_DIR")
    if not directory:
        raise CompatibilityDataUnavailable('Local native compatibility data are not configured. Acoustic analysis, filter design and general WAV export remain available.')
    path = Path(directory).expanduser().resolve() / name
    if not path.is_file() or path.stat().st_size > 128 * 1024:
        raise CompatibilityDataUnavailable('Native compatibility data are missing or too large: ' + name)
    try:
        raw = path.read_bytes()
        document = json.loads(raw)
    except (OSError, ValueError) as error:
        raise CompatibilityDataUnavailable('Could not read native compatibility data: ' + name) from error
    if not isinstance(document, dict):
        raise CompatibilityDataUnavailable('Native compatibility data must contain a JSON object: ' + name)
    return document, hashlib.sha256(raw).hexdigest()


def load_native_tables():
    document, _ = _resource("native_tables.json")
    tables = document.get("filters", {})
    lengths = {"decFilterXT32Sub29_taps": 29, "decFilterXT32Sub37_taps": 37,
               "decFilterXT32Sub93_taps": 93, "decFilterXT32Sat129_taps": 129}
    checked = {}
    for name, size in lengths.items():
        try:
            values = np.asarray(tables[name], dtype=float)
        except (ValueError, TypeError, KeyError) as error:
            raise CompatibilityDataUnavailable('Invalid native filter compatibility data: ' + name) from error
        if values.shape != (size,) or not np.isfinite(values).all() or not 0 < np.sum(np.abs(values)) < 100:
            raise CompatibilityDataUnavailable('Invalid native filter data shape or values: ' + name)
        checked[name] = values
    return checked


def load_channel_addresses():
    document, _ = _resource("channel_addresses.json")
    for channel, row in document.items():
        if not isinstance(channel, str) or not isinstance(row, dict):
            raise CompatibilityDataUnavailable('Invalid native channel mapping')
        for key in ["eq2", "neq2", "griffin"]:
            value = row.get(key)
            if value is not None and (type(value) is not int or not 0 <= value <= 255):
                raise CompatibilityDataUnavailable('Invalid native channel address')
    if not document:
        raise CompatibilityDataUnavailable('The native channel mapping is empty')
    return document


def compatibility_status():
    results = {}
    for name, loader in [("xt32_tables", load_native_tables), ("channel_addresses", load_channel_addresses)]:
        try:
            loader()
            results[name] = {"available": True}
        except CompatibilityDataUnavailable as error:
            results[name] = {"available": False, "reason": str(error)}
    return {**results, "hardware_verified": False,
            "notice": 'Public distributions exclude legacy numerical tables with unresolved redistribution permissions. Supplying local data does not validate receiver compatibility.'}
