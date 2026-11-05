"""Observed receiver framing, shared by read-only and calibration transports."""
from __future__ import annotations

import operator
import struct
import json


def _byte(value):
    if isinstance(value, bool):
        raise ValueError("A protocol byte must be an integer")
    try:
        value = operator.index(value)
    except TypeError as error:
        raise ValueError("A protocol byte must be an integer") from error
    if not 0 <= value <= 255:
        raise ValueError("A protocol byte must be between 0 and 255")
    return value


def frame(command, payload=b"", sequence=0, last=0):
    if not isinstance(command, str) or not isinstance(payload, (bytes, bytearray, memoryview)):
        raise ValueError("Invalid receiver command or payload type")
    try:
        command_bytes = command.encode("ascii")
    except UnicodeEncodeError as error:
        raise ValueError("Receiver commands must be ASCII") from error
    payload = bytes(payload)
    if len(command_bytes) != 10 or len(payload) > 65500:
        raise ValueError("Invalid receiver command or frame length")
    body = bytes([_byte(sequence), _byte(last)]) + command_bytes + b"\x00" + struct.pack(">H", len(payload)) + payload
    packet = b"\x54" + struct.pack(">H", len(body) + 4) + body
    return packet + bytes([sum(packet) & 255])


def parse_frame(packet):
    if not isinstance(packet, (bytes, bytearray, memoryview)) or len(packet) < 19 or packet[0] not in (0x52, 0x54):
        raise ValueError("Invalid receiver response frame header")
    length = struct.unpack_from(">H", packet, 1)[0]
    if length != len(packet) or sum(packet[:-1]) & 255 != packet[-1]:
        raise ValueError("Receiver response length or checksum mismatch")
    command = bytes(packet[5:15]).decode("ascii")
    payload_size = struct.unpack_from(">H", packet, 16)[0]
    # Preserve the existing receive-side tolerance. Outgoing coefficient
    # auditing separately requires exact lengths and reserved-byte values.
    if payload_size + 19 > len(packet):
        raise ValueError("Invalid receiver response payload length")
    return command, bytes(packet[18:18 + payload_size])


def acknowledgement(payload):
    """Read the explicit observed ACK field; substring matches are insufficient."""
    message = payload.decode("utf-8", errors="strict").strip("\x00 \r\n\t")
    try:
        value = json.loads(message)
    except ValueError:
        status = message.upper()
    else:
        status = value.get("Comm", "") if isinstance(value, dict) else value if isinstance(value, str) else ""
        status = status.upper() if isinstance(status, str) else ""
    if status == "ACK":
        return True
    if status in ["NAK", "NACK"]:
        return False
    raise ValueError("The receiver did not send an explicit command acknowledgement")
