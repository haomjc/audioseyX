from __future__ import annotations

import json
from pathlib import Path
import struct
import numpy as np

from .capabilities import capability
from .compat import load_native_tables


def pack_xt32(samples, is_sub=False):
    counts = [96, 96, 256, 239] if is_sub else [256, 256, 256, 235]
    names = ["decFilterXT32Sub29_taps", "decFilterXT32Sub37_taps", "decFilterXT32Sub93_taps"] if is_sub else ["decFilterXT32Sat129_taps"] * 3
    expected, packed_length = (16055, 704) if is_sub else (16321, 1024)
    residual = np.asarray(samples, dtype=float)
    if residual.ndim != 1 or len(residual) != expected or not np.isfinite(residual).all():
        raise ValueError('Invalid XT32 design vector length or values')
    tables = load_native_tables()
    blocks = []
    for count, name in zip(counts, names):
        kernel = np.asarray(tables[name], dtype=float)
        direct = (len(kernel) * 3 - 3) // 2
        fading = count - direct
        window_size = 2 * fading + 3
        window = 0.5 - 0.5 * np.cos(2 * np.pi * np.arange(window_size) / (window_size - 1))
        block = np.zeros(count)
        copied = min(count, len(residual))
        block[:copied] = residual[:copied]
        offset = window_size // 2 + 1
        block[direct:count] *= window[offset:offset + fading]
        rest = residual[direct:].copy()
        rest[:min(fading, len(rest))] -= block[direct:direct + min(fading, len(rest))]
        output_size = int(np.ceil((len(rest) + len(kernel) - 1) / 4))
        downsampled = np.zeros(output_size)
        for phase in range(4):
            if len(rest[phase::4]):
                convolution = np.convolve(rest[phase::4], kernel[phase::4])
                downsampled[:min(output_size, len(convolution))] += convolution[:output_size]
        blocks.append(block)
        residual = 4 * downsampled
    last = np.zeros(counts[-1])
    last[:min(len(last), len(residual))] = residual[:len(last)]
    result = np.zeros(packed_length)
    combined = np.concatenate(blocks + [last])
    result[:len(combined)] = combined
    return result


def compile_channel(item, tier):
    cap = capability(tier)
    length, fs, packed_length = cap.shape(item["channel"])
    taps = np.asarray(item["taps"], dtype=float)
    if len(taps) != length or item["sample_rate"] != fs or not np.isfinite(taps).all():
        raise ValueError('The filter does not match the device format')
    supplied = item.get("_native_parameters")
    if supplied is not None:
        supplied = np.asarray(supplied,dtype=float)
        base = np.asarray(item.get("_native_parameter_taps",[]),dtype=float)
        if supplied.shape != (packed_length,) or base.shape != taps.shape or not np.isfinite(supplied).all() or not np.any(base):
            raise ValueError("Supplied native parameters do not match this channel budget")
        pivot = int(np.argmax(np.abs(base)))
        scale = taps[pivot] / base[pivot]
        if not np.isfinite(scale) or not np.allclose(taps,base*scale,rtol=1e-8,atol=1e-10):
            raise ValueError("The FIR changed after native-parameter optimization; its native encoding is stale")
        coefficients = supplied * scale
        if not np.isfinite(coefficients).all():
            raise ValueError("Supplied native coefficients overflow after gain scaling")
    else:
        coefficients = pack_xt32(taps, item["channel"].startswith("SW")) if tier == "XT32" else taps.copy()
    return {"channel": item["channel"], "codec": "supplied_native_synthesis" if supplied is not None else cap.codec, "design_rate": fs,
            "design_length": length, "coefficient_count": len(coefficients),
            "coefficients": coefficients, "hardware_verified": False}


def frame(command, payload=b"", sequence=0, last=0):
    command_bytes = command.encode("ascii")
    if len(command_bytes) != 10 or len(payload) > 65500:
        raise ValueError('Invalid receiver command or frame length')
    body = bytes([sequence, last]) + command_bytes + b"\x00" + struct.pack(">H", len(payload)) + payload
    packet = b"\x54" + struct.pack(">H", len(body) + 4) + body
    return packet + bytes([sum(packet) & 255])


def parse_frame(packet):
    if len(packet) < 19 or packet[0] not in (0x52, 0x54):
        raise ValueError('Invalid receiver response frame header')
    length = struct.unpack_from(">H", packet, 1)[0]
    if length != len(packet) or sum(packet[:-1]) & 255 != packet[-1]:
        raise ValueError('Receiver response length or checksum mismatch')
    command = packet[5:15].decode("ascii")
    payload_size = struct.unpack_from(">H", packet, 16)[0]
    if payload_size + 19 > len(packet):
        raise ValueError('Invalid receiver response payload length')
    return command, packet[18:18 + payload_size]


def coefficient_packets(coefficients, channel_byte, curve=0, sample_rate_slot=2, fixed=False):
    data = np.asarray(coefficients, dtype=float)
    if not np.isfinite(data).all() or not 1 <= len(data) <= 16384:
        raise ValueError('Invalid coefficient payload')
    if np.max(np.abs(data)) > np.finfo(np.float32).max:
        raise ValueError('Coefficients exceed the float32 range')
    if fixed:
        if np.max(np.abs(data)) >= 1:
            raise ValueError('Fixed-point coefficients exceed Q31 range; silent saturation is not allowed')
        # Use the observed two's-complement-style quantization for negatives.
        integers = np.where(data < 0, -np.floor(np.abs(data) * (2 ** 31)) - 1, np.floor(data * (2 ** 31))).astype("<i4")
        raw = integers.tobytes()
    else:
        raw = data.astype("<f4").tobytes()
    chunks = [raw[:127 * 4]]
    chunks += [raw[i:i + 128 * 4] for i in range(127 * 4, len(raw), 128 * 4)]
    return [frame("SET_COEFDT", bytes([curve, sample_rate_slot, channel_byte, 0]) + chunk if i == 0 else chunk, i, len(chunks) - 1)
            for i, chunk in enumerate(chunks)]
