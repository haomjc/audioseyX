'Calibration coefficient transfer, not firmware modification. Requires explicit review before execution.'
from __future__ import annotations

import copy
import hashlib
import hmac
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
from .coefficient_transport import quantization_report, audit_coefficient_streams, packet_digest
from .native_protocol import acknowledgement

PLAN_SCHEMA = "audioseyx-native-transfer-2"


def _control_packets(plan):
    def payload(value):
        return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    before = [frame("ENTER_AUDY"), frame("SET_SETDAT", payload(plan["settings"]))]
    if plan["levels"] is not None:
        before.append(frame("SET_SETDAT", payload(plan["levels"])))
    if plan["fixed"]:
        before.append(frame("INIT_COEFS"))
    completion = [frame("FINZ_COEFS"), frame("SET_SETDAT", b'{"AudyFinFlg":"Fin"}')]
    return before, completion, frame("EXIT_AUDMD")


def transfer_plan_digest(plan):
    """Commit to the reviewed settings, target device and exact outgoing bytes."""
    before, completion, exit_packet = _control_packets(plan)
    document = {"schema": plan["schema"],
                "summary": {k: v for k, v in plan["summary"].items() if k != "digest"},
                "calibration": plan["calibration"], "settings": plan["settings"],
                "levels": plan["levels"], "fixed": plan["fixed"],
                "native_manifest": plan["native_manifest"],
                "coefficient_packet_sha256": packet_digest(plan["packets"]),
                "control_packet_sha256": packet_digest(before + completion + [exit_packet])}
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def audit_transfer_plan(plan):
    """Validate a complete prepared transaction before opening any socket."""
    try:
        if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
            raise ValueError("Prepare a current native transfer plan before execution")
        digest = plan["summary"]["digest"]
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid reviewed native transfer digest")
        if not hmac.compare_digest(digest, transfer_plan_digest(plan)):
            raise ValueError("The reviewed native transfer plan changed; prepare and review it again")
        audit = audit_coefficient_streams(plan["packets"], plan["native_manifest"], fixed=plan["fixed"])
        if audit != plan["summary"]["coefficient_transport_audit"]:
            raise ValueError("Native transfer audit differs from the reviewed summary")
        return audit
    except (KeyError, TypeError, OverflowError) as error:
        raise ValueError("The native transfer plan is incomplete or malformed") from error


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
    area=design.get('settings',{}).get('listening_area_resolution')
    if area and not area.get('required_seats_measured'):
        raise ValueError('A provisional listening-area design with missing measured seats cannot be prepared as final row calibration')
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
    if len(configured) != len(template["channels"]):
        raise ValueError('OCA aliases refer to duplicated canonical channels')
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
    if len(designs) != len(design["channels"]) or set(designs) != configured:
        raise ValueError('The design must contain exactly one complete filter for every configured channel')
    # Compile before gain planning, then again only after the actual scaling.
    # Keep the observed encoder calculation rather than assuming a synthesis
    # model or changing coefficient rounding through algebraic shortcuts.
    encoded = {name: compile_channel(item, tier) for name, item in designs.items()}
    packets = []
    native_manifest = []
    used = set()
    adjustments = []
    global_scale = 1.0
    if settings_only:
        peaks = []
        for name, item in designs.items():
            peaks += [float(np.max(np.abs(item["taps"]))), float(np.max(np.abs(encoded[name]["coefficients"])))]
        global_scale = min(1.0, 0.5 / max(max(peaks), 1e-15))
    for channel in out["channels"]:
        name = channel["commandId"]
        address = addresses.get(name, {}).get(family)
        if address is None or address in used:
            raise ValueError('Unverified or conflicting channel addresses for this device family: ' + name)
        used.add(address)
        item = copy.copy(designs[name])
        native = encoded[name]["coefficients"]
        peak = max(float(np.max(np.abs(native))), float(np.max(np.abs(item["taps"]))))
        scale = global_scale if settings_only else min(1.0, 0.5 / max(peak, 1e-15))
        compensation = -20 * np.log10(scale)
        trim = None if settings_only else float(channel["trimAdjustmentInDbs"]) + float(compensation)
        if trim is not None and not -12 <= trim <= 12:
            raise ValueError(f"{name} coefficient gain compensation exceeds the trim budget; reduce target boost and redesign")
        if trim is not None:
            channel["trimAdjustmentInDbs"] = round(trim, 1)
        item["taps"] = item["taps"] * scale
        compiled = compile_channel(item, tier)
        native = compiled["coefficients"]
        channel["filter"] = item["taps"].tolist()
        channel["filterLV"] = channel["filter"].copy()
        native_manifest.append({"channel": name, "address": address, "tier": tier,
                                "codec": compiled["codec"],
                                **quantization_report(native, fixed)})
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
    wire_audit = audit_coefficient_streams(packets, native_manifest, fixed=fixed)
    summary = {"model": model, "host": receiver["host"], "tier": tier,
               "channel_count": len(out["channels"]), "coefficient_packets": len(packets),
               "adjustments": adjustments, "hardware_verified": False,
               "original_filter_backup_available": not settings_only,
               "global_headroom_db": float(-20 * np.log10(global_scale)) if settings_only else 0,
               "coefficient_transport_audit": wire_audit,
               "coefficient_quantization": {
                   "maximum_absolute_error": max(row["maximum_absolute_error"] for row in native_manifest),
                   "underflowed_values": sum(row["underflowed_values"] for row in native_manifest),
                   "scope": "coefficient_values_only; native_DSP_reconstruction_unverified"},
               "notice": 'Experimental transfer replaces both current calibration curves and disables Dynamic EQ and Dynamic Volume. Firmware, crossovers, distances and amplifier assignments are not changed.'}
    plan = {"schema": PLAN_SCHEMA, "summary": summary, "packets": packets, "calibration": out,
            "settings": settings, "levels": level, "fixed": fixed, "native_manifest": native_manifest}
    summary["digest"] = transfer_plan_digest(plan)
    return plan


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
            if len(self.buffer) > 512 * 1024:
                raise ValueError('The calibration acknowledgement buffer exceeded its limit')
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
                if not acknowledgement(payload):
                    raise ValueError('The receiver rejected ' + expected)
                return
        raise ValueError('Timed out waiting for receiver acknowledgement: ' + expected)


def apply_plan(plan, progress=None):
    # Keep the already reviewed transaction independent of caller/UI changes
    # while waiting for the receiver's acknowledgements.
    plan = copy.deepcopy(plan)
    audit_transfer_plan(plan)
    before, completion, exit_packet = _control_packets(plan)
    host = private_host(plan["summary"]["host"])
    with socket.create_connection((host, 1256), timeout=5) as connection:
        transport = CalibrationTransport(connection)
        entered = False
        try:
            entered = True
            for packet in before:
                transport.send_ack(packet, timeout=20 if packet[5:15] == b"INIT_COEFS" else 10)
            for index, packet in enumerate(plan["packets"]):
                transport.send_ack(packet)
                if progress and index % 10 == 0:
                    progress(round((index + 1) / len(plan["packets"]) * 90), 'Transferring and waiting for receiver acknowledgement')
            for packet in completion:
                transport.send_ack(packet, timeout=(120 if plan["fixed"] else 30) if packet[5:15] == b"FINZ_COEFS" else 10)
        finally:
            if entered:
                pending = sys.exc_info()[1]
                try:
                    transport.send_ack(exit_packet, timeout=10)
                except (OSError, ValueError) as error:
                    raise ValueError((str(pending) + "；" if pending else "") + 'Receiver calibration exit was not acknowledged: ' + str(error)) from error
