from __future__ import annotations

import base64
import copy
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
from .feature_coverage import capability_assessment
from .calibration_curves import design_report
from .saved_controls import make_saved_controls, load_saved_controls
from .design_history import write_receipt
from .receiver_audit import public_audit,load_probe_report,save_probe_report


def public_design(design):
    return design_report(design)


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
        self.verification_path = None
        self.capability_path = None
        self.polarity_report = None
        self.polarity_path = None
        self.rew = RewClient()
        self.template = None
        self.transfer_plan = None
        self.capture_cancel = threading.Event()
        self.design_cancel = threading.Event()
        self.clock_cancel = threading.Event()
        self.clock_result = None
        self.clock_archive = None
        self.clock_reference_path = None
        self.target_curve = None
        self.reference_target_curve = None
        self.saved_design_settings = None
        self.saved_controls_restored = False
        self.receiver_audit=None
        self.receiver_audit_path=None
        self.input_level=None
        self.log('Workbench started. No receiver has been connected or modified.')
        for attribute, filename in [('target_curve','target-curve.json'), ('reference_target_curve','reference-target-curve.json')]:
            curve_file = self.home / filename
            if curve_file.is_file():
                try:
                    saved = json.loads(curve_file.read_text(encoding="utf-8"))
                    if saved:
                        checked = parse_target_curve("\n".join(f"{f} {level}" for f, level in zip(saved["frequency"], saved["level_db"])))
                        checked["name"] = str(saved["name"])
                        setattr(self, attribute, checked)
                except (OSError, ValueError, KeyError, TypeError):
                    self.log('A saved target curve could not be read. Its default target has been restored.')
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
        try:
            self.saved_design_settings=load_saved_controls(self.home/'last-design-settings.json',self.project)
            self.saved_controls_restored=self.saved_design_settings is not None
        except (ValueError,OSError,TypeError,RecursionError):
            self.log('Saved controls could not be restored; default controls are shown. No job was started.')
        for attribute,filename,schema in [('receiver_audit','receiver-audit.json','audioseyx-receiver-audit-1'),
                                          ('input_level','input-level.json','audioseyx-input-level-1')]:
            try:
                report=load_probe_report(self.home/filename,schema)
                setattr(self,attribute,report)
                if attribute=='receiver_audit' and report is not None:self.receiver_audit_path=self.home/filename
            except (ValueError,OSError,TypeError,RecursionError):self.log('A saved diagnostic report could not be restored. No probe or audio capture started.')

    def log(self, text):
        self.events.append({"time": time.strftime("%H:%M:%S"), "message": str(text)})
        self.events = self.events[-100:]

    def state(self):
        return {"application_id": "audioseyx-workbench", "version": __version__, "compatibility": compatibility_status(),
                "features":{"full_complex_joint_fir":True,"native_synthesis_models":True,"per_frequency_fdw":True,
                            "variable_channel_layouts":True,"read_only_receiver_settings":True,
                            'native_receiver_calibration':True,'auditory_band_balance':True,'raw_clock_correction':True,
                            'temporal_verification':True,'capability_assessment':True,'independent_native_curves':True,
                            'coupled_native_controls':True,'data_linked_saved_controls':True,
                            'native_eq_refinement':True,'measurement_robustness':True,'design_history':True,
                            'spatial_measurement_plans':True,'resumable_position_capture':True,
                            'receiver_read_only_audit':True,'local_input_level_check':sys.platform=='win32'},
                'capability_assessment': capability_assessment(self.project),
                "project": self.project.summary() if self.project else None,
                "design": public_design(self.design), "joint": self.joint, "job": dict(self.job),
                "receiver": self.receiver, "events": self.events[-15:],
                'receiver_audit':public_audit(self.receiver_audit),'input_level':self.input_level,
                "export_available": self.export_path is not None,
                "analysis_available": self.analysis_path is not None,
                'verification_available': self.verification_path is not None,
                'capability_report_available': self.capability_path is not None,
                "polarity":self.polarity_report,
                'clock':self.clock_result,
                "target_curve": {"name": self.target_curve["name"], "points": len(self.target_curve["frequency"])} if self.target_curve else None,
                'reference_target_curve': {'name':self.reference_target_curve['name'], 'points':len(self.reference_target_curve['frequency'])} if self.reference_target_curve else None,
                'saved_design_settings':self.saved_design_settings,'saved_controls_restored':self.saved_controls_restored,
                'showing_previous_design':self.design is not None and self.job.get('kind')=='design' and self.job['status'] in ['running','failed','cancelled']}

    def transfer_state(self):
        return {"template": {"model": self.template["model"], "tier": self.template["eqType"],
                             "channels": len(self.template["channels"]),
                             "original_filter_backup_available": self.template.get("_source_kind") != "native_settings_snapshot"} if self.template else None,
                "plan": self.transfer_plan["summary"] if self.transfer_plan else None}

    def set_project(self, project, *, _capture_run_id=None):
        capture_commit=_capture_run_id is not None and self.job.get('kind')=='native_measure' and self.job.get('capture_run_id')==_capture_run_id
        if self.job["status"] == "running" and not capture_commit:
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
        self.verification_path = None
        self.capability_path = None
        self.polarity_report = None
        self.polarity_path = None
        self.transfer_plan = None
        self.saved_design_settings = None
        self.saved_controls_restored = False
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

    def set_spatial_plan(self,body):
        from .spatial_plan import checked_plan,listening_area,preset_plan
        with self.lock:
            if self.project is None:raise ValueError('Import or capture measurements before planning positions')
            if self.job.get('status')=='running':raise ValueError('Wait for the active job before changing the measurement plan')
            plan=checked_plan(body['plan'],self.project) if body.get('plan') is not None else checked_plan(
                preset_plan(self.project,body.get('preset','three_seat'),body.get('count',8),
                            float(body.get('centre_priority',.5)),body.get('repeats',1)),self.project)
            project=Project(self.project.name,list(self.project.measurements),self.project.origin,self.project.model,self.project.tier,
                            copy.deepcopy(self.project.metadata),list(self.project.warnings),id=self.project.id)
            project.metadata['spatial_plan']=plan
            project.metadata['listening_area']=listening_area(plan)
            return self.set_project(project)

    def preview_native_measurement(self,body,*,existing=None,use_current=True,channels=None):
        from .spatial_plan import capture_batches,position_id
        project=self.project if use_current else existing
        if body.get('new_project') is True:project=None
        number=body.get('position',1)
        if isinstance(number,bool) or not isinstance(number,int) or not 1<=number<=20:
            raise ValueError('Position number must be an integer from 1 to 20')
        identity=position_id(body.get('position_id') or f'p{number-1}')
        plan=project.metadata.get('spatial_plan') if project is not None else None
        if plan is not None and identity not in {p['id'] for p in plan['positions']}:
            raise ValueError('Add this microphone position to the explicit measurement plan before capturing it')
        if channels is None:channels=body.get('channels')
        if channels is None:channels=plan['expected_channels'] if plan else project.channels if project is not None else None
        if channels is None and self.receiver and self.receiver.get('connected'):
            from .native_measure import select_capture_outputs
            channels=[ALIASES.get(c,c) for c in select_capture_outputs(self.receiver['status'],self.receiver.get('capture_channels'))]
        if plan is not None and channels is not None and set(channels)-set(plan['expected_channels']):
            raise ValueError('Capture outputs must belong to the explicit physical-output measurement plan')
        result=capture_batches(project,identity,channels,body.get('repeats',1),body.get('mode','append'))
        result['position_number']=number
        import hashlib
        result.pop('preview_sha256')
        result['preview_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,allow_nan=False).encode()).hexdigest()
        return result

    def start_design(self, settings):
        if not self.project:
            raise ValueError('Import measurements or load the demo first')
        settings = copy.deepcopy(settings)
        with self.lock:
            if self.job["status"] == "running":
                raise ValueError('A design job is already running')
            project = self.project
            settings["target_curve"] = self.target_curve
            if settings.get('curve_profiles') is not None:
                profiles = settings['curve_profiles']
                if not isinstance(profiles, dict) or not isinstance(profiles.get('Reference'), dict):
                    raise ValueError('Independent calibration curves require a Reference profile')
                if self.reference_target_curve:
                    profiles['Reference']['target_curve'] = copy.deepcopy(self.reference_target_curve)
            if settings.get('optimizer') == 'native_calibration':
                cached = self.receiver.get('web_settings', {}) if self.receiver and self.receiver.get('connected') else project.metadata.get('receiver_settings_snapshot', {})
                settings['receiver_snapshot'] = copy.deepcopy(cached)
            self.transfer_plan = None
            self.design_cancel = threading.Event()
            self.job = {"status": "running", "kind":"design", "progress": 0, "message": 'Preparing measurements and device formats'}
        def progress(number, message):
            self.job.update({"progress": number, "message": message})
        def run():
            try:
                settings["timing_confirmed"] = settings.get("timing_confirmed") is True
                analysis_project=Project(project.name,list(project.measurements),project.origin,project.model,project.tier,
                                         {**copy.deepcopy(project.metadata),'timing_confirmed':settings['timing_confirmed']},
                                         list(project.warnings),id=project.id)
                design = design_project(analysis_project, settings.get("tier", project.tier or "XT32"), settings, progress, cancel=self.design_cancel)
                if self.design_cancel.is_set():
                    raise InterruptedError("Filter design cancelled")
                progress(90, 'Evaluating shared-subwoofer alignment')
                joint = ({"available":True,"kind":"full_joint_fir","optimization":design["joint_optimization"],
                          "evaluation":design["joint_evaluation"],"model":design["joint_model"]}
                         if design.get("joint_model") else {'available':False,'reason':'Native receiver calibration has its own complete timing/crossover result; no second alignment is applied.'} if design.get('receiver_calibration')
                         else joint_sub_alignment(analysis_project, design, float(settings.get("crossover_hz", 80))))
                progress(95, 'Compiling candidate formats and exporting reports')
                path = export_bundle(project, design, self.home / "exports" / (project.id + "-" + str(time.time_ns())), joint)
                saved=None;temporary=None
                try:
                    document=make_saved_controls(project,settings)
                    temporary=self.home/('controls-'+secrets.token_hex(6)+'.tmp.json')
                    temporary.write_text(json.dumps(document,ensure_ascii=False,allow_nan=False),encoding='utf-8')
                    saved=document['settings']
                except (ValueError,TypeError,OSError):
                    self.log('The design is complete, but processing controls could not be saved for restart.')
                with self.lock:
                    if self.design_cancel.is_set():
                        if temporary is not None:temporary.unlink(missing_ok=True)
                        raise InterruptedError("Filter design cancelled; no result was activated")
                    if temporary is not None and saved is not None:
                        try:temporary.replace(self.home/'last-design-settings.json')
                        except OSError:
                            saved=None
                            self.log('Processing controls could not be committed; the completed design remains available.')
                    self.design, self.joint, self.export_path = design, joint, path
                    self.record_design_run(project,settings,'complete',design=design,archive=path)
                    self.saved_design_settings=saved
                    self.saved_controls_restored=False
                    self.job = {"status": "complete", "progress": 100, "message": 'Design and export complete'}
                self.log('Completed channel designs, available candidate encoding and reports. Nothing has been written to the receiver.')
            except InterruptedError as error:
                self.record_design_run(project,settings,'cancelled',error=error)
                self.job={"status":"cancelled","kind":"design","progress":0,"message":str(error)}
                self.log(str(error))
            except Exception as error:
                self.record_design_run(project,settings,'failed',error=error)
                self.job = {"status": "failed", "kind":"design", "progress": 0, "message": str(error)}
                self.log('Design incomplete: ' + str(error))
        threading.Thread(target=run, daemon=True).start()
        return dict(self.job)

    def record_design_run(self,project,settings,status,**details):
        try:
            write_receipt(self.home,project,settings,status,**details)
        except (ValueError,TypeError,OSError,RecursionError):
            self.log('The local run receipt could not be saved. Existing results and exports are retained.')

    def start_receiver_audit(self,body):
        from .receiver_audit import probe_receiver
        host=body.get('host') or (self.receiver.get('host') if self.receiver else None)
        # Validate without opening a connection before reserving the diagnostic job.
        ReceiverClient(host)
        with self.lock:
            if self.job.get('status')=='running':raise ValueError('Wait for the active job before probing receiver capabilities')
            self.job={'status':'running','kind':'receiver_audit','progress':0,'message':'Reading receiver capabilities; no settings or audio operations'}
        def run():
            try:
                report=probe_receiver(host,progress=lambda n,m:self.job.update(progress=n,message=m))
                archive=self.home/'diagnostics';archive.mkdir(exist_ok=True)
                save_probe_report(archive/('receiver-'+report['evidence_sha256']+'.json'),report)
                path=self.home/'receiver-audit.json';report=save_probe_report(path,report)
                with self.lock:
                    self.receiver_audit=report;self.receiver_audit_path=path
                    self.job={'status':'complete','kind':'receiver_audit','progress':100,'message':'Read-only receiver evidence saved; signal processing remains unverified'}
                self.log('Read-only audit complete. Capture selection, measurement references, receiver settings and filters were not changed.')
            except Exception as error:
                self.job={'status':'failed','kind':'receiver_audit','progress':0,'message':str(error)}
                self.log('Receiver audit incomplete; the previous saved report is retained: '+str(error))
        threading.Thread(target=run,daemon=True).start()
        return dict(self.job)

    def start_input_level(self,body):
        if body.get('ready') is not True:raise ValueError('Explicitly allow this five-second local microphone level check')
        if body.get('seconds',5)!=5:raise ValueError('The local level check is fixed at five seconds')
        from .audio_input import check_input_level
        with self.lock:
            if self.job.get('status')=='running':raise ValueError('Wait for the active job before accessing a recording input')
            self.job={'status':'running','kind':'input_level','progress':0,'message':'Checking input level for five seconds; no audio file or playback'}
        def run():
            try:
                report=save_probe_report(self.home/'input-level.json',check_input_level(body.get('device_index')))
                with self.lock:
                    self.input_level=report
                    self.job={'status':'complete','kind':'input_level','progress':100,'message':'Input-level statistics saved; audio samples discarded'}
            except Exception as error:
                self.job={'status':'failed','kind':'input_level','progress':0,'message':str(error)}
                self.log('Input-level check incomplete: '+str(error))
        threading.Thread(target=run,daemon=True).start()
        return dict(self.job)

    def cancel_design(self):
        with self.lock:
            if self.job.get("status")!="running" or self.job.get("kind")!="design":
                raise ValueError("No filter-design job is running")
            self.design_cancel.set()
        return {"cancel_requested":True}

    def create_clock_reference(self, body):
        from .recording_workflow import clock_reference
        path, report = clock_reference(self.home, body.get('marker_times_s', [.5, 2., 3.5]), int(body.get('sample_rate', 48000)))
        self.clock_reference_path = path
        return {'report': report, 'download_url': '/download-clock-reference'}

    def start_clock_correction(self, body):
        with self.lock:
            if self.job['status'] == 'running': raise ValueError('Wait for the current calculation before correcting a recording')
            self.clock_cancel = threading.Event()
            self.clock_result = None; self.clock_archive = None
            self.job = {'status': 'running', 'kind': 'clock', 'progress': 0, 'message': 'Preparing original WAV clock analysis'}
        def progress(number, message): self.job.update(progress=number, message=message)
        def run():
            try:
                from .recording_workflow import correct_clock_files
                path, report = correct_clock_files(self.home, body, self.clock_cancel, progress)
                with self.lock:
                    if self.clock_cancel.is_set(): raise InterruptedError('Raw recording correction cancelled')
                    self.clock_archive = path; self.clock_result = report
                    self.job = {'status': 'complete', 'kind': 'clock', 'progress': 100, 'message': 'Separate clock-corrected raw recording and timing map ready'}
                self.log('Original recording retained. Clock correction did not play sound, modify a measurement project or contact a receiver.')
            except InterruptedError as error:
                self.job = {'status': 'cancelled', 'kind': 'clock', 'progress': 0, 'message': str(error)}
            except Exception as error:
                self.job = {'status': 'failed', 'kind': 'clock', 'progress': 0, 'message': str(error)}
        threading.Thread(target=run, daemon=True).start()
        return dict(self.job)

    def set_curve(self, body):
        if self.job["status"] == "running":
            raise ValueError('Wait for the current job before changing the target curve')
        curve_name = body.get('curve_name', 'Flat')
        if curve_name not in ['Flat', 'Reference']:
            raise ValueError('Choose Flat or Reference for target-curve import')
        attribute, filename = ('target_curve','target-curve.json') if curve_name=='Flat' else ('reference_target_curve','reference-target-curve.json')
        if body.get("reset") is True:
            curve = None
        else:
            raw = base64.b64decode(body["data"], validate=True)
            if len(raw) > 1024 * 1024:
                raise ValueError('The target curve file is too large')
            curve = parse_target_curve(raw.decode("utf-8-sig"))
            curve["name"] = Path(body["filename"]).name
        setattr(self, attribute, curve)
        self.design = None
        self.joint = None
        self.export_path = None
        self.transfer_plan = None
        (self.home / filename).write_text(json.dumps(curve, ensure_ascii=False), encoding="utf-8")
        return self.state()[attribute]

    def start_native_measurement(self, body):
        if not self.receiver or not self.receiver.get("connected"):
            raise ValueError('Connect the receiver on the Project and devices page first')
        if body.get("ready") is not True:
            raise ValueError('Confirm that the stock microphone is connected to the AVR and that calibration test tones may play')
        position, repeats = body.get("position", 1), body.get("repeats", 1)
        from .native_measure import select_capture_outputs
        selected_outputs=select_capture_outputs(self.receiver["status"],body.get("channels") if body.get('channels') is not None else self.receiver.get('capture_channels'))
        selected_channels=[ALIASES.get(name,name) for name in selected_outputs]
        position_note=str(body.get('position_note','')).strip()
        if len(position_note)>200:raise ValueError('Keep the microphone position note within 200 characters')
        if isinstance(position,bool) or not isinstance(position,int) or isinstance(repeats,bool) or not isinstance(repeats,int) or not 1 <= position <= 20 or not 1 <= repeats <= 5:
            raise ValueError('Position must be 1-20 and repeats must be 1-5')
        with self.lock:
            if self.job["status"] == "running":
                raise ValueError('Wait for or stop the current job first')
            host = self.receiver["host"]
            existing = self.project if self.project and self.project.origin == "native_avr" and self.project.metadata.get("receiver_host") == host and not body.get("new_project") else None
            if existing is not None and not isinstance(existing.metadata.get('capture_runs',[]),list):
                raise ValueError('The saved capture-run history needs review before new acquisition')
            request=self.preview_native_measurement(body,existing=existing,use_current=False,channels=selected_channels)
            if body.get('preview_sha256') is not None and body['preview_sha256']!=request['preview_sha256']:
                raise ValueError('The capture preview changed; review its missing outputs/repeats again before starting')
            if not request['responses_to_capture']:
                raise ValueError('This position already meets the requested repeat coverage; no test tones were requested')
            identity=request['position']
            run_id=secrets.token_hex(12)
            run_record={'schema':'audioseyx-capture-run-1','id':run_id,'position':identity,'started_ns':time.time_ns(),
                        'request':copy.deepcopy(request),'status':'running','responses_saved':0,'receiver_filters_written':False}
            capture_directory=self.home/'captures';capture_directory.mkdir(exist_ok=True)
            checkpoint_path=capture_directory/(run_id+'.awb')
            receipt_path=capture_directory/(run_id+'.json')
            # Commit the intended scope before any receiver refresh or test tone.
            receipt_path.write_text(json.dumps(run_record,allow_nan=False),encoding='utf-8')
            self.capture_cancel = threading.Event()
            self.job = {"status": "running", "kind": "native_measure", "capture_run_id":run_id,"progress": 0, "message": 'Checking receiver and stock microphone'}
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
                    if position_note:measurement.metadata['position_note']=position_note
                    measurement.metadata.update(capture_run_id=run_id,capture_position_number=position,
                        capture_preview_sha256=request['preview_sha256'],capture_mode=request['mode'],
                        capture_session_id=run_id+':'+str(batch_index),capture_batch_expected_channels=list(batch['channels']))
                    measurement.position=identity
                    rows.append(measurement)
                    run_record['responses_saved']=len(rows)
                    snapshot = Project('Stock microphone capture checkpoint', rows.copy(), "native_avr",
                                       metadata={"receiver_host": host, "partial": True,'capture_run':run_record})
                    save_archive(snapshot, checkpoint_path)
                completed=0
                for batch_index,batch in enumerate(request['batches']):
                    if self.capture_cancel.is_set():raise InterruptedError('Capture cancelled before the next response batch')
                    size=len(batch['channels'])*batch['repeats']
                    def batch_progress(number,message):
                        progress(round((completed+number/100*size)/request['responses_to_capture']*95),message)
                    capture_position(fresh,position,batch['repeats'],self.capture_cancel,batch_progress,checkpoint,channels=batch['channels'])
                    completed+=size
            except Exception as error:
                failure = str(error)
            if rows:
                project = Project(existing.name,list(existing.measurements),existing.origin,existing.model,existing.tier,
                                  copy.deepcopy(existing.metadata),list(existing.warnings),id=existing.id) if existing else Project('AVR stock microphone measurements', [], "native_avr",
                                              value(fresh["info"], "Model", "ModelName", "targetModelName"),
                                              identify_tier(value(fresh["info"], "EqType", "enMultEQType")),
                                              {"receiver_host": host, "receiver_snapshot": fresh},
                                              ['The receiver sampled these responses and transferred them over the LAN. Original responses are retained without peak alignment.',
                                               'Model-specific microphone calibration and timing still need hardware validation. Mixed phase is not enabled automatically.'])
                project.measurements.extend(rows)
                if position_note:project.metadata.setdefault('position_notes',{})[identity]=position_note
                project.metadata["partial"] = failure is not None
                run_record.update(status='cancelled' if self.capture_cancel.is_set() else 'partial' if failure else 'complete',
                                  finished_ns=time.time_ns(),responses_saved=len(rows),error=failure)
                project.metadata.setdefault('capture_runs',[]).append(copy.deepcopy(run_record))
                try:
                    with self.lock:
                        self.set_project(project,_capture_run_id=run_id)
                    final_job = {"status": "failed" if failure else "complete", "kind": "native_measure", "progress": 0 if failure else 100,
                                 "message": ('Saved partial responses: ' + failure) if failure else f"Capture at {identity} completed; saved {len(rows)} raw responses"}
                    self.log('Stock microphone data saved. End calibration mode and unplug the microphone before transfer.')
                except (OSError,ValueError,TypeError) as error:
                    run_record.update(status='save_failed',error=str(error))
                    final_job={'status':'failed','kind':'native_measure','progress':0,'message':'Project save failed; the per-run checkpoint is retained: '+str(error)}
            else:
                run_record.update(status='cancelled' if self.capture_cancel.is_set() else 'failed',finished_ns=time.time_ns(),responses_saved=0,error=failure)
                final_job = {"status": "failed", "kind": "native_measure", "progress": 0, "message": failure or 'No valid measurement was received'}
                self.log('Stock microphone capture incomplete: ' + final_job["message"])
            try:
                temporary=receipt_path.with_suffix('.tmp')
                temporary.write_text(json.dumps(run_record,allow_nan=False),encoding='utf-8')
                temporary.replace(receipt_path)
            except (OSError,ValueError,TypeError):self.log('Capture status could not be committed; retained response checkpoints were not removed.')
            with self.lock:self.job=final_job
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
        self.design['settings']['apply_receiver_adjustments'] = body.get('apply_receiver_adjustments') is True
        self.design['settings']['initial_playback_curve'] = body.get('initial_curve', 'Flat')
        self.receiver = ReceiverClient(self.receiver["host"]).read_info(include_settings=self.design['settings']['apply_receiver_adjustments'])
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
            fresh = ReceiverClient(host).read_info(include_settings=bool(self.design['settings'].get('apply_receiver_adjustments')))
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
        project = self.project
        filename = Path(str(body.get("filename", "after.json"))).name
        directory = self.home / "uploads" / secrets.token_hex(4)
        directory.mkdir()
        path = directory / filename
        raw = base64.b64decode(body["data"], validate=True)
        if not raw or len(raw) > 128 * 1024 * 1024:
            raise ValueError('The verification file is empty or exceeds the size limit')
        path.write_bytes(raw)
        after = import_file(path, "verification_file")
        result = compare_projects(project, after, temporal_memory_mib=float(body.get('temporal_memory_mib', 128)))
        if self.project is not project:
            raise ValueError('The project changed during comparison; compare again')
        report_path = self.home/'exports'/(project.id+'-verification-'+secrets.token_hex(4)+'.json')
        report_path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        self.verification_path = report_path
        return result

    def export_capability_assessment(self):
        project = self.project
        result = capability_assessment(project)
        report_path = self.home/'exports'/('capabilities-'+secrets.token_hex(4)+'.json')
        report_path.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2), encoding='utf-8')
        if self.project is not project:
            raise ValueError('The project changed during assessment; generate the report again')
        self.capability_path = report_path
        return result

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

    def analyze_polarity(self,body):
        from .polarity import analyze_polarity
        if not self.project:raise ValueError("Import or capture original responses first")
        if self.job['status']=='running':raise ValueError("Wait for the current operation before checking polarity")
        if body.get('same_models_confirmed') is not True:raise ValueError("Confirm that each comparison group uses the same speaker model")
        project=self.project
        report=analyze_polarity(project,body.get('groups',[]))
        self.polarity_report=report
        self.polarity_path=self.home/'exports'/(project.id+'-polarity-'+secrets.token_hex(4)+'.json')
        self.polarity_path.write_text(json.dumps(report,allow_nan=False,indent=2),encoding='utf-8')
        return report


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
    checks.add_argument('--listening-area-benchmark',action='store_true',help='Validate a synthetic three-seat objective without contacting a receiver')
    checks.add_argument('--native-workflow-benchmark',action='store_true',help='Validate synthetic full-layout native-menu planning and known raw clock drift without playback')
    parser.add_argument("--benchmark-iterations",type=int,default=60)
    parser.add_argument("--report", default=None)
    args = parser.parse_args()
    if args.compat_dir:
        os.environ["AUDIOSEYX_COMPAT_DIR"] = str(Path(args.compat_dir).expanduser().resolve())
    if args.compat_status:
        print(json.dumps(compatibility_status(), ensure_ascii=False, indent=2), flush=True)
        return
    if args.self_test or args.joint_benchmark or args.listening_area_benchmark or args.native_workflow_benchmark:
        if args.native_workflow_benchmark:
            from .native_workflow_benchmarks import run_native_workflow_benchmark
            report=run_native_workflow_benchmark(lambda n,m:print(f'{n}% {m}',file=sys.stderr,flush=True))
        elif args.listening_area_benchmark:
            from .area_benchmarks import run_listening_area_benchmark
            report=run_listening_area_benchmark(args.benchmark_iterations,lambda number,message:print(f'{number}% {message}',file=sys.stderr,flush=True))
        elif args.joint_benchmark:
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
