"""Native coefficient serialization and offline reconstruction of wire streams.

This validates the bytes to send, not the AVR's internal filter synthesis.
No socket or device-discovery code is used here.
"""
from __future__ import annotations

import hashlib
import itertools
import struct

import numpy as np

from .native_protocol import frame, parse_frame, _byte

SAMPLE_RATE_SLOTS = {0: 32000, 1: 44100, 2: 48000}
CURVES = (0, 1)


def serialize_coefficients(coefficients, fixed=False):
    data = np.asarray(coefficients, dtype=float)
    if not isinstance(fixed, bool) or data.ndim != 1 or not 1 <= data.size <= 16384 or not np.isfinite(data).all():
        raise ValueError("Invalid coefficient payload or numeric format")
    if fixed:
        if np.max(np.abs(data)) >= 1:
            raise ValueError("Fixed-point coefficients exceed Q31 range; silent saturation is not allowed")
        # Retain the observed negative-value convention, including its one-LSB
        # bias at exact negative binary fractions. Do not silently re-round it.
        encoded = np.where(data < 0, -np.floor(np.abs(data) * 2**31) - 1,
                           np.floor(data * 2**31)).astype("<i4")
    else:
        if np.max(np.abs(data)) > np.finfo(np.float32).max:
            raise ValueError("Coefficients exceed the float32 range")
        encoded = data.astype("<f4")
    return encoded.tobytes()


def deserialize_coefficients(raw, fixed=False):
    if not isinstance(fixed, bool) or not isinstance(raw, (bytes, bytearray, memoryview)) or not len(raw) or len(raw) % 4:
        raise ValueError("Invalid serialized coefficient values")
    values = np.frombuffer(raw, dtype="<i4" if fixed else "<f4").astype(float)
    if fixed:
        values /= 2**31
    if not np.isfinite(values).all():
        raise ValueError("The wire coefficients contain non-finite values")
    return values


def quantization_report(coefficients, fixed=False):
    original = np.asarray(coefficients, dtype=float)
    raw = serialize_coefficients(original, fixed)
    quantized = deserialize_coefficients(raw, fixed)
    difference = np.abs(original - quantized)
    return {"format": "Q1.31_observed_negative_bias" if fixed else "IEEE754_float32_little_endian",
            "coefficient_count": int(original.size), "wire_sha256": hashlib.sha256(raw).hexdigest(),
            "maximum_absolute_error": float(np.max(difference)), "coefficient_l1_error": float(np.sum(difference)),
            "underflowed_values": int(np.count_nonzero((original != 0) & (quantized == 0))),
            "maximum_absolute_wire_value": float(np.max(np.abs(quantized))),
            "scope": "serialized_coefficient_values; native_filter_reconstruction_not_verified"}


def coefficient_packets(coefficients, channel_byte, curve=0, sample_rate_slot=2, fixed=False):
    channel_byte, curve, sample_rate_slot = _byte(channel_byte), _byte(curve), _byte(sample_rate_slot)
    if curve not in CURVES or sample_rate_slot not in SAMPLE_RATE_SLOTS:
        raise ValueError("Unsupported coefficient curve or sample-rate slot")
    raw = serialize_coefficients(coefficients, fixed)
    chunks = [raw[:127 * 4]]
    chunks += [raw[i:i + 128 * 4] for i in range(127 * 4, len(raw), 128 * 4)]
    return [frame("SET_COEFDT", bytes([curve, sample_rate_slot, channel_byte, 0]) + chunk if i == 0 else chunk,
                  i, len(chunks) - 1) for i, chunk in enumerate(chunks)]


def packet_digest(packets):
    digest = hashlib.sha256()
    for packet in packets:
        if not isinstance(packet, (bytes, bytearray, memoryview)):
            raise ValueError("Coefficient packets must be bytes")
        digest.update(struct.pack(">I", len(packet)))
        digest.update(packet)
    return digest.hexdigest()


