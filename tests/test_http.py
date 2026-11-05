import base64
import json
import re
import tempfile
import threading
import time
import os
from unittest.mock import patch
import unittest
from pathlib import Path
import hashlib
import http.client
import io
from urllib.error import HTTPError
from urllib.request import Request, build_opener, ProxyHandler

from audioseyx.app import run_server
from audioseyx.model import Measurement, Project


class HttpWorkflowTests(unittest.TestCase):
    def test_readonly_receiver_audit_and_explicit_input_level_workflow(self):
        from test_receiver_audit import snapshot,MENU,wait
        from audioseyx.receiver_audit import build_audit
        from audioseyx.audio_input import summarize_levels
        import numpy as np
        self.bench.receiver=None
        with patch('audioseyx.receiver_audit.probe_receiver',return_value=build_audit(snapshot(),MENU)):
            with self.request('/api/receiver/audit',{'host':'127.0.0.1'},self.bench.token) as response:json.load(response)
            wait(self.bench)
        self.assertEqual(self.bench.job['status'],'complete');self.assertIsNone(self.bench.receiver)
        with self.request('/download-receiver-audit') as response:self.assertTrue(json.load(response)['read_only'])
        with patch('audioseyx.audio_input.list_inputs',return_value=[{'index':0,'name':'Synthetic microphone','channels':1}]):
            with self.request('/api/audio-inputs') as response:self.assertFalse(json.load(response)['audio_captured'])
        with patch('audioseyx.audio_input.check_input_level',return_value=summarize_levels(np.zeros(8000),8000)) as recorder:
            with self.assertRaises(HTTPError):self.request('/api/audio-inputs/level',{'ready':False},self.bench.token)
            recorder.assert_not_called()
            with self.request('/api/audio-inputs/level',{'ready':True},self.bench.token) as response:json.load(response)
            wait(self.bench)
        self.assertEqual(self.bench.job['status'],'complete');self.assertFalse(self.bench.input_level['audio_file_saved'])
        with self.request('/receiver-audit.js') as response:self.assertIn(b'initReceiverAuditControls',response.read())

    def test_spatial_plan_preview_save_and_resume_preview_are_offline(self):
        from test_spatial_plan import fixture
        self.bench.set_project(fixture(1,repeats=2));self.bench.receiver=None
        with patch('audioseyx.app.ReceiverClient',side_effect=AssertionError('Offline planning cannot access a receiver')):
            with self.request('/api/spatial-plan/preview',{'preset':'three_seat','count':8},self.bench.token) as response:
                preview=json.load(response)
            self.assertFalse(preview['saved']);self.assertIsNone(self.bench.project.metadata.get('spatial_plan'))
            self.assertEqual(preview['inventory']['measured_positions'],1)
            with self.request('/api/spatial-plan',{'plan':preview['plan']},self.bench.token) as response:
                saved=json.load(response)
            self.assertEqual(saved['spatial_inventory']['planned_positions'],8)
            with self.request('/api/native-measure/preview',{'position':8,'mode':'resume','repeats':2},self.bench.token) as response:
                scope=json.load(response)
            self.assertEqual(scope['responses_to_capture'],6);self.assertTrue(scope['no_hardware_contact'])
            with self.request('/api/listening-area',{'preset':'three_seat_row','centre_priority':.6},self.bench.token) as response:
                updated=json.load(response)
            self.assertEqual(len(updated['spatial_plan']['positions']),8)
            self.assertAlmostEqual(updated['spatial_plan']['seats'][0]['weight'],.6)
            self.assertIsNone(self.bench.receiver)
        with self.request('/spatial-plan.js') as response:self.assertIn(b'initSpatialPlanner',response.read())

    def test_saved_run_download_and_recipe_are_local_integrity_checked_operations(self):
        from test_saved_controls import project
        from audioseyx.dsp import design_project
        from audioseyx.exporter import export_bundle
        from audioseyx.design_history import write_receipt
        p=project();self.bench.set_project(p)
        design=design_project(p,'MultEQ',{})
        archive=export_bundle(p,design,self.bench.home/'exports'/'history-http')
        receipt=write_receipt(self.bench.home,p,{},'complete',design=design,archive=archive)
        with self.request('/api/design-history') as response:
            rows=json.load(response)['runs']
        self.assertTrue(any(row['id']==receipt['id'] and row['dataset_matches'] for row in rows))
        with self.request('/api/design-history/recipe',{'id':receipt['id']},self.bench.token) as response:
            result=json.load(response)
        self.assertFalse(result['receiver_written']);self.assertFalse(result['automatic_calculation_started'])
        with self.request('/api/design-history/export?id='+receipt['id']) as response:
            self.assertEqual(response.read(),archive.read_bytes())
        archive.write_bytes(b'changed')
        with self.assertRaises(HTTPError) as error:
            self.request('/api/design-history/export?id='+receipt['id'])
        self.assertEqual(error.exception.code,400)

    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(dir=Path.cwd())
        cls.server, cls.bench, cls.address = run_server(Path(cls.directory.name).relative_to(Path.cwd()), open_browser=False)
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.opener = build_opener(ProxyHandler({}))
        cls.client = http.client.HTTPConnection("127.0.0.1", cls.server.server_port, timeout=10)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()
        cls.server.shutdown()
        cls.server.server_close()
        cls.directory.cleanup()

    def request(self, endpoint, data=None, token=None, origin=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-App-Token"] = token
        if origin:
            headers["Origin"] = origin
        self.client.request("POST" if data is not None else "GET", endpoint,
                            body=json.dumps(data).encode() if data is not None else None, headers=headers)
        response = self.client.getresponse()
        if response.status >= 400:
            raw = response.read()
            raise HTTPError(self.address + endpoint, response.status, response.reason, response.headers, io.BytesIO(raw))
        return response

    def test_page_assets_and_state(self):
        with self.request("/") as response:
            html = response.read().decode()
        self.assertIn('audioseyX Acoustic Workbench', html)
        self.assertNotIn("__APP_TOKEN__", html)
        with self.request("/app.js") as response:
            self.assertIn(b"renderTransfer", response.read())
        with self.request("/api/state") as response:
            state = json.load(response)
        self.assertIn("transfer", state)
        self.assertTrue(state['features']['native_eq_refinement'])
        with self.request('/native-refinement.js') as response:
            self.assertIn(b'loadDesignHistory',response.read())
        with self.request("/joint.js") as response:
            self.assertIn(b"renderJointEvaluation",response.read())
        with self.request("/transfer.js") as response:
            self.assertIn(b"initTransferControls",response.read())
        with self.request('/receiver-calibration.js') as response:
            self.assertIn(b'nativeCalibrationSettings',response.read())
        with self.request('/clock.js') as response:
            self.assertIn(b'initClockControls',response.read())
        with self.request('/verification.js') as response:
            self.assertIn(b'renderCapabilityAssessment',response.read())
        with self.request('/curve-profiles.js') as response:
            self.assertIn(b'calibrationCurveSettings',response.read())
        with self.request('/processing-controls.js') as response:
            self.assertIn(b'restoreProcessingControls',response.read())

    def test_coupled_native_api_retains_both_targets_lfe_and_unactivated_restore_controls(self):
        from test_receiver_calibration import fixture
        p,_,_,settings=fixture(positions=('p0','p1'))
        self.bench.set_project(p)
        settings.update(tier='XT32',optimizer='native_calibration',receiver_control_engine='coupled',
                        receiver_curve_control_mode='joint',curve_profiles={'Reference':{'bass_db':3}},
                        receiver_max_delay_ms=.1,receiver_sub_level_min_db=0.,receiver_sub_level_max_db=0.,validation_positions=['p1'])
        with self.request('/api/design',settings,self.bench.token) as response:self.assertIn(json.load(response)['status'],['running','complete'])
        self.wait_design()
        self.assertEqual(self.bench.job['status'],'complete',self.bench.job)
        with self.request('/api/state') as response:current=json.load(response)
        cal=current['design']['receiver_calibration']
        self.assertEqual(cal['control_selection']['mode'],'joint_curves')
        self.assertIn('LFE',cal['evaluation']['group_power_error'])
        self.assertEqual(set(cal['evaluation']['per_curve']),{'Flat','Reference'})
        self.assertFalse(current['saved_design_settings']['timing_confirmed'])
        self.assertIsNone(current['receiver'])

    def test_two_curve_design_has_distinct_public_results_and_complete_download(self):
        import zipfile
        self.bench.set_project(Project('two curves',[Measurement('FL','p0','synthetic',ir=[0,1,0,0])],'synthetic'))
        with self.request('/api/design',{'tier':'MultEQ','bass_db':0,'tilt_db_oct':0,
                          'curve_profiles':{'Reference':{'bass_db':6,'target_curve':None}}},self.bench.token) as response:
            self.assertIn(json.load(response)['status'],['running','complete'])
        self.wait_design()
        self.assertEqual(self.bench.job['status'],'complete',self.bench.job)
        with self.request('/api/state') as response:result=json.load(response)
        self.assertTrue(result['design']['curve_configuration']['independent_eq_targets'])
        self.assertNotIn('taps',result['design']['curve_designs']['Reference']['channels'][0])
        with self.request('/download') as response:
            with zipfile.ZipFile(io.BytesIO(response.read())) as archive:
                self.assertIn('curves/Reference/FL-filter.wav',archive.namelist())
                self.assertNotEqual(archive.read('FL-filter.wav'),archive.read('curves/Reference/FL-filter.wav'))

    def test_reference_target_import_does_not_replace_the_primary_target(self):
        body={'filename':'reference.targetcurve','curve_name':'Reference',
              'data':base64.b64encode(b'20 6\n1000 0\n20000 -4').decode()}
        primary=self.bench.target_curve
        with self.request('/api/target-curve',body,self.bench.token) as response:
            self.assertEqual(json.load(response)['name'],'reference.targetcurve')
        self.assertIs(self.bench.target_curve,primary)
        self.assertEqual(self.bench.reference_target_curve['level_db'][0],6)
        with self.request('/api/target-curve',{'curve_name':'Reference','reset':True},self.bench.token) as response:
            self.assertIsNone(json.load(response))

    def test_capability_assessment_and_export_are_offline_and_invalidate_with_the_project(self):
        project=Project('one position',[Measurement('FL','p0','original',ir=[0,1,0,0])],origin='synthetic')
        self.bench.set_project(project)
        with patch('audioseyx.receiver.ReceiverClient.read_info',side_effect=AssertionError('No AVR reads allowed')):
            with self.request('/api/capabilities') as response:
                report=json.load(response)
            self.assertIsNone(report['performance_percentage'])
            self.assertEqual(report['project_context']['measured_positions'],['p0'])
            with self.request('/api/capabilities/export',{},self.bench.token) as response:
                self.assertEqual(json.load(response)['schema'],'audioseyx-capability-assessment-1')
        self.assertIs(self.bench.project,project)
        with self.request('/download-capabilities') as response:
            self.assertEqual(json.load(response)['counts']['interface_blocked'],2)
        self.bench.set_project(Project('changed',[Measurement('FR','p1','different',ir=[1,0,0])]))
        with self.assertRaises(HTTPError):self.request('/download-capabilities')

    def test_temporal_comparison_preserves_originals_and_exports_all_repeats(self):
        import numpy as np
        from audioseyx.importers import save_project
        t=np.arange(8000)/8000
        before=Project('before',[Measurement('FL','p0','first',8000,np.exp(-t/.08)),
                                 Measurement('FL','p0','repeat',8000,np.exp(-t/.1))],origin='synthetic')
        after=Project('after',[Measurement('FL','p0','after',8000,np.exp(-t/.04))],origin='synthetic')
        self.bench.set_project(before)
        path=Path(self.directory.name)/'after.json';save_project(after,path)
        with self.request('/api/compare',{'filename':'after.json','data':base64.b64encode(path.read_bytes()).decode()},self.bench.token) as response:
            report=json.load(response)
        self.assertIs(self.bench.project,before)
        self.assertEqual(len(report['rows'][0]['temporal']['before']),2)
        with self.request('/download-verification') as response:
            self.assertEqual(json.load(response)['schema'],'audioseyx-playback-comparison-2')
        self.bench.set_project(after)
        with self.assertRaises(HTTPError):self.request('/download-verification')

    def test_clock_reference_creates_files_without_changing_the_project_or_contacting_receiver(self):
        before=self.bench.project
        with self.request('/api/clock/reference',{'marker_times_s':[.5,1.5,2.5],'sample_rate':8000},self.bench.token) as r:
            report=json.load(r)
        self.assertFalse(report['report']['sound_played'])
        self.assertIs(self.bench.project,before)
        with self.request('/download-clock-reference') as r:self.assertTrue(r.read().startswith(b'PK'))

    def test_original_recording_clock_correction_exports_a_separate_raw_file(self):
        from scipy.io import wavfile
        from test_clock_sync import original_recording
        value,pilot,rate,times=original_recording()
        def encoded(name,array):
            output=io.BytesIO();wavfile.write(output,rate,array)
            return {'filename':name,'data':base64.b64encode(output.getvalue()).decode()}
        original_project=self.bench.project
        body={'recording':encoded('original.wav',value),'pilot':encoded('pilot.wav',pilot),
              'marker_times_s':times.tolist(),'latency_guess_s':.035,'search_radius_s':.08}
        with self.request('/api/clock/correct',body,self.bench.token) as r:r.read()
        self.wait_design()
        self.assertEqual(self.bench.job['status'],'complete',self.bench.job)
        self.assertIs(self.bench.project,original_project)
        self.assertFalse(self.bench.clock_result['receiver_contacted'])
        self.assertAlmostEqual(self.bench.clock_result['analysis']['estimated_drift_ppm'],350,delta=1.5)
        with self.request('/download-clock') as r:
            import zipfile
            with zipfile.ZipFile(io.BytesIO(r.read())) as archive:
                self.assertIn('clock-corrected-recording.wav',archive.namelist())
                self.assertFalse(json.loads(archive.read('clock-report.json'))['mapping']['deconvolution_performed'])

    def test_native_calibration_design_is_serializable_and_stays_offline(self):
        from test_receiver_calibration import fixture
        p,_,_,settings=fixture();self.bench.set_project(p)
        settings.update(optimizer='native_calibration',receiver_max_delay_ms=.1,high_hz=300,bass_db=0,tilt_db_oct=0)
        with self.request('/api/design',settings,self.bench.token) as r:r.read()
        self.wait_design()
        self.assertEqual(self.bench.job['status'],'complete',self.bench.job)
        with self.request('/api/state') as r:state=json.load(r)
        self.assertIsNone(state['receiver'])
        self.assertIn('proposal',state['design']['receiver_calibration'])
        self.assertFalse(state['design']['receiver_upload_ready'])
        with self.request("/layouts.js") as response:
            self.assertIn(b"renderReceiverLayout",response.read())

    def test_layout_api_and_custom_demo_do_not_require_a_fixed_channel_count(self):
        with self.request("/api/layouts") as response:layouts=json.load(response)
        self.assertIn("9.4.6",[item["id"] for item in layouts["presets"]])
        self.assertIn("SB",layouts["known_channels"])
        with self.request("/api/demo",{"layout":["FL","FR","FHL","FHR","SW1","SW2"],"positions":1},self.bench.token) as response:
            project=json.load(response)
        self.assertEqual(project["layout"]["signature"],"2.2.2")
        self.assertEqual(project["measurement_count"],6)
        self.assertEqual(project["origin"],"synthetic")

    def test_polarity_api_requires_model_groups_and_invalidates_with_a_new_project(self):
        self.bench.set_project(Project('polarity',[Measurement(c,'p0',c,ir=[0,0,1,-.2,0,0]) for c in ['FL','FR']],'synthetic'))
        with self.assertRaises(HTTPError):self.request('/api/polarity',{'groups':[['FL','FR']]},self.bench.token)
        with self.request('/api/polarity',{'groups':[['FL','FR']],'same_models_confirmed':True},self.bench.token) as response:
            report=json.load(response)
        self.assertFalse(report['terminal_wiring_verified'])
        with self.request('/download-polarity') as response:self.assertEqual(json.load(response)['schema'],'audioseyx-polarity-1')
        self.bench.set_project(Project('new project',[Measurement('C','p0','c',ir=[1,0,0])]))
        with self.assertRaises(HTTPError):self.request('/download-polarity')

    def test_saved_routing_and_row_plans_work_without_connecting_to_the_receiver(self):
        project=Project('offline row',[Measurement('FL','p0','a',ir=[0,1,0]),Measurement('SW1','p0','b',ir=[0,1,0])],
                        metadata={'receiver_settings_snapshot':{'available':True,'crossovers':{'FL':60},'large_channels':[],'bass_mode':'lfe','lfe_lowpass_hz':150}})
        self.bench.set_project(project);self.bench.receiver=None
        with patch('audioseyx.receiver.socket.create_connection',side_effect=AssertionError('Offline settings must not contact an AVR')):
            with self.request('/api/design/receiver-settings',{},self.bench.token) as response:settings=json.load(response)
            self.assertEqual(settings['crossovers'],{'FL':60})
            self.assertEqual(settings['receiver_settings_reference']['source'],'offline_saved_snapshot_not_refreshed')
            with self.request('/api/listening-area',{'preset':'three_seat_row','centre_priority':.5},self.bench.token) as response:summary=json.load(response)
            self.assertEqual(summary['listening_area_coverage']['missing_training_seats'],['left','right'])

    def wait_design(self):
        deadline=time.monotonic()+10
        while self.bench.job["status"]=="running" and time.monotonic()<deadline:
            time.sleep(.01)
        self.assertNotEqual(self.bench.job["status"],"running")

    def test_joint_design_finishes_with_serializable_routed_results_and_download(self):
        rows=[Measurement(channel,"p0",channel,ir=[0,0,1,0,0,0]) for channel in ["FL","SW1"]]
        self.bench.set_project(Project("HTTP joint fixture",rows,"synthetic"))
        settings={"optimizer":"native_joint","joint_iterations":10,"joint_delay_ms":0,"timing_confirmed":True,
                  "relative_levels_confirmed":True,"routing_measurements_confirmed":True,"bass_db":0,"tilt_db_oct":0}
        with self.request("/api/design",settings,self.bench.token) as response:
            self.assertIn(json.load(response)["status"],["running","complete"])
        self.wait_design()
        self.assertEqual(self.bench.job["status"],"complete",self.bench.job)
        with self.request("/api/state") as response:
            state=json.load(response)
        self.assertEqual([item["channel"] for item in state["design"]["routed_charts"]],["FL","LFE"])
        self.assertEqual(state["joint"]["kind"],"full_joint_fir")
        self.assertFalse(state["design"]["receiver_upload_ready"])
        bound=state["design"]["joint_optimization"]["reachability"]
        self.assertTrue(bound["lower_bound_consistency_passed"])
        self.assertFalse(bound["requested_target_changed"])
        self.assertIsNotNone(bound["model_objective_gap_upper_bound"])
        with self.request("/download") as response:
            self.assertTrue(response.read().startswith(b"PK"))

    def test_design_cancel_does_not_activate_a_partial_result(self):
        self.bench.set_project(Project("cancel fixture",[Measurement("FL","p0","m",ir=[1,0,0])],"synthetic"))
        started=threading.Event()
        def wait_for_cancel(*args,**kwargs):
            started.set()
            if kwargs["cancel"].wait(5):raise InterruptedError("Filter design cancelled")
            raise RuntimeError("Test cancellation was not delivered")
        with patch("audioseyx.app.design_project",side_effect=wait_for_cancel):
            with self.request("/api/design",{},self.bench.token) as response:response.read()
            self.assertTrue(started.wait(3))
            with self.request("/api/design/cancel",{},self.bench.token) as response:
                self.assertTrue(json.load(response)["cancel_requested"])
            self.wait_design()
        self.assertEqual(self.bench.job["status"],"cancelled")
        self.assertIsNone(self.bench.design)
        self.assertIsNone(self.bench.export_path)

    def test_writes_require_local_session_token_and_origin(self):
        for token, origin in [(None, None), (self.bench.token, "https://example.com")]:
            with self.assertRaises(HTTPError) as caught:
                self.request("/api/demo", {}, token, origin)
            self.assertEqual(caught.exception.code, 403)

    def test_unknown_route_and_stale_calibration_do_not_download(self):
        for endpoint in ["/missing", "/download-calibration"]:
            with self.assertRaises(HTTPError):
                self.request(endpoint)

    def test_large_exports_stream_completely_and_support_ranges(self):
        target = Path(self.directory.name) / "large-test.zip"
        expected = bytes(range(256)) * 32768
        target.write_bytes(expected)
        self.bench.export_path = target
        for _ in range(5):
            with self.request("/download") as response:
                actual = response.read()
            self.assertEqual(hashlib.sha256(actual).digest(), hashlib.sha256(expected).digest())
        # Also cover clients which explicitly ask to close after the response.
        with self.opener.open(self.address + "/download", timeout=10) as response:
            self.assertEqual(response.read(), expected)
        request = Request(self.address + "/download", headers={"Range": "bytes=0-1023"})
        with self.opener.open(request, timeout=10) as response:
            self.assertEqual(response.status, 206)
            self.assertEqual(response.read(), expected[:1024])
        self.bench.export_path = None

    def test_analysis_project_archive_and_cache_invalidation(self):
        self.bench.set_project(Project("delay", [Measurement("FL", "p0", "m", ir=[0, 1, 0, 0])], "synthetic"))
        with self.request("/api/analyze", {"index": 0}, self.bench.token) as response:
            result = json.load(response)
        self.assertEqual(result["project_origin"], "synthetic")
        self.assertTrue(result["time_domain_available"])
        with self.request("/download-analysis") as response:
            self.assertEqual(json.load(response)["measurement_index"], 0)
        with self.request("/download-project") as response:
            self.assertTrue(response.read().startswith(b"PK"))
        self.bench.set_project(Project("different", [Measurement("FR", "p1", "n", ir=[1, 0, 0])]))
        with self.assertRaises(HTTPError):
            self.request("/download-analysis")

    def test_room_modes_api_and_analysis_script_are_available(self):
        with self.request("/api/room-modes", {"length": 5, "width": 4, "height": 3}, self.bench.token) as response:
            result = json.load(response)
        self.assertAlmostEqual(result["modes"][0]["frequency_hz"], 34.3)
        with self.request("/analysis.js") as response:
            self.assertIn(b"drawSpectrogram", response.read())

    def test_shutdown_does_not_interrupt_an_active_receiver_operation(self):
        try:
            for kind in ["native_measure", "transfer"]:
                self.bench.job = {"status": "running", "kind": kind}
                with self.assertRaises(HTTPError) as caught:
                    self.request("/api/shutdown", {}, self.bench.token)
                self.assertEqual(caught.exception.code, 400)
                with self.request("/api/state") as response:
                    self.assertEqual(json.load(response)["job"]["kind"], kind)
        finally:
            self.bench.job = {"status": "idle", "progress": 0, "message": "idle"}

    def test_download_identity_prevents_mixing_versions_during_resume(self):
        target = Path(self.directory.name) / "versioned.zip"
        target.write_bytes(b"first")
        self.bench.export_path = target
        try:
            with self.request("/api/download-info?path=/download") as response:
                info = json.load(response)
            self.assertEqual(info["sha256"], hashlib.sha256(b"first").hexdigest())
            before=target.stat()
            target.write_bytes(b"other")
            os.utime(target,ns=(before.st_atime_ns,before.st_mtime_ns))
            request = Request(self.address + "/download", headers={"If-Match": '"' + info["sha256"] + '"', "Range": "bytes=0-1"})
            with self.assertRaises(HTTPError) as caught:
                self.opener.open(request, timeout=10)
            self.assertEqual(caught.exception.code, 412)
        finally:
            self.bench.export_path = None


if __name__ == "__main__":
    unittest.main()
