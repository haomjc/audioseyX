"""Evidence from bounded receiver reads, never a claim of measured DSP behavior."""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import hashlib
import json
import math
from pathlib import Path
import secrets
import ssl
from urllib.request import build_opener, ProxyHandler, HTTPSHandler
import xml.etree.ElementTree as ET

from .receiver import ReceiverClient
from .receiver_web import NoRedirect, read_speaker_settings
from .receiver_constraints import snapshot_fingerprint

MENU_STATES={'0':'undefined','1':'not_available','2':'grayed_out','3':'selectable','4':'title'}
SETTINGS_MENUS={'configuration':'SpeakerConfig','crossovers':'Crossovers','bass':'Bass','distances':'Distances','levels':'Levels'}


def parse_menu(raw):
    root=ET.fromstring(raw)
    if root.tag!='listSetupMenu':raise ValueError('Unexpected receiver speaker-menu root')
    result={}
    for item in root:
        if item.tag in result:raise ValueError('Duplicate receiver menu item')
        text=(item.text or '').strip()
        result[item.tag]={'raw':text,'state':MENU_STATES.get(text,'unknown'),'attributes':dict(item.attrib)}
    return result


def build_audit(snapshot,menu_xml=None,menu_error=None):
    if snapshot.get('connected') is not True:raise ValueError('No successful native information read is available')
    snapshot=copy.deepcopy(snapshot)
    info=snapshot.get('info',{});settings=snapshot.get('web_settings',{});menus={}
    if menu_xml is not None:
        try:menus=parse_menu(menu_xml)
        except (ET.ParseError,ValueError) as error:menu_error=str(error)
    subs=[c for c in settings.get('configured_channels',[]) if c.startswith('SW')]
    values={c:{'distance_m':settings.get('distances_m',{}).get(c),'level_db':settings.get('levels_db',{}).get(c)} for c in subs}
    individual=len(subs)>=2 and all(v['distance_m'] is not None and v['level_db'] is not None for v in values.values())
    raw=settings.get('raw_xml',{});maximum=None;ratio=None;step=None
    try:
        root=ET.fromstring(raw.get('distances','<Distances/>'))
        text=root.findtext('SubwooferType/MaxNumber')
        maximum=int(text) if text is not None else None
        ratio=root.findtext('M2FConvertRatio');step=root.findtext('Step')
    except (ET.ParseError,ValueError):pass
    def menu_state(*names):
        states=[menus[n]['state'] for n in names if n in menus]
        if 'selectable' in states:return 'selectable'
        if 'grayed_out' in states:return 'grayed_out'
        return 'not_available' if states and all(s=='not_available' for s in states) else 'unknown'
    capabilities=[
        dict(id='native_information',name='Native identity and format',status='observed_read_only',
             detail=f"{info.get('Model','Unidentified model')} / {info.get('EQType',info.get('EqType','unknown EQ'))} / {info.get('DType','unknown format')}"),
        dict(id='sub_parameters',name='Separate sub level/distance entries',status='observed_read_only' if individual else 'not_verified',
             detail='Separate stored menu values were read; independent electrical gain/delay behavior was not measured.'),
        dict(id='dirac_menu',name='Dirac setup menu',status=menu_state('DiracLiveSetup','DiracLiveSetupRC'),
             detail='Actual menu flags only; shared frontend strings do not establish an enabled processor or license.'),
        dict(id='sub_assignment',name='Subwoofer output assignment menu',status=menu_state('SubwooferOutput'),detail='Menu exposure does not establish independently writable EQ.'),
        dict(id='lfe_distribution',name='LFE distribution menu',status=menu_state('LFEDistribution'),detail='Fixed LFE routing, if exposed, is not an arbitrary ART support matrix.'),
        dict(id='independent_sub_eq',name='Independent sub EQ during playback',status='not_verified',detail='Requires a recoverable coefficient baseline and normal-playback channel measurements.'),
        dict(id='mixed_phase',name='Native mixed-phase reconstruction',status='not_verified',detail='Requires a verified native synthesis and measured magnitude/phase through normal playback.'),
        dict(id='support_matrix',name='Arbitrary speaker-support filter matrix',status='not_exposed_by_this_probe',detail='No matrix controls are exposed by the inspected read interfaces; DSP silicon capability is not determined.'),
    ]
    report=dict(schema='audioseyx-receiver-audit-1',observed_at_utc=datetime.now(timezone.utc).isoformat(),
        host=snapshot['host'],model=info.get('Model'),interface_version=info.get('Ifver'),
        heos_component_version=info.get('HEOSFirmwareVersion'),full_firmware_version_verified=False,
        subwoofer_maximum_reported=maximum,configured_subwoofer_outputs=subs,sub_parameters=values,
        menu_evidence=menus,menu_error=menu_error,capabilities=capabilities,
        reported_flags={k:info.get(k) for k in ('SWLvlMatch','LFC','Auro','Upgrade')},
        distance_display_metadata={'step_raw':step,'meter_to_feet_ratio_raw':ratio,'electrical_delay_conversion_verified':False},
        conservative_sub_eq_model='shared_EQ_until_independent_playback_is_verified' if len(subs)>1 else 'single_or_unestablished_sub_output',
        settings_snapshot_sha256=snapshot_fingerprint(settings),read_only=True,settings_written=False,
        sound_played=False,calibration_mode_entered=False,original_EQ_backup_acquired=False,
        native_playback_verified=False,independent_subwoofer_filters_verified=False,
        raw_evidence={'native':snapshot,'speaker_menu_xml':menu_xml},
        notice='Read-only evidence is separate from software features and measured signal behavior. Timeouts and absent menu entries do not prove unsupported silicon. Meter/feet display conversion does not establish the DSP distance/delay law.')
    report['evidence_sha256']=hashlib.sha256(json.dumps(report,sort_keys=True,allow_nan=False).encode()).hexdigest()
    return report


