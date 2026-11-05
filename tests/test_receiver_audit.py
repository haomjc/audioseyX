import copy
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import numpy as np

from audioseyx.app import Workbench
from audioseyx.audio_input import summarize_levels,select_input
from audioseyx.receiver_audit import build_audit,parse_menu,probe_receiver,public_audit,save_probe_report,load_probe_report
from audioseyx.receiver_web import read_speaker_settings


MENU='<listSetupMenu><SpeakerConfig>3</SpeakerConfig><Crossovers>3</Crossovers><Bass>3</Bass><Distances>3</Distances><Levels>3</Levels><DiracLiveSetup>1</DiracLiveSetup><SubwooferOutput>1</SubwooferOutput><LFEDistribution>1</LFEDistribution></listSetupMenu>'
def snapshot():
    return dict(host='127.0.0.1',connected=True,read_only=True,
        info=dict(Model='AVR-TEST',Ifver='00.08',EQType='MultEQXT32',DType='Float',HEOSFirmwareVersion='test-component',LFC=True),
        status={'ChSetup':[{'FL':'S'},{'SWMIX1':'E'},{'SWMIX2':'E'}]},
        web_settings=dict(configured_channels=['FL','SW1','SW2'],distances_m={'SW1':2.4,'SW2':2.7},levels_db={'SW1':-2.,'SW2':-3.},
            raw_xml={'distances':'<Distances><SubwooferType><MaxNumber>2</MaxNumber></SubwooferType><M2FConvertRatio>3000</M2FConvertRatio><Step>1</Step></Distances>'}))


class Response:
    def __enter__(self):return self
    def __exit__(self,*_):pass
    def read(self,*_):return MENU.encode()


class ReceiverAuditTests(unittest.TestCase):
    def test_menu_evidence_does_not_establish_electrical_or_filter_independence(self):
        source=snapshot();report=build_audit(source,MENU)
        source['web_settings']['distances_m']['SW1']=99
        self.assertEqual(report['raw_evidence']['native']['web_settings']['distances_m']['SW1'],2.4)
        states={c['id']:c['status'] for c in report['capabilities']}
        self.assertEqual(states['sub_parameters'],'observed_read_only')
        self.assertEqual(states['dirac_menu'],'not_available')
        self.assertEqual(states['lfe_distribution'],'not_available')
        self.assertEqual(states['independent_sub_eq'],'not_verified')
        self.assertEqual(states['support_matrix'],'not_exposed_by_this_probe')
        self.assertEqual(report['subwoofer_maximum_reported'],2)
        self.assertFalse(report['distance_display_metadata']['electrical_delay_conversion_verified'])
        self.assertFalse(report['native_playback_verified']);self.assertFalse(report['settings_written']);self.assertFalse(report['sound_played'])
        self.assertNotIn('raw_evidence',public_audit(report))

    def test_probe_reads_menu_before_exposed_controls_and_never_queries_hidden_features(self):
        native=snapshot();native.pop('web_settings')
        with patch('audioseyx.receiver_audit.ReceiverClient') as client,patch('audioseyx.receiver_audit.build_opener') as opener,patch('audioseyx.receiver_audit.read_speaker_settings') as settings:
            client.return_value.host='127.0.0.1';client.return_value.web_url='https://127.0.0.1:10443'
            client.return_value.read_info.return_value=native
            opener.return_value.open.return_value=Response();settings.return_value=snapshot()['web_settings']
            report=probe_receiver('127.0.0.1')
        client.return_value.read_info.assert_called_once_with(include_settings=False)
        urls=[call.args[0] for call in opener.return_value.open.call_args_list]
        self.assertEqual(urls,['https://127.0.0.1:10443/ajax/speakers/get_config?type=1'])
        self.assertTrue(settings.call_args.kwargs['stop_on_error'])
        self.assertEqual(set(settings.call_args.kwargs['sections']),{'configuration','crossovers','bass','distances','levels'})
        self.assertFalse(report['original_EQ_backup_acquired'])

    def test_timeout_remains_unknown_and_stops_optional_settings_reads(self):
        native=snapshot();native.pop('web_settings')
        with patch('audioseyx.receiver_audit.ReceiverClient') as client,patch('audioseyx.receiver_audit.build_opener') as opener,patch('audioseyx.receiver_audit.read_speaker_settings') as settings:
            client.return_value.host='127.0.0.1';client.return_value.web_url=None;client.return_value.read_info.return_value=native
            opener.return_value.open.side_effect=TimeoutError('Synthetic menu timeout')
            report=probe_receiver('127.0.0.1');settings.assert_not_called()
        self.assertEqual(next(c['status'] for c in report['capabilities'] if c['id']=='dirac_menu'),'unknown')
        self.assertIn('timeout',report['menu_error'])

    def test_settings_reader_stops_after_first_failure_in_audit_mode(self):
        with patch('audioseyx.receiver_web.build_opener') as opener:
            opener.return_value.open.side_effect=TimeoutError('Synthetic transport timeout')
            report=read_speaker_settings('127.0.0.1',snapshot()['status'],timeout=.5,stop_on_error=True)
        self.assertEqual(opener.return_value.open.call_count,1)
        self.assertEqual(len(report['errors']),5);self.assertFalse(report['available'])

    def test_corrupt_report_or_wrong_menu_root_is_rejected(self):
        with self.assertRaises(ValueError):parse_menu('<Other/>')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'audit.json';report=save_probe_report(path,build_audit(snapshot(),MENU))
            self.assertEqual(load_probe_report(path,report['schema']),report)
            tampered=json.loads(path.read_text());tampered['native_playback_verified']=True;path.write_text(json.dumps(tampered))
            with self.assertRaisesRegex(ValueError,'integrity'):load_probe_report(path,report['schema'])
            self.assertEqual(list(Path(directory).glob('*.tmp')),[])


