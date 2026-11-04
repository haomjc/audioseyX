import base64
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from audioseyx.model import Measurement, Project, channel_and_position
from audioseyx.importers import import_file, save_project
from audioseyx.capabilities import capability, identify_tier
from audioseyx.dsp import demo_project, design_project, design_channel, resample_ir, joint_sub_alignment
from audioseyx.native import frame, parse_frame, coefficient_packets, pack_xt32
from audioseyx.exporter import export_bundle
from audioseyx.app import Workbench
from audioseyx.receiver import private_host

ROOT = Path(__file__).resolve().parents[1]


class ImportTests(unittest.TestCase):
    def test_mqx_decodes_samples_and_ignores_disabled_measurement(self):
        project = import_file(ROOT / "examples/Mqx 5.1.mqx", "user_file")
        self.assertEqual(len(project.measurements), 17)
        self.assertEqual(len(project.channels), 6)
        self.assertEqual(project.origin, "synthetic")
        self.assertTrue(all(len(m.ir) == 16384 for m in project.measurements))

    def test_mdat_reads_frequency_data_without_faking_ir(self):
        project = import_file(ROOT / "examples/Room.mdat")
        self.assertEqual(len(project.measurements), 2)
        self.assertTrue(all(m.ir is None and len(m.frequency) > 100 for m in project.measurements))
        with self.assertRaisesRegex(ValueError, "channel mappings"):
            design_project(project, "XT32", {})

    def test_project_roundtrip_preserves_timing_and_synthetic_origin(self):
        ir = np.zeros(256)
        ir[100] = 1
        project = Project("test", [Measurement("FL", "p1", "FL1", 48000, ir, start_time_s=-0.01)], "synthetic")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.json"
            save_project(project, path)
            restored = import_file(path, "user_file")
        self.assertEqual(restored.origin, "synthetic")
        self.assertEqual(restored.measurements[0].start_time_s, -0.01)
        np.testing.assert_array_equal(restored.measurements[0].ir, ir)

    def test_bad_numeric_data_is_rejected(self):
        for ir in [[0, 0], [1, float("nan")], [1, float("inf")]]:
            with self.assertRaises(ValueError):
                Measurement("FL", "p0", "bad", 48000, np.asarray(ir))

    def test_mapping_is_explicit_and_channel_names_not_guessed(self):
        self.assertEqual(channel_and_position("FL2"), ("FL", "p2"))
        self.assertEqual(channel_and_position("SW1"), ("SW1", "p0"))
        self.assertEqual(channel_and_position("SW1_2"), ("SW1", "p2"))
        self.assertEqual(channel_and_position("L Room"), ("L ROOM", "p0"))


class DspTests(unittest.TestCase):
    def test_identity_measurement_needs_identity_filter_in_all_tiers(self):
        ir = np.zeros(2048)
        ir[100] = 1
        group = [Measurement("FL", "p0", "FL0", 48000, ir)]
        for tier in ["MultEQ", "XT", "XT32"]:
            design = design_channel(group, tier, "FL", {"bass_db": 0, "tilt_db_oct": 0})
            self.assertAlmostEqual(design["taps"][0], 1, places=8)
            self.assertLess(np.max(np.abs(design["taps"][1:])), 1e-9)

    def test_robust_design_improves_held_target_on_demo(self):
        project = demo_project()
        group = [m for m in project.measurements if m.channel == "FL"]
        design = design_channel(group, "XT32", "FL", {"bass_db": 3})
        self.assertLess(design["metrics"]["rms_after_db"], design["metrics"]["rms_before_db"] * 0.75)

    def test_phase_is_not_used_without_confirmed_timing(self):
        project = demo_project()
        design = design_channel(project.measurements[:3], "XT32", "FL", {"phase_mode": "mixed"})
        self.assertEqual(design["metrics"]["phase_mode"], "minimum")
        self.assertTrue(design["limitations"])

    def test_sub_resampling_preserves_transfer_gain(self):
        ir = np.zeros(4096)
        ir[1024] = 1
        converted = resample_ir(ir, 48000, 6000)
        self.assertAlmostEqual(float(np.sum(converted)), 1, delta=0.01)
        self.assertEqual(int(np.argmax(np.abs(converted))), 128)

    def test_invalid_settings_fail_before_design(self):
        project = demo_project()
        for settings in [{"delay_ms": -1}, {"high_hz": float("nan")}, {"phase_mode": "invented"}]:
            with self.assertRaises(ValueError):
                design_project(project, "XT32", settings)
        self.assertIsNone(identify_tier(False))
        self.assertIsNone(identify_tier("unknown"))

    def test_joint_alignment_requires_common_reference_and_matching_positions(self):
        project = demo_project()
        project.origin = "user_file"
        project.metadata.clear()
        self.assertFalse(joint_sub_alignment(project, {"channels": []})["available"])

    def test_frequency_only_data_without_phase_cannot_fake_joint_alignment(self):
        row = Measurement("FL", "p0", "FL", frequency=[20, 100, 1000], magnitude_db=[0, 0, 0])
        project = Project("no phase", [row], "user_file", metadata={"timing_confirmed": True})
        self.assertFalse(joint_sub_alignment(project, {"channels": []})["available"])


