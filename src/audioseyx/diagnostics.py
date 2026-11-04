import platform
import numpy as np

from . import __version__
from .model import Measurement
from .dsp import design_channel
from .native import compile_channel, frame
from .compat import CompatibilityDataUnavailable, compatibility_status


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
    return {"version": __version__, "platform": platform.platform(), "all_passed": all(c["passed"] for c in checks),
            "checks": checks, "compatibility": compatibility_status(), "hardware_tested": False,
            "notice": 'Offline design self-test. Native encoding checks are explicitly marked unexecuted when local compatibility data are missing. No sound is played and no receiver is connected.'}
