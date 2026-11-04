import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

import numpy as np

from audioseyx.compat import CompatibilityDataUnavailable, compatibility_status, load_native_tables
from audioseyx.diagnostics import self_test
from audioseyx.dsp import design_project
from audioseyx.exporter import export_bundle
from audioseyx.model import Measurement, Project


class PublicPackageTests(unittest.TestCase):
    def test_missing_private_tables_do_not_block_wav_design_export(self):
        impulse = np.zeros(2048)
        impulse[20] = 1
        project = Project("public demo", [Measurement("FL", "p0", "m", ir=impulse)], "synthetic")
        with patch.dict(os.environ, {"AUDIOSEYX_COMPAT_DIR": ""}), tempfile.TemporaryDirectory() as directory:
            design = design_project(project, "XT32", {})
            bundle = export_bundle(project, design, Path(directory) / "export")
            with zipfile.ZipFile(bundle) as archive:
                self.assertIn("FL-filter.wav", archive.namelist())
                candidate = json.loads(archive.read("native-candidate.json"))
                self.assertFalse(candidate["encoding_available"])
                self.assertFalse(candidate["receiver_upload_ready"])
                self.assertNotIn("coefficients", candidate["channels"][0])

    def test_selftest_distinguishes_design_from_unavailable_native_encoding(self):
        with patch.dict(os.environ, {"AUDIOSEYX_COMPAT_DIR": ""}):
            report = self_test()
        self.assertTrue(report["all_passed"])
        xt32 = [check for check in report["checks"] if "XT32" in check["name"]]
        self.assertTrue(all(not check["native_encoding_checked"] for check in xt32))
        self.assertFalse(report["compatibility"]["xt32_tables"]["available"])

    def test_invalid_local_resource_is_reported_without_running_any_code(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"AUDIOSEYX_COMPAT_DIR": directory}):
            path = Path(directory) / "native_tables.json"
            path.write_text('{"filters": {}}', encoding="utf-8")
            with self.assertRaises(CompatibilityDataUnavailable):
                load_native_tables()
            self.assertFalse(compatibility_status()["xt32_tables"]["available"])
