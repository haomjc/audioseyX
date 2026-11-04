'Calibration coefficient transfer, not firmware modification. Requires explicit review before execution.'
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import socket
import time
import sys
import re

import numpy as np

from .capabilities import identify_tier, capability
from .native import frame, parse_frame, coefficient_packets, compile_channel
from .receiver import private_host
from .importers import value
from .model import ALIASES
from .compat import load_channel_addresses


def canonical_model(model):
    text = str(model).upper().strip()
    match = re.search(r"\b(AV[RPC]-[A-Z0-9]+)\b", text)
    if match:
        return match[1]
    return re.sub(r"^(DENON|MARANTZ)\s+", "", text)


def validate_template(template, allow_settings_only=False):
    if not isinstance(template, dict) or not template.get("model") or not identify_tier(template.get("eqType")):
        raise ValueError('An existing OCA with model, tier and complete channel configuration is required')
    channels = template.get("channels")
    if not isinstance(channels, list) or not channels:
        raise ValueError('The OCA contains no channels')
    ids = [str(c.get("commandId", "")) for c in channels]
    if len(set(ids)) != len(ids) or any(not identifier for identifier in ids):
        raise ValueError('OCA channel identifiers are duplicated or missing')
    if not isinstance(template.get("hasGriffinLiteDSP"), bool):
        raise ValueError('The OCA lacks DSP format information; channel addresses cannot be guessed')
    cap = capability(identify_tier(template["eqType"]))
    settings_only = allow_settings_only and template.get("_source_kind") == "native_settings_snapshot"
    for channel in channels:
        if settings_only:
            continue
        for key in ["distanceInMeters", "trimAdjustmentInDbs"]:
            if not isinstance(channel.get(key), (int, float)) or not np.isfinite(channel[key]):
                raise ValueError('The OCA lacks valid distance or trim settings')
        if not -12 <= channel["trimAdjustmentInDbs"] <= 12:
            raise ValueError('OCA trim settings exceed the supported range')
        if channel.get("speakerType") not in ["L", "S", "E"]:
            raise ValueError('Invalid OCA speaker type')
        expected = cap.shape(channel["commandId"])[0]
        for key in ["filter", "filterLV"]:
            coefficients = np.asarray(channel.get(key, []), dtype=float)
            if coefficients.ndim != 1 or len(coefficients) != expected or not np.isfinite(coefficients).all():
                raise ValueError('Supply an OCA containing both complete original filter vectors so a recovery copy can be retained')
    return template


def template_from_receiver(receiver, design):
    if not receiver or not receiver.get("connected"):
        raise ValueError('Connect the receiver first')
    info, status = receiver["info"], receiver["status"]
    model = value(info, "Model", "ModelName", "targetModelName")
    if not model:
        raise ValueError('The device model has not been read. Use discovery or supply an original OCA with complete information')
    reported = identify_tier(value(info, "EqType", "enMultEQType"))
    known_catalog = {"AVR-X3700H": "XT32", "AVC-X3700H": "XT32"}
    reported = reported or known_catalog.get(canonical_model(model))
    if reported != design["tier"]:
        raise ValueError('The receiver tier is unconfirmed or differs from the design; supply a complete OCA')
    ids = [ALIASES.get(str(key), str(key)) for row in status.get("ChSetup", []) for key, setting in row.items() if setting != "N"]
    addresses = load_channel_addresses()
    family = "eq2" if reported == "XT32" else "neq2"
    for identifier in ids:
        row = addresses.get(identifier, {})
        if row.get(family) is None or (reported == "XT32" and row.get("eq2") != row.get("griffin")):
            raise ValueError('This layout requires explicit DSP-family information; supply the original OCA')
    return {"model": canonical_model(model), "eqType": {"MultEQ": 0, "XT": 1, "XT32": 2}[reported],
            "hasGriffinLiteDSP": False, "channels": [{"commandId": identifier} for identifier in ids],
            "_source_kind": "native_settings_snapshot", "_has_original_coefficients": False,
            "_notice": 'A device-settings snapshot only; original filters are not included. Recovery may require rerunning stock calibration.'}


