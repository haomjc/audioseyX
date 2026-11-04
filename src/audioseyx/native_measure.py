'Stock-microphone adapter for the observed Float protocol. Does not run a REW sweep.'
from __future__ import annotations

import json
import socket
import threading
import time
import sys

import numpy as np

from .native import frame, parse_frame
from .model import Measurement, Project, CHANNELS, ALIASES
from .capabilities import identify_tier
from .importers import value

CANONICAL = ["FL", "C", "FR", "FWR", "SRA", "SRB", "SBR", "SBL", "SLB", "SLA", "FWL", "CH",
             "FHR", "TFR", "FDR", "TMR", "TRR", "SHR", "RHR", "SDR", "BDR", "BDL", "SDL", "RHL", "SHL", "TRL", "TML", "FDL", "TFL", "FHL", "TS", "LFE", "SW1", "SW2", "SW3", "SW4"]


class CaptureCancelled(ValueError):
    pass


class NativeSession:
    def __init__(self, connection, cancel=None):
        self.connection = connection
        self.buffer = bytearray()
        self.cancel = cancel or threading.Event()

    def receive(self, deadline, cancellable=True):
        while time.monotonic() < deadline:
            if cancellable and self.cancel.is_set():
                raise CaptureCancelled('Stock-microphone capture cancellation requested')
            if len(self.buffer) >= 3:
                size = int.from_bytes(self.buffer[1:3], "big")
                if not 19 <= size <= 65535:
                    raise ValueError('Invalid measurement response frame length')
                if len(self.buffer) >= size:
                    packet = bytes(self.buffer[:size])
                    del self.buffer[:size]
                    command, payload = parse_frame(packet)
                    return command, payload, packet[3], packet[4]
            self.connection.settimeout(min(0.5, max(0.1, deadline - time.monotonic())))
            try:
                data = self.connection.recv(65536)
            except socket.timeout:
                continue
            if not data:
                raise ValueError('The receiver closed the measurement connection')
            self.buffer.extend(data)
            if len(self.buffer) > 1024 * 1024:
                raise ValueError('The measurement receive buffer exceeded its limit')
        raise ValueError('Timed out waiting for the receiver measurement response')

    def json_command(self, command, body=None, timeout=10, report=False, cancellable=True):
        payload = b"" if body is None else json.dumps(body, separators=(",", ":")).encode()
        self.connection.sendall(frame(command, payload))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            received, payload, _, _ = self.receive(deadline, cancellable)
            if received != command:
                continue
            data = json.loads(payload.decode("utf-8"))
            text = json.dumps(data).upper()
            if "NACK" in text or "NAK" in text or "ERROR" in text:
                raise ValueError('The receiver rejected the measurement command: ' + command)
            if report:
                if any(k in data for k in ["SpConnect", "ChReport", "Distance"]):
                    return data
            elif "ACK" in text:
                return data
        raise ValueError('No receiver acknowledgement received: ' + command)

    def download_response(self, channel):
        self.connection.sendall(frame("GET_RESPON", json.dumps({"ChData": channel}, separators=(",", ":")).encode()))
        deadline = time.monotonic() + 20
        chunks = {}
        expected_last = None
        while time.monotonic() < deadline:
            command, payload, current, last = self.receive(deadline)
            if command != "GET_RESPON":
                continue
            if payload.startswith(b"{") and payload.endswith(b"}"):
                try:
                    status = json.loads(payload)
                except (ValueError, UnicodeError):
                    status = None
                if isinstance(status, dict) and "Comm" in status:
                    if str(status["Comm"]).upper() in ["NAK", "NACK", "ERROR"]:
                        raise ValueError('The receiver rejected measurement-response download')
                    continue
            if current > last or (expected_last is not None and last != expected_last):
                raise ValueError('Inconsistent measurement packet sequence')
            expected_last = last
            if current in chunks and chunks[current] != payload:
                raise ValueError('Duplicate measurement packets contain different payloads')
            chunks[current] = payload
            if sum(map(len, chunks.values())) > 262144:
                raise ValueError('The measurement data exceed the supported size')
            if len(chunks) == last + 1:
                return b"".join(chunks[index] for index in range(last + 1))
        raise ValueError('The measurement response packets are incomplete')


def select_capture_outputs(status,channels=None):
    raw_ids=[str(key) for row in status.get("ChSetup",[]) for key,setting in row.items() if setting!="N"]
    normalized=[ALIASES.get(name,name) for name in raw_ids]
    if len(normalized)!=len(set(normalized)):
        raise ValueError("Receiver channel aliases are ambiguous; no capture was started")
    if any(name not in CHANNELS for name in normalized):
        raise ValueError("Unsupported measurement channels: "+", ".join(name for name in normalized if name not in CHANNELS))
    if channels is not None:
        if not isinstance(channels,list) or not channels or len(channels)!=len(set(channels)) or set(channels)-set(normalized):
            raise ValueError("Capture selection must list distinct receiver-configured outputs")
        raw_ids=[raw for raw,canonical in zip(raw_ids,normalized) if canonical in channels]
    if not raw_ids:raise ValueError("The receiver reported no selected measurable channels")
    return raw_ids


