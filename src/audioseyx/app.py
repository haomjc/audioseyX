from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time

import numpy as np

from . import __version__
from .dsp import demo_project, design_project, joint_sub_alignment, spectrum
from .model import CHANNELS, ALIASES
from .importers import import_file, save_project
from .exporter import export_bundle
from .rew import RewClient, find_rew, launch_rew
from .receiver import ReceiverClient, discover
from .transfer import validate_template, prepare_transfer, apply_plan, template_from_receiver
from .native_measure import capture_position
from .capabilities import identify_tier
from .importers import value
from .model import Project
from .curves import parse_target_curve
from .archive import save_archive
from .analysis import analyze_measurement, room_modes
from .comparison import compare_projects
from .compat import compatibility_status


def public_design(design):
    if not design:
        return None
    return {**{k: v for k, v in design.items() if k != "channels"},
            "channels": [{k: v for k, v in item.items() if k != "taps" and not k.startswith("_")} for item in design["channels"]]}


class Workbench:
    def __init__(self, home):
        self.home = Path(home).resolve()
        for directory in ["uploads", "projects", "exports"]:
            (self.home / directory).mkdir(parents=True, exist_ok=True)
        self.project = None
        self.project_path = None
        self.design = None
        self.joint = None
        self.job = {"status": "idle", "progress": 0, "message": 'Waiting for measurements'}
        self.lock = threading.Lock()
        self.token = secrets.token_urlsafe(32)
        self.receiver = None
        self.events = []
        self.export_path = None
        self.analysis_path = None
        self.rew = RewClient()
        self.template = None
        self.transfer_plan = None
        self.capture_cancel = threading.Event()
        self.design_cancel = threading.Event()
        self.target_curve = None
        self.log('Workbench started. No receiver has been connected or modified.')
        curve_file = self.home / "target-curve.json"
        if curve_file.is_file():
            try:
                saved = json.loads(curve_file.read_text(encoding="utf-8"))
                if saved:
                    checked = parse_target_curve("\n".join(f"{f} {level}" for f, level in zip(saved["frequency"], saved["level_db"])))
                    checked["name"] = str(saved["name"])
                    self.target_curve = checked
            except (OSError, ValueError, KeyError, TypeError):
                self.log('The saved target curve could not be read. The default target has been restored.')
        last = self.home / "last-project.awb"
        if not last.is_file():
            last = self.home / "last-project.json"
        if last.is_file():
            try:
                self.project = import_file(last)
                if last.suffix == ".json":
                    save_archive(self.project, self.home / "last-project.awb")
                self.project_path = self.home / "projects" / (self.project.id + ".awb")
                save_archive(self.project, self.project_path)
                self.log('Restored the last saved project.')
            except (ValueError, OSError):
                self.log('The last project could not be restored. Please import the measurements again.')

    def log(self, text):
        self.events.append({"time": time.strftime("%H:%M:%S"), "message": str(text)})
        self.events = self.events[-100:]

    def state(self):
        return {"application_id": "audioseyx-workbench", "version": __version__, "compatibility": compatibility_status(),
                "features":{"full_complex_joint_fir":True,"native_synthesis_models":True,"per_frequency_fdw":True,
                            "variable_channel_layouts":True,"read_only_receiver_settings":True},
                "project": self.project.summary() if self.project else None,
                "design": public_design(self.design), "joint": self.joint, "job": dict(self.job),
                "receiver": self.receiver, "events": self.events[-15:],
                "export_available": self.export_path is not None,
                "analysis_available": self.analysis_path is not None,
                "target_curve": {"name": self.target_curve["name"], "points": len(self.target_curve["frequency"])} if self.target_curve else None}

    def transfer_state(self):
        return {"template": {"model": self.template["model"], "tier": self.template["eqType"],
                             "channels": len(self.template["channels"]),
                             "original_filter_backup_available": self.template.get("_source_kind") != "native_settings_snapshot"} if self.template else None,
                "plan": self.transfer_plan["summary"] if self.transfer_plan else None}

    def set_project(self, project):
        if self.job["status"] == "running":
            raise ValueError('Wait for the current design to finish before changing projects')
        # Serve immutable snapshots. Windows holds an open download handle for
        # a while after the client finishes reading, which blocks replacement.
        project_path = self.home / "projects" / (project.id + "-" + secrets.token_hex(4) + ".awb")
        save_archive(project, project_path)
        save_archive(project, self.home / "last-project.awb")
        self.project, self.project_path = project, project_path
        self.design = None
        self.joint = None
        self.export_path = None
        self.analysis_path = None
        self.transfer_plan = None
        self.log(f"Imported {project.name}：{len(project.channels)} channels, {len(project.measurements)} measurements.")
        return project.summary()

    def upload(self, body):
        filename = str(body.get("filename", "measurement.json")).replace("\\", "/").split("/")[-1]
        if not filename or len(filename) > 180:
            raise ValueError('Invalid filename')
        raw = base64.b64decode(body.get("data", ""), validate=True)
        if not raw or len(raw) > 128 * 1024 * 1024:
            raise ValueError('The file is empty or larger than 128 MiB')
        directory = self.home / "uploads" / secrets.token_hex(4)
        directory.mkdir()
        path = directory / filename
        path.write_bytes(raw)
        return self.set_project(import_file(path, "user_file"))

    def mapping(self, entries):
        if not self.project or self.job["status"] == "running":
            raise ValueError('Import measurements first and wait for the current design to finish')
        prepared = []
        for entry in entries:
            index, channel, position = int(entry["index"]), str(entry["channel"]), str(entry["position"]).strip()
            if channel not in CHANNELS or not 0 <= index < len(self.project.measurements) or not position or len(position) > 80:
                raise ValueError('Invalid channel or position mapping')
            prepared.append((index, channel, position))
        for index, channel, position in prepared:
            self.project.measurements[index].channel = channel
            self.project.measurements[index].position = position
        return self.set_project(self.project)

    def start_design(self, settings):
        if not self.project:
            raise ValueError('Import measurements or load the demo first')
        with self.lock:
            if self.job["status"] == "running":
                raise ValueError('A design job is already running')
            project = self.project
            settings["target_curve"] = self.target_curve
            self.transfer_plan = None
            self.design = None
            self.joint = None
            self.export_path = None
            self.design_cancel = threading.Event()
            self.job = {"status": "running", "kind":"design", "progress": 0, "message": 'Preparing measurements and device formats'}
        def progress(number, message):
            self.job.update({"progress": number, "message": message})
        def run():
            try:
                settings["timing_confirmed"] = settings.get("timing_confirmed") is True
                project.metadata["timing_confirmed"] = settings["timing_confirmed"]
                design = design_project(project, settings.get("tier", project.tier or "XT32"), settings, progress, cancel=self.design_cancel)
                if self.design_cancel.is_set():
                    raise InterruptedError("Filter design cancelled")
                progress(90, 'Evaluating shared-subwoofer alignment')
                joint = ({"available":True,"kind":"full_joint_fir","optimization":design["joint_optimization"],
                          "evaluation":design["joint_evaluation"],"model":design["joint_model"]}
                         if design.get("joint_model") else joint_sub_alignment(project, design, float(settings.get("crossover_hz", 80))))
                progress(95, 'Compiling candidate formats and exporting reports')
                path = export_bundle(project, design, self.home / "exports" / (project.id + "-" + str(time.time_ns())), joint)
                with self.lock:
                    if self.design_cancel.is_set():
                        raise InterruptedError("Filter design cancelled; no result was activated")
                    self.design, self.joint, self.export_path = design, joint, path
                    self.job = {"status": "complete", "progress": 100, "message": 'Design and export complete'}
                self.log('Completed channel designs, available candidate encoding and reports. Nothing has been written to the receiver.')
            except InterruptedError as error:
                self.job={"status":"cancelled","kind":"design","progress":0,"message":str(error)}
                self.log(str(error))
            except Exception as error:
                self.job = {"status": "failed", "progress": 0, "message": str(error)}
                self.log('Design incomplete: ' + str(error))
        threading.Thread(target=run, daemon=True).start()
        return dict(self.job)

    def cancel_design(self):
        with self.lock:
            if self.job.get("status")!="running" or self.job.get("kind")!="design":
                raise ValueError("No filter-design job is running")
            self.design_cancel.set()
        return {"cancel_requested":True}

    def set_curve(self, body):
        if self.job["status"] == "running":
            raise ValueError('Wait for the current job before changing the target curve')
        if body.get("reset") is True:
            self.target_curve = None
        else:
            raw = base64.b64decode(body["data"], validate=True)
            if len(raw) > 1024 * 1024:
                raise ValueError('The target curve file is too large')
            curve = parse_target_curve(raw.decode("utf-8-sig"))
            curve["name"] = Path(body["filename"]).name
            self.target_curve = curve
        self.design = None
        self.joint = None
        self.export_path = None
        self.transfer_plan = None
        (self.home / "target-curve.json").write_text(json.dumps(self.target_curve, ensure_ascii=False), encoding="utf-8")
        return self.state()["target_curve"]

    def start_native_measurement(self, body):
        if not self.receiver or not self.receiver.get("connected"):
            raise ValueError('Connect the receiver on the Project and devices page first')
        if body.get("ready") is not True:
            raise ValueError('Confirm that the stock microphone is connected to the AVR and that calibration test tones may play')
        position, repeats = int(body.get("position", 1)), int(body.get("repeats", 1))
        from .native_measure import select_capture_outputs
        selected_outputs=select_capture_outputs(self.receiver["status"],body.get("channels"))
        selected_channels=[ALIASES.get(name,name) for name in selected_outputs]
        if not 1 <= position <= 20 or not 1 <= repeats <= 5:
            raise ValueError('Position must be 1-20 and repeats must be 1-5')
        with self.lock:
            if self.job["status"] == "running":
                raise ValueError('Wait for or stop the current job first')
            host = self.receiver["host"]
            existing = self.project if self.project and self.project.origin == "native_avr" and self.project.metadata.get("receiver_host") == host and not body.get("new_project") else None
            self.capture_cancel = threading.Event()
            self.job = {"status": "running", "kind": "native_measure", "progress": 0, "message": 'Checking receiver and stock microphone'}
        def progress(number, message):
            self.job.update({"progress": number, "message": message})
        def run():
            rows = []
            fresh = None
            failure = None
            try:
                fresh = ReceiverClient(host).read_info()
                if not fresh.get("connected"):
                    raise ValueError('Could not refresh the receiver status')
                def checkpoint(measurement):
                    rows.append(measurement)
                    snapshot = Project('Stock microphone capture checkpoint', rows.copy(), "native_avr",
                                       metadata={"receiver_host": host, "partial": True})
                    save_project(snapshot, self.home / f"native-position-{position}-checkpoint.json")
                capture_position(fresh, position, repeats, self.capture_cancel, progress, checkpoint,channels=selected_channels)
            except Exception as error:
                failure = str(error)
            if rows:
                project = existing or Project('AVR stock microphone measurements', [], "native_avr",
                                              value(fresh["info"], "Model", "ModelName", "targetModelName"),
                                              identify_tier(value(fresh["info"], "EqType", "enMultEQType")),
                                              {"receiver_host": host, "receiver_snapshot": fresh},
                                              ['The receiver sampled these responses and transferred them over the LAN. Original responses are retained without peak alignment.',
                                               'Model-specific microphone calibration and timing still need hardware validation. Mixed phase is not enabled automatically.'])
                project.measurements.extend(rows)
                project.metadata["partial"] = failure is not None
                with self.lock:
                    self.job = {"status": "failed" if failure else "complete", "kind": "native_measure", "progress": 0 if failure else 100,
                                "message": ('Saved partial responses: ' + failure) if failure else f"Position {position} complete; saved {len(rows)} raw responses"}
                    self.set_project(project)
                self.log('Stock microphone data saved. End calibration mode and unplug the microphone before transfer.')
            else:
                self.job = {"status": "failed", "kind": "native_measure", "progress": 0, "message": failure or 'No valid measurement was received'}
                self.log('Stock microphone capture incomplete: ' + self.job["message"])
        threading.Thread(target=run, daemon=True).start()
        return self.job

    def import_template(self, body):
        if self.job["status"] == "running":
            raise ValueError('A job is running; calibration configuration cannot be changed')
        raw = base64.b64decode(body.get("data", ""), validate=True)
        if not raw or len(raw) > 128 * 1024 * 1024:
            raise ValueError('The OCA file is empty or exceeds the size limit')
        template = json.loads(raw.decode("utf-8-sig"))
        validate_template(template)
        self.template = template
        self.transfer_plan = None
        (self.home / "original-calibration.oca").write_bytes(raw)
        self.log('Saved the original OCA recovery copy. Nothing has been written to the receiver.')
        return self.transfer_state()

    def prepare_apply(self, body):
        if not self.project or not self.design or not self.template or not self.receiver:
            raise ValueError('Complete a design from real measurements, supply an original OCA or supported configuration, and connect the receiver first')
        if self.job["status"] == "running":
            raise ValueError('Wait for the current job to finish')
        self.design["settings"]["uncorrected_measurements_confirmed"] = body.get("uncorrected_confirmed") is True
        self.receiver = ReceiverClient(self.receiver["host"]).read_info()
        plan = prepare_transfer(self.project, self.design, self.template, self.receiver)
        (self.home / "prepared-calibration.oca").write_text(json.dumps(plan["calibration"], allow_nan=False), encoding="utf-8")
        (self.home / "receiver-before-transfer.json").write_text(json.dumps(self.receiver, ensure_ascii=False, indent=2), encoding="utf-8")
        self.transfer_plan = plan
        return plan["summary"]

    def use_current_configuration(self):
        if not self.receiver or not self.design:
            raise ValueError('Connect the receiver and complete a design first')
        if self.job["status"] == "running":
            raise ValueError('Wait for the current job to finish first')
        fresh = ReceiverClient(self.receiver["host"]).read_info()
        self.template = template_from_receiver(fresh, self.design)
        self.receiver = fresh
        self.transfer_plan = None
        (self.home / "receiver-settings-only.json").write_text(json.dumps(fresh, ensure_ascii=False, indent=2), encoding="utf-8")
        self.log('Saved the current device settings snapshot. It does not include the original filters.')
        return self.transfer_state()

    def start_apply(self, body):
        with self.lock:
            if self.job["status"] == "running":
                raise ValueError('A job is already running')
            if not self.transfer_plan or body.get("digest") != self.transfer_plan["summary"]["digest"]:
                raise ValueError('The transfer plan has expired; prepare it again')
            if body.get("reviewed") is not True:
                raise ValueError('Review the proposed calibration replacement in the interface first')
            host = self.transfer_plan["summary"]["host"]
            self.transfer_plan = None
            self.job = {"status": "running", "progress": 0, "message": 'Rechecking receiver status', "kind": "transfer"}
        try:
            fresh = ReceiverClient(host).read_info()
            plan = prepare_transfer(self.project, self.design, self.template, fresh)
            if plan["summary"]["digest"] != body["digest"]:
                raise ValueError('The device or design changed; review the plan again')
        except Exception as error:
            self.job = {"status": "failed", "progress": 0, "message": str(error), "kind": "transfer"}
            raise
        self.job = {"status": "running", "progress": 0, "message": 'Preparing the reviewed calibration replacement', "kind": "transfer"}
        def progress(number, message):
            self.job.update({"progress": number, "message": message})
        def run():
            try:
                apply_plan(plan, progress)
                self.job = {"status": "complete", "progress": 100, "message": 'Receiver acknowledged transfer completion; verification measurements are required', "kind": "transfer"}
                self.log('The receiver acknowledged coefficient transfer and calibration exit. Acoustic results still require verification measurements.')
            except Exception as error:
                self.job = {"status": "failed", "progress": 0, "message": 'Calibration completion was not confirmed: ' + str(error), "kind": "transfer"}
                self.log('Transfer incomplete. Restore using the original OCA.' if plan["summary"]["original_filter_backup_available"] else 'Transfer incomplete. The settings snapshot contains no original filters; stock calibration can be rerun to recover.')
        threading.Thread(target=run, daemon=True).start()
        return self.job

    def compare(self, body):
        if not self.project:
            raise ValueError('Import the before-correction measurements first')
        filename = Path(str(body.get("filename", "after.json"))).name
        directory = self.home / "uploads" / secrets.token_hex(4)
        directory.mkdir()
        path = directory / filename
        raw = base64.b64decode(body["data"], validate=True)
        if not raw or len(raw) > 128 * 1024 * 1024:
            raise ValueError('The verification file is empty or exceeds the size limit')
        path.write_bytes(raw)
        after = import_file(path, "verification_file")
        return compare_projects(self.project, after)

    def analyze(self, body):
        if not self.project:
            raise ValueError('Import measurements or load the demo first')
        index = int(body.get("index", 0))
        if not 0 <= index < len(self.project.measurements):
            raise ValueError('Invalid measurement index')
        project = self.project
        result = analyze_measurement(project.measurements[index])
        if self.project is not project:
            raise ValueError('The project changed during analysis; analyze it again')
        result["project_origin"] = project.origin
        result["measurement_index"] = index
        self.analysis_path = self.home / "exports" / (project.id + "-analysis-" + secrets.token_hex(4) + ".json")
        self.analysis_path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding="utf-8")
        return result


