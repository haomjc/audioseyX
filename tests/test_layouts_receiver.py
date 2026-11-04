import json
import unittest
from unittest.mock import patch

import numpy as np

from audioseyx.layouts import presets,preset_channels,summarize_layout,configured_channels
from audioseyx.model import Measurement,Project,channel_and_position
from audioseyx.dsp import demo_project,design_project
from audioseyx.capabilities import capability
from audioseyx.receiver import ReceiverClient,read_heos_identity
from audioseyx.receiver_web import parse_speaker_settings,joint_design_defaults
from audioseyx.native_measure import capture_position,select_capture_outputs
from audioseyx.native_joint import measured_grid_response,prepare_native_problem
from test_native_measure import MeasurementReceiver


class VariableLayoutTests(unittest.TestCase):
    def test_legacy_native_swmix_capture_cannot_silently_enter_filter_design(self):
        project=Project('legacy alias',[Measurement('SW1','p0','unverified',ir=[0,1,0],metadata={'receiver_raw_channel_id':'SWMIX1'})],'native_avr')
        self.assertEqual(project.summary()['unsafe_capture_indices'],[0])
        with self.assertRaisesRegex(ValueError,'SWMIX'):design_project(project,'XT32',{})
    def test_mismatched_measurement_rate_uses_full_record_transform_with_original_time(self):
        ir=np.zeros(1800);ir[270]=1;ir[1760]=.2
        measurement=Measurement("FL","p0","different rate",44100,ir,start_time_s=-.001)
        n=2048;f=np.fft.rfftfreq(n,1/48000)
        result=measured_grid_response(measurement,f,n,48000)
        supported=f<=22050
        expected=np.exp(-2j*np.pi*f*(270/44100-.001))+.2*np.exp(-2j*np.pi*f*(1760/44100-.001))
        np.testing.assert_allclose(result[supported],expected[supported],atol=1e-9)
        self.assertTrue(np.all(result[~supported]==0))

    def test_large_layout_memory_budget_is_checked_before_frequency_grid_allocation(self):
        project=demo_project("9.4.6",positions=1)
        settings={"timing_confirmed":True,"relative_levels_confirmed":True,"routing_measurements_confirmed":True,
                  "independent_subs_confirmed":True,"joint_memory_mib":128}
        with patch("audioseyx.native_joint.np.fft.rfftfreq",side_effect=AssertionError("allocated before budget check")):
            with self.assertRaisesRegex(ValueError,"memory budget"):
                prepare_native_problem(project,"XT32",settings)

    def test_example_layouts_have_the_requested_bed_sub_and_height_counts(self):
        for preset in presets():
            parts=list(map(int,preset["id"].split(".")))
            if len(parts)==2:parts.append(0)
            summary=summarize_layout(preset["channels"])
            self.assertEqual([len(summary[k]) for k in ["bed_channels","subwoofer_outputs","height_channels"]],parts)
            self.assertFalse(summary["independent_subwoofer_filters_verified"])
        self.assertEqual(preset_channels("2.0"),["FL","FR"])
        self.assertIn("SB",preset_channels("6.1"))
        self.assertEqual(len(preset_channels("9.4.6")),19)

    def test_incomplete_position_coverage_is_reported_without_inventing_measurements(self):
        measurements=[Measurement("FL","p0","a",ir=[1,0,0]),Measurement("FR","p1","b",ir=[1,0,0])]
        result=Project("incomplete",measurements).summary()["layout"]
        self.assertEqual(result["signature"],"2.0")
        self.assertFalse(result["coverage"]["complete"])
        self.assertEqual(len(result["coverage"]["missing"]),2)
        self.assertEqual(len(measurements),2)

    def test_mixed_subwoofer_aliases_do_not_imply_independent_filters(self):
        self.assertEqual(channel_and_position("SWMIX1"),("SW1","p0"))
        result=configured_channels({"ChSetup":[{"FL":"S"},{"SWMIX1":"E"},{"SWMIX2":"E"}]})
        self.assertEqual(result,["FL","SW1","SW2"])
        with self.assertRaisesRegex(ValueError,"ambiguous"):
            configured_channels({"ChSetup":[{"SW1":"E"},{"SWMIX1":"E"}]})

    def test_default_demo_remains_7_1_4_and_custom_demo_does_not_claim_an_avr_model(self):
        self.assertEqual(demo_project().summary()["layout"]["signature"],"7.1.4")
        project=demo_project("9.4.6",positions=1)
        self.assertEqual(len(project.channels),19)
        self.assertEqual(len(project.measurements),19)
        self.assertEqual(project.model,"SYNTHETIC-LAYOUT")

    def test_joint_problem_supports_stereo_height_and_four_sub_layouts_in_the_selected_tier(self):
        settings={"optimizer":"native_joint","timing_confirmed":True,"relative_levels_confirmed":True,
                  "routing_measurements_confirmed":True,"independent_subs_confirmed":True,
                  "joint_delay_ms":0,"joint_iterations":2,"position_risk":0,"bass_db":0,"tilt_db_oct":0}
        for name in ["2.0","5.1.2","9.4.6"]:
            channels=preset_channels(name);ir=np.zeros(512);ir[48]=1
            project=Project(name,[Measurement(c,"p0",c,48000,ir) for c in channels],"synthetic")
            result=design_project(project,"XT",settings)
            self.assertEqual(set(item["channel"] for item in result["channels"]),set(channels))
            expected=sum(capability("XT").shape(c)[0] for c in channels)
            self.assertEqual(result["joint_optimization"]["optimization_variables"],expected)
            self.assertEqual(result["joint_model"]["measurements_discarded"],0)
            self.assertEqual(result["joint_model"]["lfe_in_objective"],"SW1" in channels)


