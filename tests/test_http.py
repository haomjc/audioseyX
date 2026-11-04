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
        with self.request("/joint.js") as response:
            self.assertIn(b"renderJointEvaluation",response.read())
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