class NativeFormatTests(unittest.TestCase):
    def test_frames_match_known_read_and_control_frames(self):
        for command, expected in [("ENTER_AUDY", "5400130000454e5445525f4155445900000077"),
                                  ("GET_AVRINF", "54001300004745545f415652494e460000006c"),
                                  ("GET_AVRSTS", "54001300004745545f41565253545300000089")]:
            packet = frame(command)
            self.assertEqual(packet.hex(), expected)
            self.assertEqual(parse_frame(packet), (command, b""))
        broken = frame("GET_AVRINF")[:-1] + b"\x00"
        with self.assertRaises(ValueError):
            parse_frame(broken)

    def test_coefficients_roundtrip_all_packet_boundaries(self):
        for length in [1, 127, 128, 255, 512, 704, 1024]:
            original = np.linspace(-0.5, 0.5, length).astype("<f4")
            packets = coefficient_packets(original, 0x0D)
            payloads = [parse_frame(p)[1] for p in packets]
            self.assertEqual(payloads[0][:4], bytes([0, 2, 13, 0]))
            actual = np.frombuffer(payloads[0][4:] + b"".join(payloads[1:]), dtype="<f4")
            np.testing.assert_array_equal(actual, original)

    def test_q31_does_not_silently_saturate(self):
        with self.assertRaises(ValueError):
            coefficient_packets([1.0], 0, fixed=True)
        packet = coefficient_packets([0.25, -0.25], 0, fixed=True)[0]
        raw = np.frombuffer(parse_frame(packet)[1][4:], dtype="<i4")
        np.testing.assert_array_equal(raw, [536870912, -536870913])

    def test_xt32_encoding_requires_explicit_local_compatibility_data(self):
        import os
        from unittest.mock import patch
        from audioseyx.compat import CompatibilityDataUnavailable
        with patch.dict(os.environ, {"AUDIOSEYX_COMPAT_DIR": ""}):
            with self.assertRaises(CompatibilityDataUnavailable):
                pack_xt32(np.zeros(16321))

    def test_candidate_capacity_is_tier_specific(self):
        self.assertEqual(capability("MultEQ").shape("FL"), (128, 48000, 128))
        self.assertEqual(capability("XT").shape("SW1"), (512, 6000, 512))
        self.assertEqual(capability("XT32").shape("FL"), (16321, 48000, 1024))
        with self.assertRaises(ValueError):
            pack_xt32([1, 0])
        with self.assertRaises(ValueError):
            private_host("8.8.8.8")


class WorkflowTests(unittest.TestCase):
    def test_new_curve_invalidates_old_design_and_plan_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            bench = Workbench(directory)
            bench.design = {"old": True}
            bench.transfer_plan = {"old": True}
            bench.set_curve({"filename": "curve.txt", "data": base64.b64encode(b"20 3\n1000 0\n20000 -2").decode()})
            self.assertIsNone(bench.design)
            self.assertIsNone(bench.transfer_plan)
            reopened = Workbench(directory)
            self.assertEqual(reopened.target_curve["name"], "curve.txt")

    def test_native_calibration_data_cannot_be_claimed_as_corrected_playback(self):
        with tempfile.TemporaryDirectory() as directory:
            bench = Workbench(Path(directory) / "app")
            before = demo_project()
            before.measurements = before.measurements[:1]
            bench.set_project(before)
            after = Project("native", before.measurements, "native_avr")
            path = Path(directory) / "after.json"
            save_project(after, path)
            with self.assertRaisesRegex(ValueError, "calibration-mode"):
                bench.compare({"filename": "after.json", "data": base64.b64encode(path.read_bytes()).decode()})

    def test_wav_upload_keeps_filename_for_mapping(self):
        from scipy.io import wavfile
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.wav"
            ir = np.zeros(512, dtype=np.float32)
            ir[50] = 1
            wavfile.write(path, 48000, ir)
            bench = Workbench(Path(directory) / "app")
            bench.upload({"filename": "FL0.wav", "data": base64.b64encode(path.read_bytes()).decode()})
            self.assertEqual(bench.project.measurements[0].channel, "FL")

    def test_mapping_and_export_keep_synthetic_data_non_uploadable(self):
        from zipfile import ZipFile
        with tempfile.TemporaryDirectory() as directory:
            bench = Workbench(Path(directory) / "app")
            demo = demo_project()
            demo.measurements = demo.measurements[:3]
            bench.set_project(demo)
            bench.mapping([{"index": 0, "channel": "C", "position": "p0"}])
            self.assertEqual(bench.project.measurements[0].channel, "C")
            design = design_project(bench.project, "XT32", {})
            archive = export_bundle(bench.project, design, Path(directory) / "design")
            with ZipFile(archive) as zipped:
                candidate = json.loads(zipped.read("native-candidate.json"))
                self.assertFalse(candidate["receiver_upload_ready"])
                self.assertEqual(candidate["origin"], "synthetic")
                self.assertIn('design-report.md', zipped.namelist())


if __name__ == "__main__":
    unittest.main()