def decode_coefficient_stream(packets, *, fixed=False, expected_count=None):
    """Reconstruct one complete channel/curve/rate stream before any I/O."""
    packets = list(packets)
    if not packets or len(packets) > 256:
        raise ValueError("A coefficient stream needs 1-256 complete packets")
    chunks = []
    routing = None
    for index, packet in enumerate(packets):
        command, payload = parse_frame(packet)
        if command != "SET_COEFDT" or packet[0] != 0x54 or packet[15] != 0:
            raise ValueError("Unexpected command or framing in a coefficient stream")
        if len(packet) != len(payload) + 19:
            raise ValueError("Coefficient frame has undeclared trailing bytes")
        if packet[3] != index or packet[4] != len(packets) - 1:
            raise ValueError("Coefficient sequence is missing, reordered or duplicated")
        if index == 0:
            if len(payload) < 8 or payload[3] != 0:
                raise ValueError("Invalid initial coefficient routing header")
            curve, slot, address = payload[:3]
            if curve not in CURVES or slot not in SAMPLE_RATE_SLOTS:
                raise ValueError("Unsupported coefficient curve or sample-rate slot")
            routing = {"curve": curve, "sample_rate_slot": slot,
                       "sample_rate_hz": SAMPLE_RATE_SLOTS[slot], "address": address}
            payload = payload[4:]
        capacity = 127 * 4 if index == 0 else 128 * 4
        if not len(payload) or len(payload) % 4 or len(payload) > capacity:
            raise ValueError("Invalid coefficient fragment size")
        if index != len(packets) - 1 and len(payload) != capacity:
            raise ValueError("An intermediate coefficient fragment is incomplete")
        chunks.append(payload)
    raw = b"".join(chunks)
    if expected_count is not None and len(raw) != expected_count * 4:
        raise ValueError("Native coefficient count differs from the channel budget")
    if len(raw) > 16384 * 4:
        raise ValueError("Coefficient stream exceeds the supported vector size")
    values = deserialize_coefficients(raw, fixed)
    return {**routing, "packet_count": len(packets), "coefficient_count": len(values),
            "wire_sha256": hashlib.sha256(raw).hexdigest(), "values": values}


def audit_coefficient_streams(packets, manifest, *, fixed=False):
    """Require both curves and all observed rate slots for every named output."""
    from .capabilities import capability
    if not manifest:
        raise ValueError("A complete native channel manifest is required")
    by_address = {}
    names = set()
    for entry in manifest:
        address = _byte(entry["address"])
        channel = entry["channel"]
        if address in by_address or channel in names:
            raise ValueError("Native channel names or addresses are duplicated")
        if entry["coefficient_count"] != capability(entry["tier"]).shape(channel)[2]:
            raise ValueError("Native manifest differs from the Audyssey vector budget")
        by_address[address] = entry
        names.add(channel)
    seen, rows, current = set(), [], []
    for packet in packets:
        # Decode/check each frame before using even its segmentation fields.
        parse_frame(packet)
        current.append(packet)
        if packet[3] != len(current) - 1:
            raise ValueError("Coefficient stream segmentation is inconsistent")
        if packet[3] != packet[4]:
            continue
        stream = decode_coefficient_stream(current, fixed=fixed)
        entry = by_address.get(stream["address"])
        if entry is None:
            raise ValueError("Coefficient stream targets an unreviewed native channel address")
        key = (stream["address"], stream["curve"], stream["sample_rate_slot"])
        if key in seen:
            raise ValueError("A native channel/curve/rate stream is duplicated")
        if stream["coefficient_count"] != entry["coefficient_count"] or stream["wire_sha256"] != entry["wire_sha256"]:
            raise ValueError("Wire coefficients differ from the reviewed native manifest")
        seen.add(key)
        rows.append({"channel": entry["channel"], **{k: v for k, v in stream.items() if k != "values"}})
        current = []
    if current:
        raise ValueError("The final coefficient stream is incomplete")
    expected = set(itertools.product(by_address, CURVES, SAMPLE_RATE_SLOTS))
    if seen != expected:
        raise ValueError("Native transfer does not cover every channel, both curves and all sample-rate slots")
    return {"all_streams_complete": True, "channels": len(by_address), "streams": len(rows),
            "packets": len(packets), "packet_sha256": packet_digest(packets),
            "sample_rate_slots_hz": dict(SAMPLE_RATE_SLOTS), "curves": list(CURVES), "rows": rows,
            "hardware_verified": False,
            "notice": "Offline byte/coverage audit. Reusing the observed vector in each slot does not verify sample-rate-dependent AVR reconstruction."}
