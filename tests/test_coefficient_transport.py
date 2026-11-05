import hashlib
import unittest

import numpy as np

from audioseyx.capabilities import capability
from audioseyx.coefficient_transport import (
    audit_coefficient_streams, coefficient_packets, decode_coefficient_stream,
    deserialize_coefficients, quantization_report, serialize_coefficients,
)
from audioseyx.native_protocol import frame, parse_frame, acknowledgement


def example_streams(tier="XT32", channels=("FL", "FR", "SW1", "SW2"), fixed=False):
    packets, manifest = [], []
    for index, channel in enumerate(channels):
        count = capability(tier).shape(channel)[2]
        values = np.linspace(-.1, .1, count)
        address = 101 + index  # Artificial addresses, unrelated to a real AVR.
        manifest.append({"channel": channel, "address": address, "tier": tier,
                         **quantization_report(values, fixed)})
        for curve in [0, 1]:
            for slot in [0, 1, 2]:
                packets.extend(coefficient_packets(values, address, curve, slot, fixed))
    return packets, manifest


class CoefficientTransportTests(unittest.TestCase):
    def test_ack_is_an_explicit_status_not_an_arbitrary_substring(self):
        for payload in [b'ACK', b'{"Comm":"ACK"}', b'{"Comm":"ACK","Error":0}', b'"ACK"']:
            self.assertTrue(acknowledgement(payload))
        for payload in [b'NACK', b'{"Comm":"NAK"}']:
            self.assertFalse(acknowledgement(payload))
        for payload in [b'NO_ACK_YET', b'{"Comment":"ACK"}', b'{"Comm":"PENDING","message":"ACK"}', b'{}']:
            with self.assertRaises(ValueError): acknowledgement(payload)

    def test_float_and_fixed_roundtrip_cross_every_fragment_boundary(self):
        for fixed in [False, True]:
            for count in [1, 127, 128, 255, 256, 512, 704, 1024, 16384]:
                values = np.linspace(-.4, .4, count)
                packets = coefficient_packets(values, 101, 1, 0, fixed)
                decoded = decode_coefficient_stream(packets, fixed=fixed, expected_count=count)
                expected = deserialize_coefficients(serialize_coefficients(values, fixed), fixed)
                np.testing.assert_array_equal(decoded["values"], expected)
                self.assertEqual(decoded["address"], 101)
                self.assertEqual(decoded["sample_rate_hz"], 32000)
                self.assertEqual(decoded["wire_sha256"], hashlib.sha256(serialize_coefficients(values, fixed)).hexdigest())

    def test_observed_negative_q31_convention_is_retained(self):
        raw = serialize_coefficients([.25, -.25, 0], True)
        np.testing.assert_array_equal(np.frombuffer(raw, dtype="<i4"), [536870912, -536870913, 0])
        report = quantization_report([.25, -.25, 0], True)
        self.assertAlmostEqual(report["maximum_absolute_error"], 2**-31)
        self.assertEqual(report["format"], "Q1.31_observed_negative_bias")

    def test_underflow_and_finite_wire_values_are_reported(self):
        report = quantization_report([1e-50, .1])
        self.assertEqual(report["underflowed_values"], 1)
        self.assertGreater(report["maximum_absolute_error"], 0)
        with self.assertRaisesRegex(ValueError, "non-finite"):
            deserialize_coefficients(np.array([np.nan], dtype="<f4").tobytes())

    def test_invalid_shapes_ranges_and_headers_are_rejected(self):
        for values in [[], 1, [[1, 2]], [float("nan")], [float("inf")], [1e40]]:
            with self.assertRaises(ValueError):
                serialize_coefficients(values)
        with self.assertRaises(ValueError):
            serialize_coefficients([1], True)
        for argument in [{"channel_byte": True}, {"channel_byte": -1}, {"channel_byte": 256},
                         {"channel_byte": 1.0}, {"curve": 2}, {"sample_rate_slot": 3}]:
            with self.assertRaises(ValueError):
                coefficient_packets([.1], **{"channel_byte": 101, **argument})

    def test_all_tiers_and_full_layouts_cover_both_curves_and_all_rate_slots(self):
        layouts = [("FL",), ("FL", "FR"), ("FL", "FR", "SW1", "SW2"),
                   ("FL", "FR", "C", "SLA", "SRA", "SBL", "SBR", "TFL", "TFR", "TRL", "TRR", "SW1")]
        for tier in ["MultEQ", "XT", "XT32"]:
            for channels in layouts:
                packets, manifest = example_streams(tier, channels)
                report = audit_coefficient_streams(packets, manifest)
                self.assertEqual(report["streams"], len(channels) * 6)
                self.assertEqual(report["channels"], len(channels))
                self.assertTrue(report["all_streams_complete"])
                self.assertFalse(report["hardware_verified"])

    def test_missing_duplicated_and_wrongly_routed_streams_fail(self):
        packets, manifest = example_streams("MultEQ", ("FL",))
        for changed in [packets[:-2], packets + packets[:2], packets[2:]]:
            with self.assertRaises(ValueError):
                audit_coefficient_streams(changed, manifest)
        _, payload = parse_frame(packets[0])
        changed = packets.copy()
        changed[0] = frame("SET_COEFDT", payload[:2] + bytes([105]) + payload[3:], 0, 1)
        with self.assertRaisesRegex(ValueError, "unreviewed"):
            audit_coefficient_streams(changed, manifest)

    def test_reordered_truncated_and_corrupted_fragments_fail(self):
        packets = coefficient_packets(np.linspace(-.2, .2, 704), 101)
        for changed in [packets[1:], packets[:-1], [packets[1], packets[0], *packets[2:]],
                        [packets[0], packets[0], *packets[1:]], [packets[0][:-1], *packets[1:]]]:
            with self.assertRaises(ValueError):
                decode_coefficient_stream(changed)
        for position in [0, 4, 20, len(packets[0]) - 1]:
            damaged = bytearray(packets[0]); damaged[position] ^= 1
            with self.assertRaises(ValueError):
                decode_coefficient_stream([bytes(damaged), *packets[1:]])

    def test_valid_checksums_do_not_hide_bad_lengths_or_reserved_fields(self):
        packets = coefficient_packets(np.zeros(128), 101)
        _, payload = parse_frame(packets[0])
        for changed in [frame("SET_COEFDT", payload[:-4], 0, 1),
                        frame("SET_COEFDT", payload[:3] + b"\x01" + payload[4:], 0, 1),
                        frame("SET_COEFDT", payload + b"\x00", 0, 1),
                        frame("SET_SETDAT", payload, 0, 1)]:
            with self.assertRaises(ValueError):
                decode_coefficient_stream([changed, packets[1]])

    def test_manifest_must_match_budget_addresses_and_values(self):
        packets, manifest = example_streams("MultEQ", ("FL",))
        for entry in [{**manifest[0], "coefficient_count": 512},
                      {**manifest[0], "wire_sha256": "0" * 64}]:
            with self.assertRaises(ValueError):
                audit_coefficient_streams(packets, [entry])
        with self.assertRaises(ValueError):
            audit_coefficient_streams(packets, manifest * 2)
        with self.assertRaises(ValueError):
            audit_coefficient_streams(packets, [])


if __name__ == "__main__":
    unittest.main()
