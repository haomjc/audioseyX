import unittest
import numpy as np

from audioseyx.curves import parse_target_curve, target_offset
from audioseyx.rew import command_names
from audioseyx.rew import RewClient
import base64


class CurveTests(unittest.TestCase):
    def test_house_curve_is_normalised_by_reference_not_absolute_spl(self):
        curve = parse_target_curve("# target\n20 76\n1000 70\n20000 68")
        np.testing.assert_allclose(target_offset(curve, np.array([20, 1000, 20000])), [6, 0, -2])

    def test_curve_rejects_duplicate_nonfinite_and_invalid_frequencies(self):
        for text in ["20 0\n20 1", "20 NaN\n1000 0", "-20 0\n1000 0", "20 0"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_target_curve(text)

    def test_rew_commands_are_exact_choices_not_substring_matches(self):
        self.assertEqual(command_names(["SPL", {"command": "Cancel"}]), ["SPL", "Cancel"])
        self.assertEqual(command_names({"error": "SPL disabled"}), [])

    def test_rew_import_requests_signed_linear_ir_without_db_conversion(self):
        class Stub(RewClient):
            def __init__(self):
                self.paths = []
            def request(self, path, *args, **kwargs):
                self.paths.append(path)
                if path == "/measurements":
                    return {"1": {"title": "FL0"}}
                return {"sampleRate": 48000, "sampleInterval": 0,
                        "data": base64.b64encode(np.asarray([0, -0.25, 0.5, 0], dtype=">f4").tobytes()).decode()}
        client = Stub()
        project = client.import_project()
        np.testing.assert_array_equal(project.measurements[0].ir, [0, -0.25, 0.5, 0])
        self.assertTrue(all("unit=dB" not in path for path in client.paths))