class ReadOnlyReceiverSettingsTests(unittest.TestCase):
    def fixture(self):
        return {"configuration":'<SpeakerConfig><Speaker index="0"><Value>2</Value></Speaker><Speaker index="2"><Value>7</Value></Speaker><Speaker index="9"><Value>2</Value></Speaker></SpeakerConfig>',
                "crossovers":'<Crossovers><Selection>2</Selection><All>80</All><List><Speaker index="0" display="3">60</Speaker><Speaker index="7" display="3">100</Speaker></List><SelectableValue><List><Item>60</Item><Item>100</Item></List></SelectableValue></Crossovers>',
                "bass":'<Bass><SubwooferMode>2</SubwooferMode><LPFforLFE>150</LPFforLFE></Bass>',
                "distances":'<Distances><Unit>1</Unit><List><Speaker index="3">123</Speaker><Speaker index="18">234</Speaker></List></Distances>',
                "levels":'<Levels><List><Speaker index="30">-25</Speaker><Speaker index="25">-10</Speaker><Speaker index="32">0</Speaker></List></Levels>'}

    def status(self):
        return {"ChSetup":[{"FL":"S"},{"FR":"S"},{"TFL":"S"},{"TFR":"S"},{"SWMIX1":"E"},{"SWMIX2":"E"}]}

    def test_crossover_groups_distances_and_levels_use_distinct_index_maps(self):
        settings=parse_speaker_settings(self.fixture(),self.status())
        self.assertEqual(settings["crossovers"],{"FL":60,"FR":60,"TFL":100,"TFR":100})
        self.assertEqual(settings["distances_m"],{"SW1":1.23,"TFL":2.34})
        self.assertEqual(settings["levels_db"],{"SW1":-2.5,"TFL":-1.,"SW1+SW2":0.})
        self.assertEqual(settings["configured_subwoofer_count"],2)
        self.assertFalse(settings["independent_subwoofer_filters_verified"])
        defaults=joint_design_defaults(settings,["FL","FR","TFL","TFR","SW1"])
        self.assertEqual(defaults["bass_mode"],"lfe_plus_main")
        self.assertEqual(defaults["lfe_lowpass_hz"],150)

    def test_missing_routing_values_are_not_replaced_by_assumed_80_hz_for_a_sub_system(self):
        with self.assertRaisesRegex(ValueError,"incomplete"):
            joint_design_defaults({"crossovers":{"FL":60}},["FL","FR","SW1"])
        self.assertEqual(joint_design_defaults({},["FL","FR"])["crossovers"],{})

    def test_web_url_is_accepted_only_for_a_lan_receiver_without_credentials(self):
        client=ReceiverClient("https://192.168.1.80:10443/index.html")
        self.assertEqual(client.host,"192.168.1.80")
        self.assertEqual(client.port,1256)
        for bad in ["https://example.com/index.html","https://user:password@192.168.1.80/"]:
            with self.assertRaises(ValueError):ReceiverClient(bad)

    def test_heos_identity_selects_matching_ip_instead_of_the_first_player(self):
        class Socket:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def settimeout(self,*args):pass
            def sendall(self,payload):self.command=payload
            def recv(self,size):
                return json.dumps({"heos":{"command":"player/get_players","result":"success"},
                                   "payload":[{"ip":"192.168.1.2","model":"Other player"},
                                              {"ip":"127.0.0.1","model":"Denon AVR-TEST","version":"synthetic"}]}).encode()
        sock=Socket()
        with patch("audioseyx.receiver.socket.create_connection",return_value=sock):
            identity=read_heos_identity("127.0.0.1")
        self.assertEqual(identity["model"],"Denon AVR-TEST")
        self.assertEqual(sock.command,b"heos://player/get_players\r\n")
        self.assertNotIn("serial",identity)

    def test_selected_mixed_sub_output_preserves_raw_id_and_records_exclusion(self):
        receiver={"host":"127.0.0.1","connected":True,"info":{"DType":"Float","SysDelay":124},
                  "status":{**self.status(),"Mic":True,"HPPlug":False}}
        device=MeasurementReceiver()
        with patch("audioseyx.native_measure.socket.create_connection",return_value=device):
            rows=capture_position(receiver,settle_seconds=0,channels=["FL","SW1"])
        self.assertEqual({row.channel for row in rows},{"FL","SW1"})
        sub=next(row for row in rows if row.channel=="SW1")
        self.assertEqual(sub.metadata["receiver_raw_channel_id"],"SWMIX1")
        self.assertIn("SWMIX2",sub.metadata["configured_outputs_not_selected"])
        started=[json.loads(payload)["Channel"] for command,payload in device.commands if command=="START_CHNL"]
        self.assertEqual(started,["FL","SW1"])
        self.assertEqual(sub.metadata["receiver_measurement_command_id"],"SW1")
        position=next(json.loads(payload) for command,payload in device.commands if command=="SET_POSNUM")
        self.assertEqual(position["ChSetup"],["FL","SW1"])
        with self.assertRaises(ValueError):select_capture_outputs(receiver["status"],["UNKNOWN"])


if __name__=="__main__":unittest.main()