def prepare_transfer(project, design, template, receiver):
    validate_template(template, allow_settings_only=True)
    settings_only = template.get("_source_kind") == "native_settings_snapshot"
    if project.origin == "synthetic":
        raise ValueError('Synthetic demonstration measurements must not be transferred to a real receiver')
    if design["settings"].get("phase_mode", "minimum") != "minimum":
        raise ValueError('Mixed-phase designs are export-only until native magnitude and phase have been verified')
    if not design["settings"].get("uncorrected_measurements_confirmed"):
        raise ValueError('Confirm that the design measurements do not already include existing correction to avoid double compensation')
    if not receiver or not receiver.get("connected"):
        raise ValueError('Connect the receiver and read its information first')
    tier = identify_tier(template["eqType"])
    if tier != design["tier"]:
        raise ValueError('The OCA and design Audyssey tiers differ')
    info, status = receiver["info"], receiver["status"]
    model = value(info, "Model", "ModelName", "targetModelName")
    if not model or canonical_model(model) != canonical_model(template["model"]):
        raise ValueError('The receiver model does not match the OCA or no verifiable model was reported')
    if status.get("Mic") is True:
        raise ValueError('The calibration microphone is still connected. End native capture and unplug it first')
    raw_mapping = {ALIASES.get(str(key), str(key)): str(key) for row in status.get("ChSetup", []) for key, setting in row.items() if setting != "N"}
    active = set(raw_mapping)
    configured = {ALIASES.get(channel["commandId"], channel["commandId"]) for channel in template["channels"]}
    if active != configured:
        raise ValueError('The receiver layout differs from the OCA; check the channel configuration')
    if set(project.channels) != configured:
        raise ValueError('Measurements do not cover every OCA channel; partial replacement is not performed')
    dtype = str(value(info, "DType", default="")).lower()
    if not dtype.startswith(("fixed", "float")):
        raise ValueError('The receiver coefficient numeric format could not be confirmed')
    fixed = dtype.startswith("fixed")
    addresses = load_channel_addresses()
    family = "griffin" if template["hasGriffinLiteDSP"] else "eq2" if tier == "XT32" else "neq2"
    out = copy.deepcopy(template)
    for channel in out["channels"]:
        channel["commandId"] = ALIASES.get(channel["commandId"], channel["commandId"])
    designs = {item["channel"]: item for item in design["channels"]}
    packets = []
    used = set()
    adjustments = []
    global_scale = 1.0
    if settings_only:
        peaks = []
        for item in design["channels"]:
            peaks += [float(np.max(np.abs(item["taps"]))), float(np.max(np.abs(compile_channel(item, tier)["coefficients"])))]
        global_scale = min(1.0, 0.5 / max(max(peaks), 1e-15))
    for channel in out["channels"]:
        name = channel["commandId"]
        address = addresses.get(name, {}).get(family)
        if address is None or address in used:
            raise ValueError('Unverified or conflicting channel addresses for this device family: ' + name)
        used.add(address)
        item = copy.copy(designs[name])
        native = compile_channel(item, tier)["coefficients"]
        peak = max(float(np.max(np.abs(native))), float(np.max(np.abs(item["taps"]))))
        scale = global_scale if settings_only else min(1.0, 0.5 / max(peak, 1e-15))
        compensation = -20 * np.log10(scale)
        trim = None if settings_only else float(channel["trimAdjustmentInDbs"]) + float(compensation)
        if trim is not None and not -12 <= trim <= 12:
            raise ValueError(f"{name} coefficient gain compensation exceeds the trim budget; reduce target boost and redesign")
        if trim is not None:
            channel["trimAdjustmentInDbs"] = round(trim, 1)
        item["taps"] = item["taps"] * scale
        native = compile_channel(item, tier)["coefficients"]
        channel["filter"] = item["taps"].tolist()
        channel["filterLV"] = channel["filter"].copy()
        adjustments.append({"channel": name, "coefficient_scale": scale, "trim_db": channel.get("trimAdjustmentInDbs")})
        for curve in [0, 1]:
            for rate in [0, 1, 2]:
                packets.extend(coefficient_packets(native, address, curve, rate, fixed))
    settings = {"AudyFinFlg": "NotFin", "AudyDynEq": False, "AudyDynVol": False,
                "AudyMultEq": True, "AudyEqSet": "Flat"}
    level = None if settings_only else {"ChLevel": [{raw_mapping[c["commandId"]]: round(c["trimAdjustmentInDbs"] * 10)} for c in out["channels"]]}
    if settings_only:
        out["schema"] = "audyssey-workbench-calibration-1"
        out["global_headroom_db"] = float(-20 * np.log10(global_scale))
    digest = hashlib.sha256(json.dumps(out, sort_keys=True, allow_nan=False).encode()).hexdigest()
    summary = {"digest": digest, "model": model, "host": receiver["host"], "tier": tier,
               "channel_count": len(out["channels"]), "coefficient_packets": len(packets),
               "adjustments": adjustments, "hardware_verified": False,
               "original_filter_backup_available": not settings_only,
               "global_headroom_db": float(-20 * np.log10(global_scale)) if settings_only else 0,
               "notice": 'Experimental transfer replaces both current calibration curves and disables Dynamic EQ and Dynamic Volume. Firmware, crossovers, distances and amplifier assignments are not changed.'}
    return {"summary": summary, "packets": packets, "calibration": out, "settings": settings,
            "levels": level, "fixed": fixed}


