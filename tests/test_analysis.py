import base64
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

import numpy as np

from audioseyx.analysis import analyze_measurement, decay_analysis, room_modes
from audioseyx.archive import save_archive, load_archive
from audioseyx.comparison import compare_projects
from audioseyx.model import Measurement, Project
from audioseyx.app import Workbench
from audioseyx.benchmarks import run_benchmarks
from audioseyx.dsp import design_channel, spectrum


def exponential_ir(fs=48000, seconds=2, decay=.45, noise=0):
    rng = np.random.default_rng(571)
    t = np.arange(round(fs * seconds)) / fs
    return rng.normal(size=len(t)) * np.exp(-np.log(1000) * t / decay) + noise * rng.normal(size=len(t))


class AcousticAnalysisTests(unittest.TestCase):
    def test_pure_delay_group_delay_and_start_time_are_preserved(self):
        raw = np.zeros(8192)
        raw[240] = 1
        m = Measurement("FL", "p0", "delay", ir=raw, start_time_s=-.001)
        result = analyze_measurement(m)
        self.assertAlmostEqual(result["peak_time_ms"], 4)
        np.testing.assert_allclose(result["spectrum"]["group_delay_ms"], 4, atol=1e-8)
        np.testing.assert_allclose(result["spectrum"]["excess_group_delay_ms"], 0, atol=1e-8)
        self.assertEqual(np.argmax(m.ir), 240)
        self.assertEqual(m.start_time_s, -.001)
        json.dumps(result, allow_nan=False)

    def test_known_exponential_decay_is_recovered_and_short_record_is_refused(self):
        long = decay_analysis(exponential_ir(), 48000)
        for name in ["T20", "T30"]:
            fit = long["fits"][name]
            self.assertTrue(fit["available"], fit)
            self.assertAlmostEqual(fit["estimate_s"], .45, delta=.025)
        short = decay_analysis(exponential_ir(seconds=.341, decay=2), 48000)
        self.assertFalse(short["fits"]["T30"]["available"])

    def test_noise_dominated_late_decay_is_refused(self):
        result = decay_analysis(exponential_ir(decay=.3, noise=.003), 48000)
        self.assertFalse(result["fits"]["T30"]["available"])

    def test_frequency_only_cannot_invent_time_domain_or_phase(self):
        m = Measurement("FL", "p0", "FR", frequency=[20, 100, 1000, 10000], magnitude_db=[0, 0, 0, 0])
        result = analyze_measurement(m)
        self.assertFalse(result["time_domain_available"])
        self.assertNotIn("impulse", result)
        self.assertIsNone(result["spectrum"]["group_delay_ms"])
        self.assertNotIn("decay", result)

    def test_spectrogram_has_one_level_reference_for_all_times(self):
        fs = 48000
        t = np.arange(fs) / fs
        raw = np.cos(2 * np.pi * 100 * t) * np.exp(-8 * t)
        result = analyze_measurement(Measurement("SW1", "p0", "ring", fs, raw))
        levels = np.asarray(result["spectrogram"]["level_db"])
        self.assertAlmostEqual(levels.max(), 0)
        self.assertLess(levels[-1].max(), -40)
        self.assertFalse(result["decay"]["bands"][0]["fits"])

    def test_rectangular_room_first_axial_mode_and_invalid_inputs(self):
        result = room_modes(5, 4, 3)
        self.assertAlmostEqual(result["modes"][0]["frequency_hz"], 34.3)
        self.assertEqual(result["modes"][0]["order"], [1, 0, 0])
        for dimensions in [(0, 4, 3), (np.nan, 4, 3), (5, 4, 70)]:
            with self.assertRaises(ValueError):
                room_modes(*dimensions)

    def test_benchmarks_include_independent_holdout_and_null_protection(self):
        report = run_benchmarks()
        self.assertTrue(report["all_passed"], report)
        self.assertFalse(report["hardware_tested"])
        heldout = [check for check in report["checks"] if "held_out_used_for_design" in check]
        self.assertEqual(len(heldout), 3)
        self.assertTrue(all(not check["held_out_used_for_design"] for check in heldout))

    def test_limited_frequency_data_does_not_get_extrapolated_full_band_correction(self):
        m = Measurement("FL", "p0", "limited FR", frequency=[100, 200, 300, 500], magnitude_db=[-12, 6, 0, -6])
        design = design_channel([m], "XT32", "FL", {"low_hz": 20, "high_hz": 10000})
        self.assertEqual(design["metrics"]["effective_correction_band_hz"], [100, 500])
        self.assertEqual(design["chart"]["frequency"][0], 100)
        self.assertEqual(design["chart"]["frequency"][-1], 500)
        gain = 20 * np.log10(abs(spectrum(Measurement("FL", "f", "filter", 48000, design["taps"]), np.asarray([2000]))[0]))
        self.assertLess(abs(gain), .1)
        with self.assertRaises(ValueError):
            design_channel([m], "XT32", "FL", {"low_hz": 1000, "high_hz": 10000})