def capture_position(receiver, position=1, repeats=1, cancel=None, progress=None, on_measurement=None, settle_seconds=5, channels=None):
    if not receiver.get("connected"):
        raise ValueError('Connect the receiver first')
    info, status = receiver["info"], receiver["status"]
    if not str(value(info, "DType", default="")).lower().startswith("float"):
        raise ValueError('Stock-microphone capture currently supports Float devices. Use the stock app and import files for Fixed devices; their capture sequence needs separate adaptation')
    if status.get("Mic") is not True:
        raise ValueError("Plug the receiver's stock calibration microphone into its calibration input")
    if status.get("HPPlug") is True:
        raise ValueError('Disconnect the receiver headphone output before measuring speakers')
    if not 1 <= int(position) <= 20 or not 1 <= int(repeats) <= 5:
        raise ValueError('Position must be 1-20 and repeats must be 1-5')
    raw_ids=select_capture_outputs(status,channels)
    order = {name: index for index, name in enumerate(CANONICAL)}
    raw_ids.sort(key=lambda name: order.get(name, 999))
    event = cancel or threading.Event()
    if event.is_set():
        raise CaptureCancelled('Measurement preparation stopped')
    measurements = []
    with socket.create_connection((receiver["host"], 1256), timeout=5) as connection:
        session = NativeSession(connection, event)
        entered = False
        try:
            entered = True
            session.json_command("ENTER_AUDY")
            if event.wait(settle_seconds):
                raise CaptureCancelled('Measurement preparation stopped')
            # Keep each physical position in its own session; retain raw responses instead of firmware averaging.
            session.json_command("SET_POSNUM", {"Position": 1, "ChSetup": raw_ids})
            total = len(raw_ids) * repeats
            for index, raw_id in enumerate(raw_ids):
                channel = ALIASES.get(raw_id, raw_id)
                for repeat in range(repeats):
                    if progress:
                        progress(round((index * repeats + repeat) / total * 95), f"Position {position}: measuring {channel}（{repeat + 1}/{repeats}）")
                    report = session.json_command("START_CHNL", {"Channel": raw_id}, timeout=25, report=True)
                    details = report.get("ChReport", report)
                    if isinstance(details, list):
                        details = details[0] if details else {}
                    if not isinstance(details, dict):
                        raise ValueError('Unsupported receiver measurement report structure')
                    if details.get("SpConnect") == "N" or details.get("Distance") == 0:
                        raise ValueError(channel + ' has no valid speaker response; check connections and subwoofer standby')
                    raw = session.download_response(raw_id)
                    if len(raw) != 16384 * 4:
                        raise ValueError('Unsupported response length returned by this device: ' + str(len(raw)))
                    coefficient = float(details.get("ResponseCoef", report.get("ResponseCoef", 1)))
                    if not np.isfinite(coefficient) or not 0 < abs(coefficient) < 1e6:
                        raise ValueError('Invalid receiver response scaling factor')
                    samples = np.frombuffer(raw, dtype="<f4").astype(float) * coefficient
                    measurement = Measurement(channel, f"p{position - 1}", f"{channel} / position {position} / repeat {repeat + 1} time(s)", 48000, samples,
                                              timing_reference="native_receiver_clock_unverified",
                                              metadata={"repeat": repeat + 1, "report": details,
                                                        "receiver_raw_channel_id":raw_id,
                                                        "selected_capture_outputs":raw_ids,
                                                        "configured_outputs_not_selected":[str(key) for row in status.get("ChSetup",[]) for key,setting in row.items() if setting!="N" and str(key) not in raw_ids],
                                                        "system_delay_reported": value(info, "SysDelay", "SystemDelay"),
                                                        "receiver_mic_processing_verified": False})
                    measurements.append(measurement)
                    if on_measurement:
                        on_measurement(measurement)
        finally:
            if entered:
                pending = sys.exc_info()[1]
                if event.is_set():
                    try:
                        session.json_command("ABORT_OPRT", timeout=5, cancellable=False)
                    except (OSError, ValueError):
                        pass
                try:
                    session.json_command("EXIT_AUDMD", timeout=10, cancellable=False)
                except (OSError, ValueError) as error:
                    raise ValueError((str(pending) + "；" if pending else "") + 'Receiver calibration exit was not acknowledged: ' + str(error)) from error
    return measurements
