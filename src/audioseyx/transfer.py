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
from .receiver_constraints import validate_receiver_proposal
from .calibration_curves import CURVE_SPECS, PROFILE_KEYS, curve_designs, fir_fingerprint
from .headroom import required_headroom_db

PLAN_SCHEMA = "audioseyx-native-transfer-4"


def _control_packets(plan):
    def payload(value):
        return json.dumps(value, separators=(",", ":"), allow_nan=False).encode()
    before = [frame("ENTER_AUDY"), frame("SET_SETDAT", payload(plan["settings"]))]
    for update in plan.get('receiver_updates', []):
        if not isinstance(update, dict) or len(update) != 1 or next(iter(update)) not in ['Distance', 'Crossover']:
            raise ValueError('Unexpected receiver-setting operation in the reviewed native plan')
        before.append(frame('SET_SETDAT', payload(update)))
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
                "receiver_updates": plan.get('receiver_updates', []),
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
        channels = {c['commandId']:c for c in plan['calibration']['channels']}
        if len(channels)!=len(plan['calibration']['channels']) or set(channels)!={r['channel'] for r in plan['native_manifest']}:
            raise ValueError('Calibration and reviewed coefficient manifests cover different outputs')
        for entry in plan['native_manifest']:
            for name, spec in CURVE_SPECS.items():
                details = entry.get('curves', {}).get(str(spec['curve_id']), {})
                taps = channels[entry['channel']].get(spec['calibration_field'], [])
                if len(taps)!=capability(entry['tier']).shape(entry['channel'])[0] or details.get('fir_sha256')!=fir_fingerprint(taps):
                    raise ValueError('Calibration curve FIRs differ from the reviewed slot mapping')
        initial = plan['summary']['initial_curve']
        if initial not in CURVE_SPECS or plan['settings'].get('AudyEqSet')!=initial:
            raise ValueError('Initial curve selection differs from the reviewed native plan')
        expected_settings = {'AudyFinFlg':'NotFin', 'AudyDynEq':False, 'AudyDynVol':False,
                             'AudyMultEq':True, 'AudyEqSet':initial}
        if (plan['settings']!=expected_settings or plan['settings']['AudyDynEq'] is not False or
            plan['settings']['AudyDynVol'] is not False or plan['settings']['AudyMultEq'] is not True):
            raise ValueError('Unexpected calibration control settings in the reviewed native plan')
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
    if design.get('project_id')!=project.id:
        raise ValueError('The filter design belongs to another measurement project; redesign before preparing transfer')
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
    receiver_proposal = None
    if design['settings'].get('apply_receiver_adjustments'):
        if canonical_model(design.get('receiver_calibration', {}).get('model')) != canonical_model(model):
            raise ValueError('The modeled receiver and connected receiver differ')
        receiver_proposal = validate_receiver_proposal(design.get('receiver_calibration'), receiver.get('web_settings', {}), configured)
    dtype = str(value(info, "DType", default="")).lower()
    if not dtype.startswith(("fixed", "float")):
        raise ValueError('The receiver coefficient numeric format could not be confirmed')
    fixed = dtype.startswith("fixed")
    addresses = load_channel_addresses()
    family = "griffin" if template["hasGriffinLiteDSP"] else "eq2" if tier == "XT32" else "neq2"
    out = copy.deepcopy(template)
    status_types = {ALIASES.get(str(key), str(key)): setting for row in status.get('ChSetup', []) for key, setting in row.items() if setting != 'N'}
    for channel in out["channels"]:
        channel["commandId"] = ALIASES.get(channel["commandId"], channel["commandId"])
        if settings_only:
            kind = status_types[channel['commandId']]
            if kind not in ['S', 'L', 'E']: raise ValueError('Unrecognized native speaker type in the settings snapshot')
            channel['speakerType'] = kind
        if receiver_proposal:
            name = channel['commandId']
            channel['distanceInMeters'] = receiver_proposal['distances_m'][name]
            channel['trimAdjustmentInDbs'] = receiver_proposal['levels_db'][name]
            if name in receiver_proposal['crossovers_hz']: channel['xover'] = receiver_proposal['crossovers_hz'][name]
    curves = curve_designs(design)
    initial_curve = design['settings'].get('initial_playback_curve', 'Flat')
    if initial_curve not in CURVE_SPECS:
        raise ValueError('Choose Flat or Reference as the initial receiver curve')
    designs = {name:{item['channel']:item for item in curve['channels']} for name,curve in curves.items()}
    for name, curve in curves.items():
        if len(designs[name])!=len(curve['channels']) or set(designs[name])!=configured:
            raise ValueError('Each calibration curve must contain exactly one complete filter for every configured channel')
    # Compile before gain planning, then again only after the actual scaling.
    # Keep the observed encoder calculation rather than assuming a synthesis
    # model or changing coefficient rounding through algebraic shortcuts.
    encoded = {curve:{name:compile_channel(item, tier) for name,item in items.items()}
               for curve,items in designs.items()}
    packets = []
    native_manifest = []
    used = set()
    adjustments = []
    global_scale = 1.0
    if settings_only:
        peaks = []
        for curve, items in designs.items():
            for name, item in items.items():
                peaks += [float(np.max(np.abs(item["taps"]))), float(np.max(np.abs(encoded[curve][name]["coefficients"])))]
        global_scale = 10 ** (-required_headroom_db(max(peaks))/20)
    for channel in out["channels"]:
        name = channel["commandId"]
        address = addresses.get(name, {}).get(family)
        if address is None or address in used:
            raise ValueError('Unverified or conflicting channel addresses for this device family: ' + name)
        used.add(address)
        peak = max(max(float(np.max(np.abs(encoded[curve][name]['coefficients']))),
                       float(np.max(np.abs(designs[curve][name]['taps'])))) for curve in curves)
        scale = global_scale if settings_only else 10 ** (-required_headroom_db(peak)/20)
        compensation = -20 * np.log10(scale)
        trim = None if settings_only and not receiver_proposal else float(channel["trimAdjustmentInDbs"]) + float(compensation)
        if trim is not None and not -12 <= trim <= 12:
            raise ValueError(f"{name} coefficient gain compensation exceeds the trim budget; reduce target boost and redesign")
        if trim is not None:
            channel["trimAdjustmentInDbs"] = round(trim, 1)
        manifest = {'channel':name, 'address':address, 'tier':tier,
                    'coefficient_count':capability(tier).shape(name)[2], 'curves':{}}
        for curve, spec in CURVE_SPECS.items():
            item = copy.copy(designs[curve][name])
            if receiver_proposal and name in receiver_proposal['inverted_outputs']:
                item['taps'] = -item['taps']
                if '_native_parameters' in item:
                    item['_native_parameters'] = -item['_native_parameters']
                    item['_native_parameter_taps'] = -item['_native_parameter_taps']
            item['taps'] = item['taps'] * scale
            compiled = compile_channel(item, tier)
            native = compiled['coefficients']
            channel[spec['calibration_field']] = item['taps'].tolist()
            manifest['curves'][str(spec['curve_id'])] = {
                'curve_name':curve, 'calibration_field':spec['calibration_field'],
                'codec':compiled['codec'], 'fir_sha256':fir_fingerprint(item['taps']),
                **quantization_report(native, fixed)}
            for rate in [0,1,2]:
                packets.extend(coefficient_packets(native, address, spec['curve_id'], rate, fixed))
        native_manifest.append(manifest)
        adjustments.append({"channel": name, "coefficient_scale": scale, "trim_db": channel.get("trimAdjustmentInDbs"),
                            'filter_polarity_inverted': bool(receiver_proposal and name in receiver_proposal['inverted_outputs'])})
    settings = {"AudyFinFlg": "NotFin", "AudyDynEq": False, "AudyDynVol": False,
                "AudyMultEq": True, "AudyEqSet": initial_curve}
    # SWMIX is a status alias, not the individual SET_SETDAT control identifier.
    controls = {c['commandId']: c['commandId'] if raw_mapping[c['commandId']].startswith('SWMIX') else raw_mapping[c['commandId']] for c in out['channels']}
    level = None if settings_only and not receiver_proposal else {"ChLevel": [{controls[c["commandId"]]: round(c["trimAdjustmentInDbs"] * 10)} for c in out["channels"]]}
    receiver_updates = []
    if receiver_proposal:
        current = receiver['web_settings']
        if any(abs(receiver_proposal['distances_m'][c] - current['distances_m'][c]) > 1e-8 for c in configured):
            receiver_updates.append({'Distance': [{controls[c]: round(receiver_proposal['distances_m'][c] * 100)} for c in controls]})
        if receiver_proposal['crossovers_hz']:
            entries = []
            for c in controls:
                crossover_setting = 'F' if c.startswith('SW') else receiver_proposal['crossovers_hz'].get(c, current['crossovers'].get(c, 'F'))
                if crossover_setting != 'F': crossover_setting = int(round(crossover_setting))
                entries.append({controls[c]: crossover_setting})
            receiver_updates.append({'Crossover': entries})
    if settings_only:
        out["schema"] = "audyssey-workbench-calibration-1"
        out["global_headroom_db"] = float(-20 * np.log10(global_scale))
    wire_audit = audit_coefficient_streams(packets, native_manifest, fixed=fixed)
    precision_rows = [details for entry in native_manifest for details in entry['curves'].values()]
    summary = {"model": model, "host": receiver["host"], "tier": tier,
               "channel_count": len(out["channels"]), "coefficient_packets": len(packets),
               "adjustments": adjustments, "hardware_verified": False,
               'initial_curve':initial_curve, 'independent_curve_targets':design.get('curve_designs') is not None,
               'curve_slots':copy.deepcopy(CURVE_SPECS),
               'curve_targets':{name:{k:copy.deepcopy(v) for k,v in curve['settings'].items() if k in PROFILE_KEYS} for name,curve in curves.items()},
               'curve_headroom_rule':'One common scale per output across both curves; one global scale when baseline trims are unknown',
               "original_filter_backup_available": not settings_only,
               "global_headroom_db": float(-20 * np.log10(global_scale)) if settings_only else 0,
               "coefficient_transport_audit": wire_audit,
               'receiver_settings_included': bool(receiver_proposal),
               'receiver_settings_proposal': receiver_proposal,
               'receiver_model_assumptions': design.get('receiver_calibration', {}).get('assumptions') if receiver_proposal else None,
               'receiver_control_selection': copy.deepcopy(design.get('receiver_calibration', {}).get('control_selection')) if receiver_proposal else None,
               'receiver_measurement_robustness': copy.deepcopy(design.get('receiver_calibration', {}).get('measurement_robustness')) if receiver_proposal else None,
               'receiver_eq_refinement': copy.deepcopy(design.get('receiver_calibration', {}).get('eq_refinement')) if receiver_proposal else None,
               'headroom_compensated_by_channel_levels': bool(not settings_only or receiver_proposal),
               "coefficient_quantization": {
                   "maximum_absolute_error": max(row["maximum_absolute_error"] for row in precision_rows),
                   "underflowed_values": sum(row["underflowed_values"] for row in precision_rows),
                   "scope": "coefficient_values_only; native_DSP_reconstruction_unverified"},
               "notice": ('Experimental transfer includes the reviewed modeled speaker levels, distances, crossovers and subwoofer filter polarity. Firmware, bass mode and amplifier assignment are retained.' if receiver_proposal else 'Experimental transfer replaces both current calibration curves and disables Dynamic EQ and Dynamic Volume. Firmware, crossovers, distances and amplifier assignments are not changed.')}
    plan = {"schema": PLAN_SCHEMA, "summary": summary, "packets": packets, "calibration": out,
            "settings": settings, "levels": level, "fixed": fixed, "native_manifest": native_manifest,
            'receiver_updates': receiver_updates}
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
