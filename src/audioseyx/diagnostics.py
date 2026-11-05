import platform
import numpy as np

from . import __version__
from .model import Measurement
from .dsp import design_channel
from .native import compile_channel, frame
from .compat import CompatibilityDataUnavailable, compatibility_status
from .capabilities import capability
from .coefficient_transport import coefficient_packets, quantization_report, audit_coefficient_streams


def self_test():
    checks = []
    for tier in ["MultEQ", "XT", "XT32"]:
        for channel in ["FL", "SW1"]:
            try:
                ir = np.zeros(2048)
                ir[100] = 1
                design = design_channel([Measurement(channel, "p0", "identity", 48000, ir)], tier, channel,
                                        {"bass_db": 0, "tilt_db_oct": 0})
                valid = abs(design["taps"][0] - 1) < 1e-8 and np.isfinite(design["taps"]).all()
                check = {"name": f"{tier} / {channel} design", "passed": bool(valid), "native_encoding_checked": False}
                try:
                    native = compile_channel(design, tier)
                    check.update(native_encoding_checked=True, coefficients=native["coefficient_count"])
                    check["passed"] = bool(valid and np.isfinite(native["coefficients"]).all())
                except CompatibilityDataUnavailable as error:
                    check["native_encoding_unavailable_reason"] = str(error)
                checks.append(check)
            except Exception as error:
                checks.append({"name": f"{tier} / {channel}", "passed": False, "error": str(error)})
    checks.append({"name": 'Known protocol frame', "passed": frame("ENTER_AUDY").hex() == "5400130000454e5445525f4155445900000077"})
    for tier in ["MultEQ", "XT", "XT32"]:
        for fixed in [False, True]:
            try:
                manifest, packets = [], []
                for address, channel in [(101, "FL"), (102, "SW1")]:
                    # Artificial transport vectors and addresses, with no
                    # dependency on the private XT32 synthesis/encoding data.
                    values = np.zeros(capability(tier).shape(channel)[2]); values[0] = .25
                    entry = {'channel':channel, 'address':address, 'tier':tier,
                             'coefficient_count':len(values), 'curves':{}}
                    for curve in [0, 1]:
                        curve_values = values if curve==0 else values*.5
                        entry['curves'][str(curve)] = quantization_report(curve_values, fixed)
                        for slot in [0, 1, 2]:
                            packets.extend(coefficient_packets(curve_values, address, curve, slot, fixed))
                    manifest.append(entry)
                audit = audit_coefficient_streams(packets, manifest, fixed=fixed)
                checks.append({"name": f"Synthetic native transport / {tier} / {'Q31' if fixed else 'Float32'}",
                               "passed": audit["all_streams_complete"] and audit['channels_with_distinct_curve_coefficients']==2,
                               "streams": audit["streams"], 'distinct_curve_outputs':audit['channels_with_distinct_curve_coefficients'],
                               "native_encoding_checked": False, "hardware_tested": False})
            except Exception as error:
                checks.append({"name": f"Synthetic native transport / {tier}", "passed": False, "error": str(error)})
    return {"version": __version__, "platform": platform.platform(), "all_passed": all(c["passed"] for c in checks),
            "checks": checks, "compatibility": compatibility_status(), "hardware_tested": False,
            "notice": 'Offline design self-test. Native encoding checks are explicitly marked unexecuted when local compatibility data are missing. No sound is played and no receiver is connected.'}
