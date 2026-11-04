import copy
import unittest
from unittest.mock import patch

import numpy as np

from audioseyx.model import Measurement, Project
from audioseyx.dsp import design_project
from audioseyx.native import frame, parse_frame
from audioseyx.transfer import validate_template, prepare_transfer, apply_plan, template_from_receiver, canonical_model


def example():
    impulse = np.zeros(2048)
    impulse[100] = 1
    project = Project("test", [Measurement("FL", "p0", "FL0", 48000, impulse),
                                Measurement("SW1", "p0", "SW1", 48000, impulse)], "unit_test")
    design = design_project(project, "MultEQ", {"phase_mode": "minimum", "uncorrected_measurements_confirmed": True,
                                               "bass_db": 0, "tilt_db_oct": 0})
    channels = []
    for name, size, kind in [("FL", 128, "S"), ("SW1", 512, "E")]:
        taps = [0.5] + [0.0] * (size - 1)
        channels.append({"commandId": name, "speakerType": kind, "distanceInMeters": 2.5,
                         "trimAdjustmentInDbs": -6.0, "filter": taps, "filterLV": taps.copy(), "xover": 80})
    template = {"model": "AVR-TEST", "eqType": 0, "hasGriffinLiteDSP": False, "channels": channels}
    receiver = {"connected": True, "host": "127.0.0.1", "info": {"Model": "AVR-TEST", "DType": "Float32"},
                "status": {"Mic": False, "ChSetup": [{"FL": "S"}, {"SW1": "E"}]}}
    return project, design, template, receiver


class FakeReceiver:
    def __init__(self, reject=None):
        self.reject = reject
        self.commands = []
        self.reply = b""

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def settimeout(self, *_):
        pass

    def sendall(self, packet):
        command, payload = parse_frame(packet)
        self.commands.append((command, payload))
        self.reply += frame(command, b'{"Comm":"NACK"}' if command == self.reject else b'{"Comm":"ACK"}')

    def recv(self, *_):
        result, self.reply = self.reply, b""
        return result


class TransferTests(unittest.TestCase):
    def setUp(self):
        # Artificial test receiver addresses, unrelated to any real AVR model.
        self.address_patch = patch("audioseyx.transfer.load_channel_addresses", return_value={
            "FL": {"eq2": 101, "neq2": 101, "griffin": 101},
            "SW1": {"eq2": 102, "neq2": 102, "griffin": 102}})
        self.address_patch.start()
        self.addCleanup(self.address_patch.stop)

    def test_model_labels_allow_brand_prefix_but_not_different_models(self):
        self.assertEqual(canonical_model("Denon AVR-X3700H"), "AVR-X3700H")
        self.assertNotEqual(canonical_model("AVR-X3700H"), canonical_model("AVR-X3800H"))
    def test_plan_keeps_configuration_and_fits_numeric_budget(self):
        project, design, template, receiver = example()
        original = copy.deepcopy(template)
        plan = prepare_transfer(project, design, template, receiver)
        self.assertEqual(template, original)
        self.assertEqual(plan["calibration"]["channels"][0]["distanceInMeters"], 2.5)
        self.assertEqual(plan["calibration"]["channels"][0]["xover"], 80)
        self.assertAlmostEqual(plan["calibration"]["channels"][0]["trimAdjustmentInDbs"], 0, delta=0.1)
        self.assertTrue(plan["packets"])
        self.assertFalse(plan["summary"]["hardware_verified"])

    def test_rejects_synthetic_mixed_unconfirmed_and_wrong_model(self):
        for case in ["synthetic", "mixed", "unconfirmed", "wrong-model", "missing-channel"]:
            project, design, template, receiver = example()
            if case == "synthetic":
                project.origin = "synthetic"
            elif case == "mixed":
                design["settings"]["phase_mode"] = "mixed"
            elif case == "unconfirmed":
                design["settings"].pop("uncorrected_measurements_confirmed")
            elif case == "wrong-model":
                receiver["info"]["Model"] = "OTHER"
            else:
                receiver["status"]["ChSetup"].pop()
            with self.subTest(case=case), self.assertRaises(ValueError):
                prepare_transfer(project, design, template, receiver)

    def test_complete_backup_requires_both_full_curve_vectors(self):
        _, _, template, _ = example()
        template["channels"][0]["filterLV"] = [1]
        with self.assertRaises(ValueError):
            validate_template(template)

    def test_simulated_receiver_confirms_full_transfer_sequence(self):
        plan = prepare_transfer(*example())
        receiver = FakeReceiver()
        with patch("audioseyx.transfer.socket.create_connection", return_value=receiver):
            apply_plan(plan)
        commands = [row[0] for row in receiver.commands]
        self.assertEqual(commands[0], "ENTER_AUDY")
        self.assertIn("FINZ_COEFS", commands)
        self.assertEqual(commands[-1], "EXIT_AUDMD")
        self.assertEqual(commands.count("SET_COEFDT"), len(plan["packets"]))

    def test_rejection_stops_writes_and_exits_calibration(self):
        plan = prepare_transfer(*example())
        receiver = FakeReceiver(reject="SET_COEFDT")
        with patch("audioseyx.transfer.socket.create_connection", return_value=receiver), self.assertRaises(ValueError):
            apply_plan(plan)
        commands = [row[0] for row in receiver.commands]
        self.assertEqual(commands.count("SET_COEFDT"), 1)
        self.assertNotIn("FINZ_COEFS", commands)
        self.assertEqual(commands[-1], "EXIT_AUDMD")

    def test_settings_only_mode_uses_common_headroom_without_guessing_trims(self):
        project, design, _, receiver = example()
        receiver["info"]["EqType"] = 0
        template = template_from_receiver(receiver, design)
        self.assertFalse(template["_has_original_coefficients"])
        plan = prepare_transfer(project, design, template, receiver)
        self.assertIsNone(plan["levels"])
        self.assertFalse(plan["summary"]["original_filter_backup_available"])
        self.assertTrue(all(row["trim_db"] is None for row in plan["summary"]["adjustments"]))
        self.assertGreaterEqual(plan["summary"]["global_headroom_db"], 6)


if __name__ == "__main__":
    unittest.main()