def probe_receiver(host,timeout=2.5,progress=None):
    if isinstance(timeout,bool) or not math.isfinite(timeout) or not .5<=timeout<=5:raise ValueError('Invalid read-only probe timeout')
    receiver=ReceiverClient(host,timeout=timeout)
    if progress:progress(10,'Reading native identity and status only')
    snapshot=receiver.read_info(include_settings=False)
    if not snapshot.get('connected'):raise ValueError(snapshot.get('error','Receiver information read failed'))
    origin=receiver.web_url or f"https://{'['+receiver.host+']' if ':' in receiver.host else receiver.host}:10443"
    client=build_opener(ProxyHandler({}),HTTPSHandler(context=ssl._create_unverified_context()),NoRedirect)
    raw=None;error=None
    try:
        if progress:progress(45,'Reading the actual speaker menu before selecting settings queries')
        with client.open(origin+'/ajax/speakers/get_config?type=1',timeout=timeout) as response:
            payload=response.read(262145)
        if len(payload)>262144:raise ValueError('The receiver menu exceeds the size limit')
        raw=payload.decode('utf-8');menus=parse_menu(raw)
        sections=[key for key,name in SETTINGS_MENUS.items() if menus.get(name,{}).get('state') in ('selectable','grayed_out')]
        if progress:progress(65,'Reading exposed speaker controls; stop after the first failure')
        snapshot['web_settings']=read_speaker_settings(receiver.host,snapshot['status'],origin,timeout,sections=sections,stop_on_error=True)
    except (OSError,ValueError,ET.ParseError) as exc:
        error=str(exc)
    if progress:progress(90,'Separating observed menu evidence from unverified DSP behavior')
    return build_audit(snapshot,raw,error)


def public_audit(report):
    return None if report is None else {k:v for k,v in report.items() if k!='raw_evidence'}


def save_probe_report(path,report):
    path=Path(path);result={k:v for k,v in report.items() if k!='evidence_sha256'}
    result['evidence_sha256']=hashlib.sha256(json.dumps(result,sort_keys=True,allow_nan=False).encode()).hexdigest()
    raw=json.dumps(result,ensure_ascii=False,allow_nan=False,indent=2)
    if len(raw.encode())>4*1024**2:raise ValueError('The probe report exceeds the size limit')
    temporary=path.with_name(path.name+'.'+secrets.token_hex(6)+'.tmp')
    try:
        temporary.write_text(raw,encoding='utf-8');temporary.replace(path)
    finally:temporary.unlink(missing_ok=True)
    return result


def load_probe_report(path,schema):
    path=Path(path)
    if not path.is_file():return None
    if path.stat().st_size>4*1024**2:raise ValueError('The probe report exceeds the size limit')
    def reject(value):raise ValueError('Non-finite probe report')
    data=json.loads(path.read_text(encoding='utf-8'),parse_constant=reject)
    if not isinstance(data,dict) or data.get('schema')!=schema:raise ValueError('Unknown probe report schema')
    expected=data.get('evidence_sha256')
    payload={k:v for k,v in data.items() if k!='evidence_sha256'}
    actual=hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
    if expected!=actual:raise ValueError('The saved probe report failed its integrity check')
    return data