def run_server(home, port=0, open_browser=True):
    from .http_server import build_server
    return build_server(Workbench, home, port, open_browser)


def main():
    import argparse
    parser = argparse.ArgumentParser(description='audioseyX Acoustic Workbench')
    parser.add_argument("--version", action="version", version="audioseyX " + __version__)
    parser.add_argument("--compat-dir", help='Directory containing optional local native compatibility data')
    parser.add_argument("--compat-status", action="store_true", help='Check local compatibility data without connecting to a receiver')
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--data-dir", default=None)
    checks=parser.add_mutually_exclusive_group()
    checks.add_argument("--self-test", action="store_true")
    checks.add_argument("--joint-benchmark",action="store_true",help="Run the full synthetic XT32 7.1.4 joint-FIR benchmark without connecting to a receiver")
    parser.add_argument("--benchmark-iterations",type=int,default=60)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    if args.compat_dir:
        os.environ["AUDIOSEYX_COMPAT_DIR"] = str(Path(args.compat_dir).expanduser().resolve())
    if args.compat_status:
        print(json.dumps(compatibility_status(), ensure_ascii=False, indent=2), flush=True)
        return
    if args.self_test or args.joint_benchmark:
        if args.joint_benchmark:
            from .joint_benchmarks import run_joint_benchmarks
            report=run_joint_benchmarks(args.benchmark_iterations,lambda number,message:print(f"{number}% {message}",file=sys.stderr,flush=True))
        else:
            from .diagnostics import self_test
            report = self_test()
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            Path(args.report).write_text(encoded, encoding="utf-8")
        print(encoded, flush=True)
        raise SystemExit(0 if report["all_passed"] else 1)
    base = Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
    from platformdirs import user_data_path
    default_home = base / 'UserData' if getattr(sys, "frozen", False) else user_data_path("audioseyX", appauthor=False)
    home = Path(args.data_dir).expanduser() if args.data_dir else default_home
    existing = home / "running.json"
    if args.port == 0 and existing.is_file():
        try:
            from urllib.request import build_opener, ProxyHandler
            from urllib.parse import urlparse
            previous = json.loads(existing.read_text(encoding="utf-8"))
            address = previous["url"]
            parts = urlparse(address)
            if parts.scheme == "http" and parts.hostname == "127.0.0.1" and parts.port:
                with build_opener(ProxyHandler({})).open(address + "/api/state", timeout=1) as response:
                    if json.load(response).get("application_id") == "audioseyx-workbench":
                        if not args.no_browser:
                            import webbrowser
                            webbrowser.open(address)
                        return
        except (OSError, ValueError, KeyError):
            pass
    server, _, address = run_server(home, args.port, not args.no_browser)
    print('audioseyX workbench: ' + address, flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