class CalibrationTransport:
    def __init__(self, connection):
        self.connection = connection
        self.buffer = bytearray()

    def send_ack(self, packet, timeout=10):
        expected = packet[5:15].decode("ascii")
        self.connection.sendall(packet)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.connection.settimeout(min(2, max(0.1, deadline - time.monotonic())))
            try:
                chunk = self.connection.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                raise ValueError('The receiver closed the calibration connection')
            self.buffer.extend(chunk)
            while len(self.buffer) >= 3:
                length = int.from_bytes(self.buffer[1:3], "big")
                if not 19 <= length <= 65535:
                    raise ValueError('Invalid calibration response frame length')
                if len(self.buffer) < length:
                    break
                response = bytes(self.buffer[:length])
                del self.buffer[:length]
                command, payload = parse_frame(response)
                if command != expected:
                    continue
                message = payload.decode("utf-8", errors="replace").upper()
                if "NAK" in message or "NACK" in message or "ERROR" in message:
                    raise ValueError('The receiver rejected ' + expected)
                if "ACK" in message:
                    return
        raise ValueError('Timed out waiting for receiver acknowledgement: ' + expected)


def apply_plan(plan, progress=None):
    host = private_host(plan["summary"]["host"])
    with socket.create_connection((host, 1256), timeout=5) as connection:
        transport = CalibrationTransport(connection)
        entered = False
        try:
            entered = True
            transport.send_ack(frame("ENTER_AUDY"))
            transport.send_ack(frame("SET_SETDAT", json.dumps(plan["settings"], separators=(",", ":")).encode()))
            if plan["levels"] is not None:
                transport.send_ack(frame("SET_SETDAT", json.dumps(plan["levels"], separators=(",", ":")).encode()))
            if plan["fixed"]:
                transport.send_ack(frame("INIT_COEFS"), timeout=20)
            for index, packet in enumerate(plan["packets"]):
                transport.send_ack(packet)
                if progress and index % 10 == 0:
                    progress(round((index + 1) / len(plan["packets"]) * 90), 'Transferring and waiting for receiver acknowledgement')
            transport.send_ack(frame("FINZ_COEFS"), timeout=120 if plan["fixed"] else 30)
            transport.send_ack(frame("SET_SETDAT", b'{"AudyFinFlg":"Fin"}'))
        finally:
            if entered:
                pending = sys.exc_info()[1]
                try:
                    transport.send_ack(frame("EXIT_AUDMD"), timeout=10)
                except (OSError, ValueError) as error:
                    raise ValueError((str(pending) + "；" if pending else "") + 'Receiver calibration exit was not acknowledged: ' + str(error)) from error