def wait(bench):
    deadline=time.monotonic()+10
    while bench.job['status']=='running' and time.monotonic()<deadline:time.sleep(.01)
    if bench.job['status']=='running':raise AssertionError('Diagnostic job did not finish')


class LocalLevelTests(unittest.TestCase):
    def test_numeric_statistics_have_no_audio_payload_or_calibration_claim(self):
        values=np.tile([0.,.5,-.5,1.],1000)
        result=summarize_levels(values,8000)
        self.assertEqual(result['seconds'],.5);self.assertEqual(result['peak_dbfs'],0.)
        self.assertEqual(result['clipped_samples'],1000)
        self.assertAlmostEqual(result['rms_dbfs'],20*np.log10(np.sqrt(np.mean(values**2))))
        self.assertFalse(result['audio_file_saved']);self.assertFalse(result['microphone_calibrated'])
        self.assertNotIn('samples',result)
        with self.assertRaises(ValueError):summarize_levels(np.zeros(40001),8000)
        with self.assertRaises(ValueError):summarize_levels([float('nan')],8000)

    def test_input_selection_does_not_silently_choose_between_microphones(self):
        devices=[{'index':0,'name':'Microphone A'},{'index':1,'name':'Microphone B'},{'index':2,'name':'Stereo mix'}]
        with self.assertRaises(ValueError):select_input(devices)
        self.assertEqual(select_input(devices,1),devices[1])
        with self.assertRaises(ValueError):select_input(devices,True)

    def test_level_access_requires_fresh_permission_and_persists_statistics_only(self):
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(Path(directory))
            with patch('audioseyx.audio_input.check_input_level') as reader:
                with self.assertRaises(ValueError):bench.start_input_level({'ready':False})
                with self.assertRaises(ValueError):bench.start_input_level({'ready':True,'seconds':30})
                reader.assert_not_called()
                reader.return_value={**summarize_levels(np.zeros(8000),8000),'device':{'name':'Synthetic input'}}
                bench.start_input_level({'ready':True});wait(bench)
            self.assertEqual(bench.job['status'],'complete')
            self.assertFalse(bench.input_level['audio_file_saved'])
            self.assertEqual(list(Path(directory).rglob('*.wav')),[])
            with patch('audioseyx.audio_input.check_input_level',side_effect=AssertionError('Restore must not capture')):
                restored=Workbench(Path(directory))
            self.assertEqual(restored.input_level,bench.input_level);self.assertEqual(restored.job['status'],'idle')

    def test_audit_job_retains_previous_report_on_failed_read_and_never_selects_receiver(self):
        with tempfile.TemporaryDirectory() as directory:
            bench=Workbench(Path(directory));previous=build_audit(snapshot(),MENU)
            with patch('audioseyx.receiver_audit.probe_receiver',return_value=previous):
                bench.start_receiver_audit({'host':'127.0.0.1'});wait(bench)
            self.assertIsNone(bench.receiver);self.assertIsNotNone(bench.receiver_audit_path)
            self.assertEqual(len(list((Path(directory)/'diagnostics').glob('receiver-*.json'))),1)
            report=bench.receiver_audit
            with patch('audioseyx.receiver_audit.probe_receiver',side_effect=OSError('Synthetic disconnected device')):
                bench.start_receiver_audit({'host':'127.0.0.1'});wait(bench)
            self.assertEqual(bench.job['status'],'failed');self.assertIs(bench.receiver_audit,report)


if __name__=='__main__':unittest.main()