class ProjectArchiveTests(unittest.TestCase):
    def test_roundtrip_retains_origin_timing_and_exact_arrays(self):
        ir = exponential_ir(seconds=.1)
        project = Project("原厂麦", [Measurement("FL", "p0", "m", ir=ir, start_time_s=-.01,
                                             metadata={"repeat": 2})], "native_avr")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.awb"
            save_archive(project, path)
            loaded = load_archive(path)
            self.assertEqual(loaded.origin, "native_avr")
            self.assertEqual(loaded.measurements[0].start_time_s, -.01)
            np.testing.assert_array_equal(loaded.measurements[0].ir, ir)
            bench = Workbench(Path(directory) / "state")
            bench.upload({"filename": "project.awb", "data": base64.b64encode(path.read_bytes()).decode()})
            restored = Workbench(bench.home)
            self.assertEqual(restored.project.origin, "native_avr")

    def test_modified_payload_and_path_traversal_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "project.awb"
            project = Project("p", [Measurement("FL", "p0", "m", ir=[0, 1, .1])])
            save_archive(project, path)
            with zipfile.ZipFile(path) as original:
                files = {name: original.read(name) for name in original.namelist()}
            for change in ["corrupt", "path"]:
                altered = files.copy()
                if change == "corrupt":
                    altered["arrays/0000-ir.npy"] = altered["arrays/0000-ir.npy"][:-1] + b"x"
                else:
                    altered["../escape.txt"] = b"x"
                bad = Path(directory) / (change + ".awb")
                with zipfile.ZipFile(bad, "w") as archive:
                    for name, raw in altered.items():
                        archive.writestr(name, raw)
                with self.assertRaises(ValueError):
                    load_archive(bad)
            self.assertFalse((Path(directory).parent / "escape.txt").exists())

    def test_oversized_declared_npy_shape_is_rejected_before_allocation(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = io.BytesIO()
            np.lib.format.write_array_header_1_0(payload, {"descr": "<f8", "fortran_order": False, "shape": (10**12,)})
            raw = payload.getvalue()
            manifest = {"schema": "audyssey-workbench-archive-1", "name": "p", "measurements": [
                {"channel": "FL", "position": "p0", "name": "m", "ir": {
                    "array": "arrays/0000-ir.npy", "sha256": hashlib.sha256(raw).hexdigest()}}]}
            path = Path(directory) / "huge.awb"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("manifest.json", json.dumps(manifest))
                archive.writestr("arrays/0000-ir.npy", raw)
            with self.assertRaisesRegex(ValueError, "shape"):
                load_archive(path)


class ComparisonTests(unittest.TestCase):
    def measurement(self, amplitude, metadata=None):
        return Measurement("FL", "p0", "m", ir=[amplitude, 0, 0], metadata=metadata or {})

    def test_unknown_units_do_not_report_a_physical_level_change(self):
        before = Project("before", [self.measurement(1)])
        after = Project("after", [self.measurement(100)])
        row = compare_projects(before, after)["rows"][0]
        self.assertIsNone(row["mean_level_delta_db"])
        self.assertAlmostEqual(row["shape_delta_rms_db"], 0)

    def test_repeats_are_grouped_and_known_chain_allows_level_comparison(self):
        meta = {"level_unit": "linear_ADC_relative_db", "measurement_chain_id": "same-mic-and-gain"}
        before = Project("before", [self.measurement(1, meta), self.measurement(1, meta)])
        after = Project("after", [self.measurement(2, meta)])
        result = compare_projects(before, after)
        self.assertEqual(result["matched"], 1)
        self.assertEqual(result["rows"][0]["before_repeats"], 2)
        self.assertAlmostEqual(result["rows"][0]["mean_level_delta_db"], 20 * np.log10(2))
        after.measurements[0].metadata = {**meta, "level_unit": "dB_SPL"}
        self.assertIsNone(compare_projects(before, after)["rows"][0]["mean_level_delta_db"])


if __name__ == "__main__":
    unittest.main()
