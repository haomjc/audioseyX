import copy
import unittest
from unittest.mock import patch

import numpy as np

from audioseyx.model import Measurement, Project
from audioseyx.dsp import design_project
from audioseyx.native import frame, parse_frame
from audioseyx.transfer import (validate_template, prepare_transfer, apply_plan, template_from_receiver,
                               canonical_model, audit_transfer_plan, transfer_plan_digest)
from audioseyx.receiver_constraints import receiver_profile,snapshot_fingerprint


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
    def test_incomplete_spatial_plan_and_changed_plan_are_not_prepared(self):
        from audioseyx.spatial_plan import preset_plan
        p,d,t,r=example();p.metadata['spatial_plan']=preset_plan(p,preset='single_area',count=8)
        with self.assertRaisesRegex(ValueError,'incomplete spatial measurement plan'):prepare_transfer(p,d,t,r)
        p.metadata['spatial_plan']=preset_plan(p,preset='observed')
        with self.assertRaisesRegex(ValueError,'does not match the current spatial'):prepare_transfer(p,d,t,r)

    def test_modeled_distances_levels_crossovers_and_polarity_reach_the_reviewed_bytes(self):
        project,design,template,receiver=example()
        receiver['status']['ChSetup'][-1]={'SWMIX1':'E'}
        snapshot={'available':True,'configured_channels':['FL','SW1'],'speaker_types':{'FL':'small'},'large_channels':[],
                  'distances_m':{'FL':2.5,'SW1':2.5},'levels_db':{'FL':-6.,'SW1':-6.},
                  'crossovers':{'FL':80.},'bass_mode':'lfe','lfe_lowpass_hz':120.}
        receiver['web_settings']=snapshot
        design['settings']['apply_receiver_adjustments']=True
        design['receiver_calibration']={'schema':'audioseyx-receiver-calibration-1','model':'AVR-TEST',
            'profile':receiver_profile('AVR-X3700H'),'baseline_snapshot_sha256':snapshot_fingerprint(snapshot),
            'proposal':{'distances_m':{'FL':2.5,'SW1':3.18},'levels_db':{'FL':-5.5,'SW1':-6.},
                        'crossovers_hz':{'FL':100.},'inverted_outputs':['SW1']}}
        plan=prepare_transfer(project,design,template,receiver)
        self.assertTrue(plan['summary']['receiver_settings_included'])
        self.assertEqual(plan['receiver_updates'][0],{'Distance':[{'FL':250},{'SW1':318}]})
        self.assertEqual(plan['receiver_updates'][1],{'Crossover':[{'FL':100.},{'SW1':'F'}]})
        self.assertIn('SW1',plan['levels']['ChLevel'][1])
        self.assertLess(plan['calibration']['channels'][1]['filter'][0],0)
        self.assertTrue(audit_transfer_plan(plan)['all_streams_complete'])
        device=FakeReceiver()
        with patch('audioseyx.transfer.socket.create_connection',return_value=device):apply_plan(plan)
        payloads=[payload.decode() for command,payload in device.commands if command=='SET_SETDAT']
        self.assertTrue(any('"Distance"' in payload and '318' in payload for payload in payloads))
        changed=copy.deepcopy(receiver);changed['web_settings']['levels_db']['FL']=-5.5
        with self.assertRaisesRegex(ValueError,'changed since'):
            prepare_transfer(project,design,template,changed)

    def test_status_subwoofer_alias_is_not_sent_as_a_control_identifier(self):
        project,design,template,receiver=example()
        receiver['status']['ChSetup'][-1]={'SWMIX1':'E'}
        plan=prepare_transfer(project,design,template,receiver)
        self.assertEqual(set(plan['levels']['ChLevel'][-1]),{'SW1'})

    def test_reviewed_digest_commits_to_device_settings_and_exact_wire_bytes(self):
        plan = prepare_transfer(*example())
        self.assertTrue(audit_transfer_plan(plan)["all_streams_complete"])
        self.assertEqual(plan["summary"]["coefficient_transport_audit"]["streams"], 12)
        for kind in ["host", "settings", "levels", "native_manifest", "wire", "calibration", "summary"]:
            changed = copy.deepcopy(plan)
            if kind == "host": changed["summary"]["host"] = "127.0.0.2"
            elif kind == "settings": changed["settings"]["AudyDynEq"] = True
            elif kind == "levels": changed["levels"]["ChLevel"][0]["FL"] += 1
            elif kind == "native_manifest": changed["native_manifest"][0]["address"] += 1
            elif kind == "wire": changed["packets"][0] = changed["packets"][0][:-1] + b"\x00"
            elif kind == "calibration": changed["calibration"]["channels"][0]["filter"][0] += .01
            else: changed["summary"]["global_headroom_db"] += 1
            with self.subTest(kind=kind), patch("audioseyx.transfer.socket.create_connection") as connect:
                with self.assertRaisesRegex(ValueError, "changed"):
                    apply_plan(changed)
                connect.assert_not_called()

    def test_a_fresh_digest_does_not_hide_a_missing_rate_stream(self):
        plan = prepare_transfer(*example())
        plan["packets"] = plan["packets"][2:]  # Whole 128-value FL stream removed.
        plan["summary"]["digest"] = transfer_plan_digest(plan)
        with patch("audioseyx.transfer.socket.create_connection") as connect:
            with self.assertRaisesRegex(ValueError, "every channel"):
                apply_plan(plan)
            connect.assert_not_called()

    def test_missing_duplicate_or_extra_design_channels_are_not_silently_ignored(self):
        for kind in ['missing','duplicate','extra']:
            project,design,template,receiver=example()
            if kind=='missing':design['channels'].pop()
            elif kind=='duplicate':design['channels'].append(copy.deepcopy(design['channels'][0]))
            else:
                extra=copy.deepcopy(design['channels'][0]);extra['channel']='FR';design['channels'].append(extra)
            with self.subTest(kind=kind),self.assertRaisesRegex(ValueError,'exactly one'):
                prepare_transfer(project,design,template,receiver)

    def test_execution_uses_a_private_reviewed_snapshot(self):
        plan=prepare_transfer(*example())
        class MutatingCaller(FakeReceiver):
            def sendall(self,packet):
                if not self.commands:
                    plan['packets'][0]=b'caller changed the pending packet'
                    plan['settings']['AudyDynEq']=True
                super().sendall(packet)
        receiver=MutatingCaller()
        with patch('audioseyx.transfer.socket.create_connection',return_value=receiver):
            apply_plan(plan)
        self.assertEqual(receiver.commands[-1][0],'EXIT_AUDMD')
        self.assertEqual(sum(c=='SET_COEFDT' for c,_ in receiver.commands),len(plan['packets']))

    def test_incomplete_listening_area_is_not_prepared_as_final_row_calibration(self):
        project,design,template,receiver=example()
        design['settings']['listening_area_resolution']={'required_seats_measured':False,'missing_training_seats':['left','right']}
        with self.assertRaisesRegex(ValueError,'provisional listening-area'):
            prepare_transfer(project,design,template,receiver)
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
